"""DynamoDB application patterns lab: a single-table key design with a sparse GSI, batch writes with retries,
conditional writes, optimistic locking with a version attribute, a transfer with TransactWriteItems and an idempotency
token, strongly consistent reads, query vs scan with paging, and PartiQL.

Floci 2.3.0 behaves like DynamoDB for every pattern here: condition expressions, ReturnValuesOnConditionCheckFailure,
transactions (cancellation reasons, idempotent client tokens, IdempotentParameterMismatch), the 25-item batch limit,
the rule against consistent reads on a GSI, GSI projections, paging with LastEvaluatedKey, reserved words, consumed
capacity, and PartiQL. What it does not do: reads are never stale (one node, so an eventually consistent read always
sees the latest write) and nothing is ever throttled, so a batch never comes back with unprocessed items.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'dynamodb'
LAB_KEY = 'application-patterns'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'

TABLE = 'lab-ddbapp-store'
GSI = 'GSI1'
GSI_PROJECTED = ['total']
CUSTOMER = 'CUST#c-100'
ORDER_COUNT = 40
OPEN_EVERY = 4  # every 4th order is open, so the sparse index holds 10
BATCH_LIMIT = 25
PAGE_SIZE = 10
BIG_ORDER_TOTAL = 300

PROFILE_SK = 'PROFILE'
INVENTORY = ('PRODUCT#p-1', 'STOCK')
START_STOCK, RESERVE_OK, RESERVE_TOO_MANY = 20, 3, 50
PRODUCT = ('PRODUCT#p-1', 'DETAILS')
START_PRICE, PRICE_A, PRICE_B = 1999, 1799, 2499
ALICE, BOB = ('ACCT#alice', 'ACCOUNT'), ('ACCT#bob', 'ACCOUNT')
START_BALANCE, TRANSFER_AMOUNT, OVERDRAFT_AMOUNT = 100, 25, 500
TRANSFER_ID, OVERDRAFT_ID = 'TRANSFER#t-0001', 'TRANSFER#t-0002'
NOTE = (CUSTOMER, 'NOTE#n-1')

KEY_DESIGN_COMMAND = (
    f'aws dynamodb create-table --table-name {TABLE} --billing-mode PAY_PER_REQUEST \\\n'
    '  --attribute-definitions AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S '
    'AttributeName=GSI1PK,AttributeType=S AttributeName=GSI1SK,AttributeType=S \\\n'
    '  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \\\n'
    f'  --global-secondary-indexes \'IndexName={GSI},KeySchema=[{{AttributeName=GSI1PK,KeyType=HASH}},'
    '{AttributeName=GSI1SK,KeyType=RANGE}],Projection={ProjectionType=INCLUDE,NonKeyAttributes=[total]}\''
)
BATCH_COMMAND = (
    'aws dynamodb batch-write-item --request-items file://orders-001-025.json --return-consumed-capacity TOTAL\n'
    '# resend UnprocessedItems with exponential backoff until it comes back empty\n'
    'aws dynamodb batch-write-item --request-items file://orders-026-040.json'
)
CONDITIONAL_COMMAND = (
    f'aws dynamodb put-item --table-name {TABLE} --item file://profile.json '
    '--condition-expression "attribute_not_exists(PK)" --return-values-on-condition-check-failure ALL_OLD\n'
    f'aws dynamodb update-item --table-name {TABLE} --key \'{{"PK": {{"S": "{INVENTORY[0]}"}}, "SK": {{"S": "{INVENTORY[1]}"}}}}\' '
    '--update-expression "SET stock = stock - :q" --condition-expression "stock >= :q" '
    f'--expression-attribute-values \'{{":q": {{"N": "{RESERVE_OK}"}}}}\''
)
LOCKING_COMMAND = (
    f'aws dynamodb get-item --table-name {TABLE} --key file://product-key.json   # both writers read version 1\n'
    f'aws dynamodb update-item --table-name {TABLE} --key file://product-key.json '
    '--update-expression "SET price = :p, version = version + :one" --condition-expression "version = :v" '
    f'--expression-attribute-values \'{{":p": {{"N": "{PRICE_A}"}}, ":v": {{"N": "1"}}, ":one": {{"N": "1"}}}}\''
)
TRANSACTION_COMMAND = (
    'aws dynamodb transact-write-items --client-request-token <uuid> --transact-items file://transfer.json '
    '--return-consumed-capacity TOTAL\n'
    '# transfer.json: Update alice (balance >= :a), Update bob, Put the transfer record (attribute_not_exists)\n'
    'aws dynamodb transact-get-items --transact-items file://read-accounts.json'
)
CONSISTENCY_COMMAND = (
    f'aws dynamodb get-item --table-name {TABLE} --key file://order-key.json --return-consumed-capacity TOTAL\n'
    f'aws dynamodb get-item --table-name {TABLE} --key file://order-key.json --consistent-read --return-consumed-capacity TOTAL\n'
    f'aws dynamodb query --table-name {TABLE} --index-name {GSI} --key-condition-expression "GSI1PK = :s" --consistent-read ...'
)
QUERY_COMMAND = (
    f'aws dynamodb query --table-name {TABLE} --key-condition-expression "PK = :c AND begins_with(SK, :o)" '
    f'--limit {PAGE_SIZE} [--exclusive-start-key <LastEvaluatedKey>]\n'
    f'aws dynamodb query ... --filter-expression "#t > :big" --expression-attribute-names \'{{"#t": "total"}}\'\n'
    f'aws dynamodb scan --table-name {TABLE} --filter-expression "#t > :big" ...\n'
    f'aws dynamodb query --table-name {TABLE} --index-name {GSI} --key-condition-expression "GSI1PK = :open"'
)
PARTIQL_COMMAND = (
    f'aws dynamodb execute-statement --statement "SELECT SK, total FROM \\"{TABLE}\\" WHERE PK = ? AND begins_with(SK, ?)" '
    f'--parameters \'[{{"S": "{CUSTOMER}"}}, {{"S": "ORDER#00"}}]\'\n'
    f'aws dynamodb execute-statement --statement "INSERT INTO \\"{TABLE}\\" VALUE {{\'PK\': ?, \'SK\': ?, \'body\': ?}}" ...\n'
    f'aws dynamodb execute-statement --statement "UPDATE \\"{TABLE}\\" SET stock = stock - 1 WHERE PK = ? AND SK = ? AND stock > 1000" ...'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build DynamoDB application patterns: key design, conditional writes, locking, transactions, and paging',
    'description': (
        'Design a single-table key schema with a sparse index, load it with batch writes, protect writes with '
        'conditions and a version attribute, move store credit with an idempotent transaction, compare consistent and '
        'eventually consistent reads, page through queries, and run the same access patterns in PartiQL.'
    ),
    'steps': [
        {
            'key': 'key-design',
            'title': 'Design a single-table key schema with a sparse GSI',
            'command': KEY_DESIGN_COMMAND,
            'explanation': (
                'Start from the access patterns: get a customer profile, list a customer\'s orders newest first, list '
                'open orders, and read accounts and products by ID. A generic PK and SK hold them all in one table '
                '(PK CUST#c-100 with SK PROFILE or ORDER#001, PK ACCT#alice, PK PRODUCT#p-1), and a high-cardinality '
                'partition key spreads load. GSI1 is sparse: only open orders carry GSI1PK, so the index holds just '
                'them, and it projects only total to keep index writes small. How Floci emulates this: the table and '
                'index are real, including the INCLUDE projection; on-demand mode is a setting, since Floci never '
                'throttles.'
            ),
        },
        {
            'key': 'batch-load',
            'title': 'Load 40 orders with BatchWriteItem and retry unprocessed items',
            'command': BATCH_COMMAND,
            'explanation': (
                'BatchWriteItem takes at most 25 puts or deletes per call (a 26-item batch is rejected) and can return '
                'UnprocessedItems when a table is throttled; the SDK pattern is to resend those with exponential '
                'backoff. Batches are not transactions: each item succeeds or fails on its own, and conditions are not '
                'allowed. How Floci emulates this: the 25-item limit and consumed capacity are real, but Floci never '
                'throttles, so UnprocessedItems is always empty; the step still runs the retry loop.'
            ),
        },
        {
            'key': 'conditional-writes',
            'title': 'Create a profile only once and reserve stock with condition expressions',
            'command': CONDITIONAL_COMMAND,
            'explanation': (
                'attribute_not_exists(PK) makes a put create-only, so a second signup with the same ID fails with '
                'ConditionalCheckFailedException instead of overwriting; ReturnValuesOnConditionCheckFailure ALL_OLD '
                'returns the item that blocked it without another read. An update with "stock >= :q" reserves stock '
                'atomically, so two buyers can never oversell. How Floci emulates this: conditions are evaluated for '
                'real, and the failed write returns the existing item.'
            ),
        },
        {
            'key': 'optimistic-locking',
            'title': 'Stop a lost update with optimistic locking on a version attribute',
            'command': LOCKING_COMMAND,
            'explanation': (
                'Two writers read the product at version 1. Writer A updates the price with "version = 1" as the '
                'condition and bumps it to 2. Writer B\'s update, still expecting version 1, fails, so B re-reads '
                'version 2 and retries. Without the condition, B would silently overwrite A (a lost update). The '
                'DynamoDB Enhanced Client and DynamoDBMapper do this with a @Version attribute. How Floci emulates '
                'this: the conditional updates are real.'
            ),
        },
        {
            'key': 'transaction-transfer',
            'title': 'Move store credit with TransactWriteItems and an idempotency token',
            'command': TRANSACTION_COMMAND,
            'explanation': (
                'A transfer debits alice only if her balance covers it, credits bob, and writes a transfer record, all '
                'or nothing. A ClientRequestToken makes a retry safe for 10 minutes: replaying the same request '
                'succeeds without moving the money twice, and reusing the token with a different request fails with '
                'IdempotentParameterMismatchException. An overdraft cancels the whole transaction with one reason per '
                'item. Transactions cost twice the capacity of plain writes. How Floci emulates this: all of it is '
                'real, including cancellation reasons and the doubled capacity.'
            ),
        },
        {
            'key': 'consistent-reads',
            'title': 'Compare eventually and strongly consistent reads',
            'command': CONSISTENCY_COMMAND,
            'explanation': (
                'Reads are eventually consistent by default and cost half a read unit per 4 KB; ConsistentRead=true '
                'returns the latest committed write and costs a full unit. Global secondary indexes are always '
                'eventually consistent, so a consistent query on a GSI is rejected. How Floci emulates this: the '
                'capacity math and the GSI rule are real, but Floci runs on one node, so even an eventually consistent '
                'read always sees the latest write; on AWS it can be up to about a second behind.'
            ),
        },
        {
            'key': 'query-paging',
            'title': 'Page through a query and compare it with a filtered scan',
            'command': QUERY_COMMAND,
            'explanation': (
                'A query reads one partition by key; Limit caps the items read per page and LastEvaluatedKey says where '
                'to continue, until a page comes back without one. A filter expression runs after the read, so it '
                'trims the results but not the cost (ScannedCount vs Count), and a scan reads the whole table. total '
                'is a reserved word, so expressions need a placeholder such as #t. How Floci emulates this: paging, '
                'filters, ScannedCount, reserved words, and the sparse index are real.'
            ),
        },
        {
            'key': 'partiql',
            'title': 'Run the same access patterns with PartiQL',
            'command': PARTIQL_COMMAND,
            'explanation': (
                'PartiQL is SQL-compatible syntax for DynamoDB. A SELECT with the partition key in WHERE is a query; '
                'without it, it is a full scan, so the key design still matters. INSERT fails with '
                'DuplicateItemException if the item exists, and an UPDATE whose WHERE clause includes a non-key '
                'condition is a conditional write. How Floci emulates this: SELECT, INSERT, and conditional UPDATE '
                'behave as on AWS.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str = 'dynamodb'):
    return FlociClientFactory().client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'step': step,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _error(exc: ClientError) -> dict[str, Any]:
    error = {'Code': _code(exc), 'Message': exc.response.get('Error', {}).get('Message')}
    for name in ('Item', 'CancellationReasons'):
        if exc.response.get(name) is not None:
            error[name] = exc.response[name]
    return error


def _attempt(call) -> dict[str, Any]:
    """Run a call that may be refused and return its response or its error."""
    try:
        return {'Ok': True, 'Response': call()}
    except ClientError as exc:
        return {'Ok': False, 'Error': _error(exc)}


# ── Items ────────────────────────────────────────────────────────────────────

def S(value: str) -> dict[str, str]:  # noqa: N802 - DynamoDB type names
    return {'S': value}


def N(value: int | float) -> dict[str, str]:  # noqa: N802
    return {'N': str(value)}


def _key(pk: str, sk: str) -> dict[str, Any]:
    return {'PK': S(pk), 'SK': S(sk)}


def _number(item: dict[str, Any] | None, name: str) -> float | None:
    value = (item or {}).get(name, {}).get('N')
    return float(value) if value is not None else None


def order_item(number: int) -> dict[str, Any]:
    total = 20 + (number * 37) % 480
    item = {**_key(CUSTOMER, f'ORDER#{number:03d}'), 'total': N(total), 'status': S('CLOSED'),
            'items': N(1 + number % 5)}
    if number % OPEN_EVERY == 0:
        item.update({'status': S('OPEN'), 'GSI1PK': S('STATUS#OPEN'), 'GSI1SK': S(f'2026-10-24#{number:03d}')})
    return item


def expected_open_orders() -> int:
    return sum(1 for number in range(1, ORDER_COUNT + 1) if number % OPEN_EVERY == 0)


def expected_big_orders() -> int:
    return sum(1 for number in range(1, ORDER_COUNT + 1) if _number(order_item(number), 'total') > BIG_ORDER_TOTAL)


def _table() -> dict[str, Any] | None:
    try:
        return _client().describe_table(TableName=TABLE)['Table']
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _get(key: tuple[str, str], consistent: bool = True) -> dict[str, Any] | None:
    return _client().get_item(TableName=TABLE, Key=_key(*key), ConsistentRead=consistent).get('Item')


def _query_all(**kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    start: dict[str, Any] = {}
    while True:
        page = _client().query(TableName=TABLE, **kwargs, **start)
        items.extend(page.get('Items', []))
        if 'LastEvaluatedKey' not in page:
            return items
        start = {'ExclusiveStartKey': page['LastEvaluatedKey']}


def _orders() -> list[dict[str, Any]]:
    return _query_all(KeyConditionExpression='PK = :c AND begins_with(SK, :o)',
                      ExpressionAttributeValues={':c': S(CUSTOMER), ':o': S('ORDER#')})


def _open_orders() -> list[dict[str, Any]]:
    return _query_all(IndexName=GSI, KeyConditionExpression='GSI1PK = :s', ExpressionAttributeValues={':s': S('STATUS#OPEN')})


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_key_design() -> tuple[bool, str]:
    table = _table()
    if not table:
        return False, f'Table {TABLE} was not found.'
    keys = {item['AttributeName']: item['KeyType'] for item in table.get('KeySchema', [])}
    if keys != {'PK': 'HASH', 'SK': 'RANGE'}:
        return False, f'{TABLE} needs PK as the partition key and SK as the sort key.'
    indexes = {item['IndexName']: item for item in table.get('GlobalSecondaryIndexes', [])}
    index = indexes.get(GSI) or {}
    index_keys = {item['AttributeName']: item['KeyType'] for item in index.get('KeySchema', [])}
    projection = index.get('Projection') or {}
    if index_keys != {'GSI1PK': 'HASH', 'GSI1SK': 'RANGE'}:
        return False, f'{GSI} must use GSI1PK and GSI1SK.'
    if projection.get('ProjectionType') != 'INCLUDE' or sorted(projection.get('NonKeyAttributes') or []) != GSI_PROJECTED:
        return False, f'{GSI} should project only {", ".join(GSI_PROJECTED)} (INCLUDE).'
    if (table.get('BillingModeSummary') or {}).get('BillingMode', 'PROVISIONED') != 'PAY_PER_REQUEST':
        return False, f'{TABLE} should use on-demand capacity (PAY_PER_REQUEST).'
    return True, f'{TABLE} has PK and SK keys and a sparse {GSI} that projects only total.'


def _check_batch() -> tuple[bool, str]:
    if not _check_key_design()[0]:
        return False, f'Create {TABLE} first.'
    orders, open_orders = _orders(), _open_orders()
    if len(orders) != ORDER_COUNT:
        return False, f'{CUSTOMER} should have {ORDER_COUNT} orders; found {len(orders)}.'
    if len(open_orders) != expected_open_orders():
        return False, f'{GSI} should hold the {expected_open_orders()} open orders; found {len(open_orders)}.'
    marker = _marked('batch-load')
    if not marker:
        return False, 'Load the orders with BatchWriteItem.'
    return True, marker['message']


def _check_conditional() -> tuple[bool, str]:
    if not _check_key_design()[0]:
        return False, f'Create {TABLE} first.'
    if not _get((CUSTOMER, PROFILE_SK)):
        return False, f'The {CUSTOMER} profile was not found.'
    stock = _number(_get(INVENTORY), 'stock')
    if stock != START_STOCK - RESERVE_OK:
        return False, f'Stock should be {START_STOCK - RESERVE_OK} after reserving {RESERVE_OK}; found {stock}.'
    marker = _marked('conditional-writes')
    if not marker:
        return False, 'Run the conditional writes.'
    return True, marker['message']


def _check_locking() -> tuple[bool, str]:
    if not _check_key_design()[0]:
        return False, f'Create {TABLE} first.'
    product = _get(PRODUCT)
    if _number(product, 'version') != 3 or _number(product, 'price') != PRICE_B:
        return False, f'The product should be at version 3 with price {PRICE_B} after both writers.'
    marker = _marked('optimistic-locking')
    if not marker:
        return False, 'Run the two writers.'
    return True, marker['message']


def _check_transaction() -> tuple[bool, str]:
    if not _check_key_design()[0]:
        return False, f'Create {TABLE} first.'
    balances = (_number(_get(ALICE), 'balance'), _number(_get(BOB), 'balance'))
    expected = (START_BALANCE - TRANSFER_AMOUNT, TRANSFER_AMOUNT)
    if balances != expected:
        return False, f'Balances should be alice {expected[0]:g} and bob {expected[1]:g}; found {balances}.'
    if not _get((TRANSFER_ID, 'TRANSFER')) or _get((OVERDRAFT_ID, 'TRANSFER')):
        return False, f'Only the {TRANSFER_ID} record should exist.'
    marker = _marked('transaction-transfer')
    if not marker:
        return False, 'Run the transfer.'
    return True, marker['message']


def _check_marker(step: str, prompt: str) -> tuple[bool, str]:
    if not _check_batch()[0]:
        return False, 'Load the orders first.'
    marker = _marked(step)
    if not marker:
        return False, prompt
    return True, marker['message']


def _check_partiql() -> tuple[bool, str]:
    if not _check_batch()[0]:
        return False, 'Load the orders first.'
    if not _get(NOTE):
        return False, f'The note {NOTE[1]} inserted with PartiQL was not found.'
    marker = _marked('partiql')
    if not marker:
        return False, 'Run the PartiQL statements.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_key_design() -> dict[str, Any]:
    started = time.perf_counter()
    dynamodb = _client()
    created = False
    if not _table():
        dynamodb.create_table(
            TableName=TABLE, BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[{'AttributeName': name, 'AttributeType': 'S'} for name in ('PK', 'SK', 'GSI1PK', 'GSI1SK')],
            KeySchema=[{'AttributeName': 'PK', 'KeyType': 'HASH'}, {'AttributeName': 'SK', 'KeyType': 'RANGE'}],
            GlobalSecondaryIndexes=[{
                'IndexName': GSI,
                'KeySchema': [{'AttributeName': 'GSI1PK', 'KeyType': 'HASH'}, {'AttributeName': 'GSI1SK', 'KeyType': 'RANGE'}],
                'Projection': {'ProjectionType': 'INCLUDE', 'NonKeyAttributes': GSI_PROJECTED},
            }],
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        created = True
    _wait(lambda: (_table() or {}).get('TableStatus') == 'ACTIVE', timeout=60)
    table = _table() or {}
    response = {'Created': created, 'TableStatus': table.get('TableStatus'), 'KeySchema': table.get('KeySchema'),
                'GlobalSecondaryIndexes': table.get('GlobalSecondaryIndexes'),
                'AccessPatterns': {
                    'Customer profile': f'GetItem PK={CUSTOMER}, SK={PROFILE_SK}',
                    'Customer orders, newest first': f'Query PK={CUSTOMER}, begins_with(SK, ORDER#), ScanIndexForward=false',
                    'Open orders': f'Query {GSI} GSI1PK=STATUS#OPEN (sparse: only open orders have GSI1PK)',
                    'Account or product by ID': 'GetItem PK=ACCT#<id> or PRODUCT#<id>',
                }}
    verified, message = _check_key_design()
    return _result('key-design', KEY_DESIGN_COMMAND, response, verified, message, started)


def step_batch_load() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_key_design()[0]:
        return _result('batch-load', BATCH_COMMAND, {}, False, f'Create {TABLE} first.', started)
    dynamodb = _client()
    requests = [{'PutRequest': {'Item': order_item(number)}} for number in range(1, ORDER_COUNT + 1)]
    too_big = _attempt(lambda: dynamodb.batch_write_item(RequestItems={TABLE: requests[:BATCH_LIMIT + 1]}))
    batches, retries, capacity = [], 0, 0.0
    for start in range(0, len(requests), BATCH_LIMIT):
        pending = {TABLE: requests[start:start + BATCH_LIMIT]}
        sent = len(pending[TABLE])
        for attempt in range(5):
            result = dynamodb.batch_write_item(RequestItems=pending, ReturnConsumedCapacity='TOTAL')
            capacity += sum(item.get('CapacityUnits', 0) for item in result.get('ConsumedCapacity', []))
            pending = result.get('UnprocessedItems') or {}
            if not pending:
                break
            retries += 1
            time.sleep(min(2 ** attempt * 0.1, 2))
        batches.append({'Items': sent, 'UnprocessedAfterRetries': len(pending.get(TABLE, []))})
    orders, open_orders = _orders(), _open_orders()
    response = {'TwentySixItemBatch': too_big, 'Batches': batches, 'Retries': retries,
                'ConsumedWriteCapacity': capacity, 'Orders': len(orders), 'OpenOrdersInIndex': len(open_orders)}
    if too_big.get('Ok'):
        return _result('batch-load', BATCH_COMMAND, response, False,
                       f'A batch of {BATCH_LIMIT + 1} items should be rejected.', started)
    if len(orders) != ORDER_COUNT or len(open_orders) != expected_open_orders():
        return _result('batch-load', BATCH_COMMAND, response, False,
                       f'Expected {ORDER_COUNT} orders and {expected_open_orders()} open orders in {GSI}; found '
                       f'{len(orders)} and {len(open_orders)}.', started)
    message = (f'A {BATCH_LIMIT + 1}-item batch was rejected ({too_big["Error"]["Code"]}), then {ORDER_COUNT} orders went '
               f'in as batches of {" and ".join(str(batch["Items"]) for batch in batches)}, using {capacity:g} write '
               f'units. {GSI} holds only the {len(open_orders)} open orders. ')
    message += (f'{retries} retries resent unprocessed items.' if retries else
                'No items came back unprocessed: Floci never throttles, so the retry loop had nothing to resend.')
    _marker('batch-load', {'message': message})
    verified, check = _check_batch()
    return _result('batch-load', BATCH_COMMAND, response, verified, message if verified else check, started)


def step_conditional_writes() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_key_design()[0]:
        return _result('conditional-writes', CONDITIONAL_COMMAND, {}, False, f'Create {TABLE} first.', started)
    dynamodb = _client()
    # Start from a clean profile and full stock, so the step can be rerun.
    dynamodb.delete_item(TableName=TABLE, Key=_key(CUSTOMER, PROFILE_SK))
    dynamodb.put_item(TableName=TABLE, Item={**_key(*INVENTORY), 'stock': N(START_STOCK)})
    profile = {**_key(CUSTOMER, PROFILE_SK), 'name': S('Dana Developer'), 'email': S('dana@example.com')}
    create = lambda name: _attempt(lambda: dynamodb.put_item(  # noqa: E731
        TableName=TABLE, Item={**profile, 'name': S(name)}, ConditionExpression='attribute_not_exists(PK)',
        ReturnValuesOnConditionCheckFailure='ALL_OLD'))
    first, second = create('Dana Developer'), create('Impostor')
    reserve = lambda quantity: _attempt(lambda: dynamodb.update_item(  # noqa: E731
        TableName=TABLE, Key=_key(*INVENTORY), UpdateExpression='SET stock = stock - :q',
        ConditionExpression='stock >= :q', ExpressionAttributeValues={':q': N(quantity)}, ReturnValues='UPDATED_NEW'))
    reserved, oversold = reserve(RESERVE_OK), reserve(RESERVE_TOO_MANY)
    stock = _number(_get(INVENTORY), 'stock')
    stored_name = (_get((CUSTOMER, PROFILE_SK)) or {}).get('name', {}).get('S')
    response = {'FirstCreate': first, 'SecondCreate': second, 'Reserve3': reserved, 'Reserve50': oversold,
                'StockNow': stock, 'StoredName': stored_name}
    blocked_by = ((second.get('Error') or {}).get('Item') or {}).get('name', {}).get('S')
    if not first['Ok'] or second['Ok'] or second['Error']['Code'] != 'ConditionalCheckFailedException':
        return _result('conditional-writes', CONDITIONAL_COMMAND, response, False,
                       'The first create should succeed and the second should fail its condition.', started)
    if not reserved['Ok'] or oversold['Ok'] or stock != START_STOCK - RESERVE_OK or stored_name != 'Dana Developer':
        return _result('conditional-writes', CONDITIONAL_COMMAND, response, False,
                       f'Reserving {RESERVE_OK} should work, reserving {RESERVE_TOO_MANY} should fail, and the stock '
                       f'should end at {START_STOCK - RESERVE_OK}.', started)
    message = ('The profile was created once; the second create failed with ConditionalCheckFailedException'
               + (f' and returned the existing item (name {blocked_by}) without another read' if blocked_by else '')
               + f'. Reserving {RESERVE_OK} took stock from {START_STOCK} to {stock:g}, and reserving '
               f'{RESERVE_TOO_MANY} failed its condition, so nothing was oversold.')
    _marker('conditional-writes', {'message': message})
    verified, check = _check_conditional()
    return _result('conditional-writes', CONDITIONAL_COMMAND, response, verified, message if verified else check, started)


def step_optimistic_locking() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_key_design()[0]:
        return _result('optimistic-locking', LOCKING_COMMAND, {}, False, f'Create {TABLE} first.', started)
    dynamodb = _client()
    dynamodb.put_item(TableName=TABLE, Item={**_key(*PRODUCT), 'name': S('Trail mug'), 'price': N(START_PRICE),
                                             'version': N(1)})

    def save(price: int, expected_version: float) -> dict[str, Any]:
        return _attempt(lambda: dynamodb.update_item(
            TableName=TABLE, Key=_key(*PRODUCT), UpdateExpression='SET price = :p, version = version + :one',
            ConditionExpression='version = :v',
            ExpressionAttributeValues={':p': N(price), ':v': N(int(expected_version)), ':one': N(1)},
            ReturnValues='ALL_NEW'))

    seen_a, seen_b = _get(PRODUCT), _get(PRODUCT)
    a_saves = save(PRICE_A, _number(seen_a, 'version'))
    b_first = save(PRICE_B, _number(seen_b, 'version'))
    reread = _get(PRODUCT)
    b_retry = save(PRICE_B, _number(reread, 'version'))
    final = _get(PRODUCT)
    response = {'WriterAReads': seen_a, 'WriterBReads': seen_b, 'WriterASaves': a_saves, 'WriterBFirstTry': b_first,
                'WriterBRereads': reread, 'WriterBRetry': b_retry, 'Final': final}
    if not a_saves['Ok'] or b_first['Ok'] or not b_retry['Ok']:
        return _result('optimistic-locking', LOCKING_COMMAND, response, False,
                       'Writer A should save, writer B\'s stale update should fail, and B\'s retry should save.', started)
    message = (f'Both writers read version 1. Writer A saved price {PRICE_A} as version 2; writer B\'s update, still '
               f'expecting version 1, failed with {b_first["Error"]["Code"]} instead of overwriting it. B re-read '
               f'version {_number(reread, "version"):g} (price {_number(reread, "price"):g}) and saved price {PRICE_B} '
               f'as version {_number(final, "version"):g}.')
    _marker('optimistic-locking', {'message': message})
    verified, check = _check_locking()
    return _result('optimistic-locking', LOCKING_COMMAND, response, verified, message if verified else check, started)


def _transfer(amount: int, token: str, transfer_id: str) -> dict[str, Any]:
    return _attempt(lambda: _client().transact_write_items(ClientRequestToken=token, ReturnConsumedCapacity='TOTAL', TransactItems=[
        {'Update': {'TableName': TABLE, 'Key': _key(*ALICE), 'UpdateExpression': 'SET balance = balance - :a',
                    'ConditionExpression': 'balance >= :a', 'ExpressionAttributeValues': {':a': N(amount)}}},
        {'Update': {'TableName': TABLE, 'Key': _key(*BOB), 'UpdateExpression': 'SET balance = balance + :a',
                    'ExpressionAttributeValues': {':a': N(amount)}}},
        {'Put': {'TableName': TABLE, 'Item': {**_key(transfer_id, 'TRANSFER'), 'amount': N(amount),
                                              'from': S(ALICE[0]), 'to': S(BOB[0])},
                 'ConditionExpression': 'attribute_not_exists(PK)'}},
    ]))


def step_transaction_transfer() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_key_design()[0]:
        return _result('transaction-transfer', TRANSACTION_COMMAND, {}, False, f'Create {TABLE} first.', started)
    dynamodb = _client()
    # Open both accounts again and clear old transfers, so the step can be rerun.
    for account, balance in ((ALICE, START_BALANCE), (BOB, 0)):
        dynamodb.put_item(TableName=TABLE, Item={**_key(*account), 'balance': N(balance)})
    for transfer_id in (TRANSFER_ID, OVERDRAFT_ID):
        dynamodb.delete_item(TableName=TABLE, Key=_key(transfer_id, 'TRANSFER'))
    token = str(uuid.uuid4())
    first = _transfer(TRANSFER_AMOUNT, token, TRANSFER_ID)
    replay = _transfer(TRANSFER_AMOUNT, token, TRANSFER_ID)
    mismatch = _transfer(TRANSFER_AMOUNT + 5, token, TRANSFER_ID)
    overdraft = _transfer(OVERDRAFT_AMOUNT, str(uuid.uuid4()), OVERDRAFT_ID)
    read = dynamodb.transact_get_items(TransactItems=[{'Get': {'TableName': TABLE, 'Key': _key(*account)}}
                                                      for account in (ALICE, BOB)])
    balances = [_number(item.get('Item'), 'balance') for item in read.get('Responses', [])]
    capacity = sum(item.get('CapacityUnits', 0) for item in (first.get('Response') or {}).get('ConsumedCapacity', []))
    reasons = [reason.get('Code') for reason in (overdraft.get('Error') or {}).get('CancellationReasons') or []]
    response = {'ClientRequestToken': token, 'Transfer': first, 'Replay': replay, 'SameTokenDifferentRequest': mismatch,
                'Overdraft': overdraft, 'TransactGetItems': balances}
    expected = [START_BALANCE - TRANSFER_AMOUNT, TRANSFER_AMOUNT]
    if not first['Ok'] or not replay['Ok'] or balances != expected:
        return _result('transaction-transfer', TRANSACTION_COMMAND, response, False,
                       f'The transfer and its replay should leave alice {expected[0]} and bob {expected[1]}; found '
                       f'{balances}.', started)
    if overdraft['Ok'] or (overdraft.get('Error') or {}).get('Code') != 'TransactionCanceledException':
        return _result('transaction-transfer', TRANSACTION_COMMAND, response, False,
                       f'A transfer of {OVERDRAFT_AMOUNT} should be cancelled.', started)
    message = (f'The transfer moved {TRANSFER_AMOUNT} from alice to bob and wrote {TRANSFER_ID} for {capacity:g} write '
               f'units (twice a plain write per item). Replaying it with the same token succeeded without moving the '
               f'money again (alice {balances[0]:g}, bob {balances[1]:g}).')
    if not mismatch['Ok']:
        message += f' Reusing the token for a different amount failed with {mismatch["Error"]["Code"]}.'
    message += f' A {OVERDRAFT_AMOUNT} transfer was cancelled as a whole'
    message += f' with reasons {", ".join(reasons)}.' if reasons else '.'
    _marker('transaction-transfer', {'message': message})
    verified, check = _check_transaction()
    return _result('transaction-transfer', TRANSACTION_COMMAND, response, verified, message if verified else check, started)


def step_consistent_reads() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_batch()[0]:
        return _result('consistent-reads', CONSISTENCY_COMMAND, {}, False, 'Load the orders first.', started)
    dynamodb = _client()
    key = _key(CUSTOMER, 'ORDER#001')
    dynamodb.update_item(TableName=TABLE, Key=key, UpdateExpression='SET note = :n',
                         ExpressionAttributeValues={':n': S(f'checked {uuid.uuid4().hex[:8]}')})
    written = (_get((CUSTOMER, 'ORDER#001')) or {}).get('note', {}).get('S')
    eventual = dynamodb.get_item(TableName=TABLE, Key=key, ReturnConsumedCapacity='TOTAL')
    strong = dynamodb.get_item(TableName=TABLE, Key=key, ConsistentRead=True, ReturnConsumedCapacity='TOTAL')
    gsi = _attempt(lambda: dynamodb.query(TableName=TABLE, IndexName=GSI, KeyConditionExpression='GSI1PK = :s',
                                          ExpressionAttributeValues={':s': S('STATUS#OPEN')}, ConsistentRead=True))
    units = [(read.get('ConsumedCapacity') or {}).get('CapacityUnits') for read in (eventual, strong)]
    eventual_note = eventual.get('Item', {}).get('note', {}).get('S')
    response = {'EventualRead': {'ConsumedCapacity': units[0], 'SawLatestWrite': eventual_note == written},
                'StrongRead': {'ConsumedCapacity': units[1]}, 'ConsistentQueryOnGsi': gsi}
    if gsi['Ok']:
        return _result('consistent-reads', CONSISTENCY_COMMAND, response, False,
                       f'A consistent query on {GSI} should be rejected.', started)
    if units[0] and units[1] and units[1] != units[0] * 2:
        return _result('consistent-reads', CONSISTENCY_COMMAND, response, False,
                       f'A strongly consistent read should cost twice an eventually consistent one; got {units}.', started)
    message = (f'The eventually consistent read cost {units[0]:g} read units and the strongly consistent read {units[1]:g}. '
               if units[0] and units[1] else 'Both reads returned the order. ')
    message += f'A consistent query on {GSI} was rejected ({gsi["Error"]["Code"]}): indexes are always eventually consistent.'
    if eventual_note == written:
        message += (' The eventually consistent read already saw the update made just before it; Floci runs on one node, '
                    'so reads are never stale, while on AWS it could lag by about a second.')
    _marker('consistent-reads', {'message': message})
    verified, check = _check_marker('consistent-reads', 'Run the reads.')
    return _result('consistent-reads', CONSISTENCY_COMMAND, response, verified, message if verified else check, started)


def step_query_paging() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_batch()[0]:
        return _result('query-paging', QUERY_COMMAND, {}, False, 'Load the orders first.', started)
    dynamodb = _client()
    pages, start = [], {}
    while True:
        page = dynamodb.query(TableName=TABLE, KeyConditionExpression='PK = :c AND begins_with(SK, :o)',
                              ExpressionAttributeValues={':c': S(CUSTOMER), ':o': S('ORDER#')}, Limit=PAGE_SIZE,
                              ScanIndexForward=False, **start)
        pages.append({'Count': page['Count'], 'First': (page.get('Items') or [{}])[0].get('SK', {}).get('S'),
                      'LastEvaluatedKey': page.get('LastEvaluatedKey')})
        if 'LastEvaluatedKey' not in page or len(pages) > ORDER_COUNT:
            break
        start = {'ExclusiveStartKey': page['LastEvaluatedKey']}
    filter_values = {':c': S(CUSTOMER), ':o': S('ORDER#'), ':big': N(BIG_ORDER_TOTAL)}
    reserved = _attempt(lambda: dynamodb.query(TableName=TABLE, KeyConditionExpression='PK = :c AND begins_with(SK, :o)',
                                               FilterExpression='total > :big', ExpressionAttributeValues=filter_values))
    filtered = dynamodb.query(TableName=TABLE, KeyConditionExpression='PK = :c AND begins_with(SK, :o)',
                              FilterExpression='#t > :big', ExpressionAttributeNames={'#t': 'total'},
                              ExpressionAttributeValues=filter_values)
    scanned = {'Count': 0, 'ScannedCount': 0}
    scan_start: dict[str, Any] = {}
    while True:
        page = dynamodb.scan(TableName=TABLE, FilterExpression='#t > :big', ExpressionAttributeNames={'#t': 'total'},
                             ExpressionAttributeValues={':big': N(BIG_ORDER_TOTAL)}, **scan_start)
        scanned['Count'] += page['Count']
        scanned['ScannedCount'] += page['ScannedCount']
        if 'LastEvaluatedKey' not in page:
            break
        scan_start = {'ExclusiveStartKey': page['LastEvaluatedKey']}
    open_orders = dynamodb.query(TableName=TABLE, IndexName=GSI, KeyConditionExpression='GSI1PK = :s',
                                 ExpressionAttributeValues={':s': S('STATUS#OPEN')})
    projected = sorted({name for item in open_orders.get('Items', []) for name in item})
    total_read = sum(page['Count'] for page in pages)
    response = {'Pages': pages, 'ReservedWordWithoutPlaceholder': reserved,
                'QueryWithFilter': {'Count': filtered['Count'], 'ScannedCount': filtered['ScannedCount']},
                'ScanWithFilter': scanned,
                'OpenOrders': {'Count': open_orders['Count'], 'Attributes': projected}}
    if total_read != ORDER_COUNT or pages[0]['First'] != f'ORDER#{ORDER_COUNT:03d}':
        return _result('query-paging', QUERY_COMMAND, response, False,
                       f'Paging should return all {ORDER_COUNT} orders, newest first; got {total_read}.', started)
    if filtered['Count'] != expected_big_orders() or filtered['ScannedCount'] != ORDER_COUNT:
        return _result('query-paging', QUERY_COMMAND, response, False,
                       f'The filtered query should read {ORDER_COUNT} orders and return {expected_big_orders()}.', started)
    if open_orders['Count'] != expected_open_orders() or 'items' in projected:
        return _result('query-paging', QUERY_COMMAND, response, False,
                       f'{GSI} should return the {expected_open_orders()} open orders with only the projected attributes.',
                       started)
    full_pages = [page['Count'] for page in pages]
    message = (f'Pages of {PAGE_SIZE} returned {" + ".join(str(count) for count in full_pages)} orders, newest first, '
               f'following LastEvaluatedKey until a page came back without one. The filter on total read '
               f'{filtered["ScannedCount"]} orders to return {filtered["Count"]}, while the scan read '
               f'{scanned["ScannedCount"]} items in the whole table for the same {scanned["Count"]}. ')
    if not reserved['Ok']:
        message += 'Without the #t placeholder the filter was rejected, because total is a reserved word. '
    message += (f'{GSI} returned the {open_orders["Count"]} open orders with only {", ".join(projected)}, '
                'not their other attributes.')
    _marker('query-paging', {'message': message})
    verified, check = _check_marker('query-paging', 'Run the queries.')
    return _result('query-paging', QUERY_COMMAND, response, verified, message if verified else check, started)


def step_partiql() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_batch()[0] or not _check_conditional()[0]:
        return _result('partiql', PARTIQL_COMMAND, {}, False, 'Load the orders and run the conditional writes first.', started)
    dynamodb = _client()
    dynamodb.delete_item(TableName=TABLE, Key=_key(*NOTE))
    select = dynamodb.execute_statement(
        Statement=f'SELECT SK, total FROM "{TABLE}" WHERE PK = ? AND begins_with(SK, ?)',
        Parameters=[S(CUSTOMER), S('ORDER#00')])
    insert_statement = f"INSERT INTO \"{TABLE}\" VALUE {{'PK': ?, 'SK': ?, 'body': ?}}"
    insert_parameters = [S(NOTE[0]), S(NOTE[1]), S('Prefers weekend delivery')]
    inserted = _attempt(lambda: dynamodb.execute_statement(Statement=insert_statement, Parameters=insert_parameters))
    duplicate = _attempt(lambda: dynamodb.execute_statement(Statement=insert_statement, Parameters=insert_parameters))
    guarded = _attempt(lambda: dynamodb.execute_statement(
        Statement=f'UPDATE "{TABLE}" SET stock = stock - 1 WHERE PK = ? AND SK = ? AND stock > 1000',
        Parameters=[S(INVENTORY[0]), S(INVENTORY[1])]))
    stock = _number(_get(INVENTORY), 'stock')
    rows = select.get('Items', [])
    response = {'Select': {'Rows': len(rows), 'Sample': rows[:3]}, 'Insert': inserted, 'InsertAgain': duplicate,
                'ConditionalUpdate': guarded, 'StockAfter': stock}
    if len(rows) != 9 or not inserted['Ok'] or duplicate['Ok'] or guarded['Ok'] or stock != START_STOCK - RESERVE_OK:
        return _result('partiql', PARTIQL_COMMAND, response, False,
                       'SELECT should return orders 001 to 009, the first INSERT should work and the second fail, and the '
                       'conditional UPDATE should change nothing.', started)
    message = (f'SELECT with the partition key returned orders 001 to 009 as a query. INSERT added {NOTE[1]}, and inserting '
               f'it again failed with {duplicate["Error"]["Code"]}. The UPDATE with "stock > 1000" in its WHERE clause '
               f'failed with {guarded["Error"]["Code"]}, leaving stock at {stock:g}.')
    _marker('partiql', {'message': message})
    verified, check = _check_partiql()
    return _result('partiql', PARTIQL_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'key-design': step_key_design,
    'batch-load': step_batch_load,
    'conditional-writes': step_conditional_writes,
    'optimistic-locking': step_optimistic_locking,
    'transaction-transfer': step_transaction_transfer,
    'consistent-reads': step_consistent_reads,
    'query-paging': step_query_paging,
    'partiql': step_partiql,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status() -> dict[str, Any]:
    checks = {
        'key-design': lambda: _check_key_design()[0],
        'batch-load': lambda: _check_batch()[0],
        'conditional-writes': lambda: _check_conditional()[0],
        'optimistic-locking': lambda: _check_locking()[0],
        'transaction-transfer': lambda: _check_transaction()[0],
        'consistent-reads': lambda: _check_marker('consistent-reads', '')[0],
        'query-paging': lambda: _check_marker('query-paging', '')[0],
        'partiql': lambda: _check_partiql()[0],
    }
    steps = {}
    for step, check in checks.items():
        verified = _safe(check)
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset() -> dict[str, Any]:
    """Delete the lab table and clear progress."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []
    try:
        if _table():
            _client().delete_table(TableName=TABLE)
            deleted.append(f'table:{TABLE}')
    except ClientError as exc:
        if _code(exc) != 'ResourceNotFoundException':
            errors.append(f'table:{TABLE}: {exc}')
    except BotoCoreError as exc:
        errors.append(f'table:{TABLE}: {exc}')
    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': f'aws dynamodb delete-table --table-name {TABLE}',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'errors': errors,
        'verification': {
            'status': 'failed' if errors else 'passed',
            'message': 'The lab table could not be removed: ' + '; '.join(errors) if errors
            else f'{TABLE} is deleted with every item in it.',
        },
    }
