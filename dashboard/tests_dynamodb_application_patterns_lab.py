"""DynamoDB application patterns lab: key design, batch writes, conditional writes, optimistic locking, an idempotent
transaction, consistent reads, paging, and PartiQL. The fake behaves like Floci 2.3.0: it evaluates the lab's condition
and update expressions, rejects 26-item batches and consistent reads on a GSI, honors client request tokens, pages with
LastEvaluatedKey, and never throttles."""

import copy
import importlib
import re
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.dynamodb_patterns_labs')

SERVICE = 'dynamodb'
LAB = 'application-patterns'
RESERVED = {'total', 'status', 'name', 'items'}


def client_error(code: str, message: str = 'refused', **extra) -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}, **extra},
                       'Operation')


def value(attribute):
    if attribute is None:
        return None
    if 'N' in attribute:
        return float(attribute['N'])
    return attribute['S']


def number(amount: float) -> dict:
    return {'N': str(int(amount)) if float(amount).is_integer() else str(amount)}


class FakeDynamo:
    def __init__(self, unprocessed_once=False):
        self.unprocessed_once = unprocessed_once
        self.table = None
        self.items: dict[tuple[str, str], dict] = {}
        self.tokens: dict[str, list] = {}

    # Expressions
    def _name(self, token, names):
        token = token.strip()
        if token.startswith('#'):
            return names[token]
        if token in RESERVED:
            raise client_error('ValidationException', f'Attribute name is a reserved keyword; reserved word: {token}')
        return token

    def _condition(self, item, expression, names=None, values=None):
        names, values = names or {}, values or {}
        if not expression:
            return True
        expression = expression.strip()
        if expression.startswith('attribute_not_exists('):
            return item is None
        left, op, right = re.match(r'(\S+)\s*(>=|>|=)\s*(\S+)', expression).groups()
        current = value((item or {}).get(self._name(left, names)))
        target = value(values[right])
        if current is None:
            return False
        return {'>=': current >= target, '>': current > target, '=': current == target}[op]

    def _update(self, item, expression, names=None, values=None):
        names, values = names or {}, values or {}
        for assignment in expression.removeprefix('SET ').split(','):
            target, source = (part.strip() for part in assignment.split('='))
            target = self._name(target, names)
            match = re.match(r'(\w+)\s*([+-])\s*(:\w+)', source)
            if match:
                base, sign, operand = match.groups()
                result = value(item[base]) + (1 if sign == '+' else -1) * value(values[operand])
                item[target] = number(result)
            else:
                item[target] = values[source]

    def _key(self, key):
        return key['PK']['S'], key['SK']['S']

    def _require_table(self, name):
        if not self.table or name != self.table['TableName']:
            raise client_error('ResourceNotFoundException')

    # Tables
    def describe_table(self, TableName):
        self._require_table(TableName)
        return {'Table': copy.deepcopy(self.table)}

    def create_table(self, TableName, BillingMode, AttributeDefinitions, KeySchema, GlobalSecondaryIndexes, Tags):
        self.table = {'TableName': TableName, 'TableStatus': 'ACTIVE', 'KeySchema': KeySchema,
                      'GlobalSecondaryIndexes': GlobalSecondaryIndexes, 'BillingModeSummary': {'BillingMode': BillingMode}}

    def delete_table(self, TableName):
        self._require_table(TableName)
        self.table, self.items = None, {}

    # Items
    def get_item(self, TableName, Key, ConsistentRead=False, ReturnConsumedCapacity=None):
        self._require_table(TableName)
        response = {}
        item = self.items.get(self._key(Key))
        if item:
            response['Item'] = copy.deepcopy(item)
        if ReturnConsumedCapacity:
            response['ConsumedCapacity'] = {'TableName': TableName, 'CapacityUnits': 1.0 if ConsistentRead else 0.5}
        return response

    def put_item(self, TableName, Item, ConditionExpression=None, ReturnValuesOnConditionCheckFailure=None):
        self._require_table(TableName)
        key = self._key(Item)
        existing = self.items.get(key)
        if not self._condition(existing, ConditionExpression):
            extra = {'Item': copy.deepcopy(existing)} if ReturnValuesOnConditionCheckFailure == 'ALL_OLD' else {}
            raise client_error('ConditionalCheckFailedException', 'The conditional request failed', **extra)
        self.items[key] = copy.deepcopy(Item)
        return {}

    def delete_item(self, TableName, Key):
        self._require_table(TableName)
        self.items.pop(self._key(Key), None)

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None,
                    ReturnValues=None):
        self._require_table(TableName)
        key = self._key(Key)
        item = self.items.get(key)
        if not self._condition(item, ConditionExpression, values=ExpressionAttributeValues):
            raise client_error('ConditionalCheckFailedException', 'The conditional request failed')
        item = item if item is not None else dict(Key)
        self._update(item, UpdateExpression, values=ExpressionAttributeValues)
        self.items[key] = item
        return {'Attributes': copy.deepcopy(item)}

    def batch_write_item(self, RequestItems, ReturnConsumedCapacity=None):
        (table, requests), = RequestItems.items()
        if len(requests) > 25:
            raise client_error('ValidationException', 'Member must have length less than or equal to 25')
        unprocessed = []
        if self.unprocessed_once:
            self.unprocessed_once = False
            requests, unprocessed = requests[:-2], requests[-2:]
        for request in requests:
            self.put_item(TableName=table, Item=request['PutRequest']['Item'])
        response = {'UnprocessedItems': {table: unprocessed} if unprocessed else {}}
        if ReturnConsumedCapacity:
            response['ConsumedCapacity'] = [{'TableName': table, 'CapacityUnits': float(len(requests))}]
        return response

    # Reads
    def _select(self, IndexName=None, KeyConditionExpression='', ExpressionAttributeValues=None):
        values = ExpressionAttributeValues or {}
        if IndexName:
            wanted = values[':s']['S']
            rows = [item for item in self.items.values() if item.get('GSI1PK', {}).get('S') == wanted]
            rows.sort(key=lambda item: item['GSI1SK']['S'])
            projected = {'PK', 'SK', 'GSI1PK', 'GSI1SK', *lab.GSI_PROJECTED}
            return [{name: attr for name, attr in item.items() if name in projected} for item in rows], 'GSI1SK'
        pk, prefix = values[':c']['S'], values[':o']['S']
        rows = [item for item in self.items.values() if item['PK']['S'] == pk and item['SK']['S'].startswith(prefix)]
        rows.sort(key=lambda item: item['SK']['S'])
        return rows, 'SK'

    def query(self, TableName, KeyConditionExpression, ExpressionAttributeValues, IndexName=None, Limit=None,
              ScanIndexForward=True, ExclusiveStartKey=None, FilterExpression=None, ExpressionAttributeNames=None,
              ConsistentRead=False):
        self._require_table(TableName)
        if IndexName and ConsistentRead:
            raise client_error('ValidationException', 'Consistent reads are not supported on global secondary indexes')
        if FilterExpression:
            self._name(re.match(r'(\S+)', FilterExpression).group(1), ExpressionAttributeNames or {})
        rows, sort_key = self._select(IndexName, KeyConditionExpression, ExpressionAttributeValues)
        if not ScanIndexForward:
            rows.reverse()
        if ExclusiveStartKey:
            position = next(index for index, item in enumerate(rows) if item['SK'] == ExclusiveStartKey['SK'])
            rows = rows[position + 1:]
        page = rows[:Limit] if Limit else rows
        response = {'ScannedCount': len(page)}
        if Limit and len(page) == Limit:
            response['LastEvaluatedKey'] = {'PK': page[-1]['PK'], 'SK': page[-1]['SK']}
        if FilterExpression:
            page = [item for item in page if self._condition(item, FilterExpression, ExpressionAttributeNames,
                                                             ExpressionAttributeValues)]
        response.update({'Items': copy.deepcopy(page), 'Count': len(page)})
        return response

    def scan(self, TableName, FilterExpression, ExpressionAttributeNames, ExpressionAttributeValues, ExclusiveStartKey=None):
        self._require_table(TableName)
        rows = list(self.items.values())
        matches = [item for item in rows if 'total' in item and self._condition(
            item, FilterExpression, ExpressionAttributeNames, ExpressionAttributeValues)]
        return {'Items': matches, 'Count': len(matches), 'ScannedCount': len(rows)}

    # Transactions
    def transact_write_items(self, ClientRequestToken, TransactItems, ReturnConsumedCapacity=None):
        if ClientRequestToken in self.tokens:
            if self.tokens[ClientRequestToken] != TransactItems:
                raise client_error('IdempotentParameterMismatchException')
            return {'ConsumedCapacity': [{'TableName': lab.TABLE, 'CapacityUnits': 2.0 * len(TransactItems)}]}
        reasons, ok = [], True
        for entry in TransactItems:
            (kind, request), = entry.items()
            key = self._key(request.get('Key') or request['Item'])
            passed = self._condition(self.items.get(key), request.get('ConditionExpression'),
                                     values=request.get('ExpressionAttributeValues'))
            reasons.append({'Code': 'None'} if passed else {'Code': 'ConditionalCheckFailed', 'Message': 'The conditional request failed'})
            ok = ok and passed
        if not ok:
            raise client_error('TransactionCanceledException', 'Transaction cancelled', CancellationReasons=reasons)
        for entry in TransactItems:
            (kind, request), = entry.items()
            if kind == 'Put':
                self.items[self._key(request['Item'])] = copy.deepcopy(request['Item'])
            else:
                self.update_item(TableName=lab.TABLE, Key=request['Key'], UpdateExpression=request['UpdateExpression'],
                                 ExpressionAttributeValues=request['ExpressionAttributeValues'])
        self.tokens[ClientRequestToken] = copy.deepcopy(TransactItems)
        return {'ConsumedCapacity': [{'TableName': lab.TABLE, 'CapacityUnits': 2.0 * len(TransactItems)}]}

    def transact_get_items(self, TransactItems):
        return {'Responses': [{'Item': copy.deepcopy(self.items.get(self._key(entry['Get']['Key'])))}
                              for entry in TransactItems]}

    # PartiQL
    def execute_statement(self, Statement, Parameters):
        if Statement.startswith('SELECT'):
            rows, _ = self._select(ExpressionAttributeValues={':c': Parameters[0], ':o': Parameters[1]})
            return {'Items': [{'SK': item['SK'], 'total': item['total']} for item in rows]}
        if Statement.startswith('INSERT'):
            key = (Parameters[0]['S'], Parameters[1]['S'])
            if key in self.items:
                raise client_error('DuplicateItemException', 'Duplicate primary key exists in table')
            self.items[key] = {'PK': Parameters[0], 'SK': Parameters[1], 'body': Parameters[2]}
            return {}
        key = (Parameters[0]['S'], Parameters[1]['S'])
        limit = float(re.search(r'stock > (\d+)', Statement).group(1))
        if value(self.items[key]['stock']) <= limit:
            raise client_error('ConditionalCheckFailedException', 'The conditional request failed')
        self.items[key]['stock'] = number(value(self.items[key]['stock']) - 1)
        return {}


class DynamoDbApplicationPatternsLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeDynamo()
        patcher = patch('dashboard.labs.dynamodb_patterns_labs._client', side_effect=lambda name='dynamodb': self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.dynamodb_patterns_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self, results):
        return {key: result['verification']['message'] for key, result in results.items() if not result['verified']}

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('DVA-C02', '1.3'), tasks)
        primary = [tag for tag in LAB_EXAM_TAGS[(SERVICE, LAB)] if tag['exam'] == 'DVA-C02' and tag['primary']]
        self.assertEqual(primary[0]['task'], '1.3')

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_order_data_matches_the_expected_counts(self):
        self.assertEqual(lab.expected_open_orders(), 10)
        self.assertTrue(0 < lab.expected_big_orders() < lab.ORDER_COUNT)
        self.assertNotIn('GSI1PK', lab.order_item(1))
        self.assertEqual(lab.order_item(4)['GSI1PK'], {'S': 'STATUS#OPEN'})

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        self.assertEqual(self.failed(results), {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_messages_report_what_happened(self):
        results = self.run_all()
        self.assertIn('Floci never throttles', results['batch-load']['verification']['message'])
        self.assertIn('name Dana Developer', results['conditional-writes']['verification']['message'])
        self.assertIn('version 3', results['optimistic-locking']['verification']['message'])
        transfer = results['transaction-transfer']['verification']['message']
        self.assertIn('IdempotentParameterMismatchException', transfer)
        self.assertIn('ConditionalCheckFailed, None, None', transfer)
        self.assertIn('never stale', results['consistent-reads']['verification']['message'])
        paging = results['query-paging']['verification']['message']
        self.assertIn('10 + 10 + 10 + 10', paging)
        self.assertIn('reserved word', paging)
        self.assertIn('DuplicateItemException', results['partiql']['verification']['message'])

    def test_unprocessed_items_are_retried(self):
        self.fake.unprocessed_once = True
        run_lab_step(SERVICE, LAB, 'key-design')
        result = run_lab_step(SERVICE, LAB, 'batch-load')
        self.assertTrue(result['verified'])
        self.assertIn('1 retries resent unprocessed items', result['verification']['message'])
        self.assertEqual(result['json']['Retries'], 1)

    def test_key_design_is_checked(self):
        self.run_all()
        self.fake.table['GlobalSecondaryIndexes'][0]['Projection'] = {'ProjectionType': 'ALL'}
        self.assertIn('project only total', lab._check_key_design()[1])
        self.assertEqual([step for step, item in lab_status(SERVICE, LAB)['steps'].items() if item['verified']], [])

    def test_a_lost_update_fails_the_locking_check(self):
        self.run_all()
        self.fake.items[lab.PRODUCT]['version'] = {'N': '2'}
        self.assertIn('version 3', lab._check_locking()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'optimistic-locking')['verified'])

    def test_a_double_transfer_fails_the_transaction_check(self):
        self.run_all()
        self.fake.items[lab.ALICE]['balance'] = {'N': '50'}
        self.assertIn('alice 75', lab._check_transaction()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'transaction-transfer')['verified'])

    def test_rerunning_steps_keeps_the_data_consistent(self):
        self.run_all()
        results = self.run_all()
        self.assertEqual(self.failed(results), {})
        self.assertEqual(len(lab._orders()), lab.ORDER_COUNT)
        self.assertEqual(lab._number(lab._get(lab.INVENTORY), 'stock'), lab.START_STOCK - lab.RESERVE_OK)

    def test_steps_out_of_order_explain_what_is_missing(self):
        for step in lab.STEP_KEYS[1:]:
            with self.subTest(step=step):
                self.assertIn('first', run_lab_step(SERVICE, LAB, step)['verification']['message'])

    def test_reset_removes_the_table_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [f'table:{lab.TABLE}'])
        self.assertIsNone(self.fake.table)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
