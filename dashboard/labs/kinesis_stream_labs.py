"""Stream processing with Kinesis and Lambda: a stream with longer retention, a Lambda consumer whose event source
mapping batches records, bisects a failing batch, and sends what it cannot process to an SQS on-failure destination,
a poison record found from the failure metadata, a replay from a timestamp, an enhanced fan-out consumer, a shard
split for a hot stream, and an MSK cluster for comparison.

Floci 2.4.0 runs the event source mapping for real: it polls the shard, batches, invokes the function in a Lambda
container, retries, bisects a failing batch, and writes the failure record to the SQS destination. Replays, enhanced
fan-out registration, resharding and MSK (a Redpanda broker) are real too. What Floci does not do: it drops
TumblingWindowInSeconds and ParallelizationFactor from the mapping, so the lab explains them instead of running them.
"""

from __future__ import annotations

import base64
import io
import json
import time
import zipfile
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'kinesis'
LAB_KEY = 'stream-processing'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

STREAM = 'lab-clicks'
RETENTION_HOURS = 48
TABLE = 'lab-page-views'
FAILED_QUEUE = 'lab-clicks-failed'
FUNCTION = 'lab-click-counter'
FUNCTION_ROLE = 'lab-click-counter-role'
INLINE_POLICY = 'lab-stream'
EFO_CONSUMER = 'lab-clicks-analytics'
MSK_CLUSTER = 'lab-clicks-kafka'
KAFKA_VERSION = '3.6.0'
BATCH_SIZE = 50
BATCH_WINDOW = 2
RETRIES = 2

FUNCTION_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FUNCTION_ROLE}'
STREAM_ARN = f'arn:aws:kinesis:{REGION}:{ACCOUNT}:stream/{STREAM}'
TABLE_ARN = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{TABLE}'
QUEUE_ARN = f'arn:aws:sqs:{REGION}:{ACCOUNT}:{FAILED_QUEUE}'

EVENT_WAIT_SECONDS = 90
FLOCI = 'How Floci emulates this: '

# ── Data ─────────────────────────────────────────────────────────────────────

PAGES = ['home', 'cart', 'checkout']
POISON_ID = 1
POISON_DATA = b'{"event_id": 1, "user": "u1", "page": '  # cut off mid-record


def click(event_id: int) -> dict[str, Any]:
    return {'event_id': event_id, 'user': f'u{event_id % 6}', 'page': PAGES[event_id % 3]}


FIRST_BATCH = list(range(1, 25))
SECOND_BATCH = list(range(25, 31))


def record_data(event_id: int) -> bytes:
    return POISON_DATA if event_id == POISON_ID else json.dumps(click(event_id)).encode()


def expected_views(event_ids: list[int]) -> dict[str, int]:
    views: dict[str, int] = {}
    for event_id in event_ids:
        if event_id == POISON_ID:
            continue
        page = click(event_id)['page']
        views[page] = views.get(page, 0) + 1
    return dict(sorted(views.items()))


def count_records(records: list[dict[str, Any]]) -> tuple[dict[str, int], int]:
    """Page views from raw Kinesis records, and how many records were not valid clicks."""
    views: dict[str, int] = {}
    invalid = 0
    for record in records:
        try:
            page = json.loads(record['Data'])['page']
        except (ValueError, KeyError, TypeError):
            invalid += 1
            continue
        views[page] = views.get(page, 0) + 1
    return dict(sorted(views.items())), invalid


COUNTER_SOURCE = '''
import base64
import json
import os

import boto3

dynamodb = boto3.client('dynamodb')
TABLE = os.environ['TABLE']


def handler(event, context):
    # Validate the whole batch before writing anything, so a bad record fails the batch cleanly.
    clicks = []
    for record in event['Records']:
        data = base64.b64decode(record['kinesis']['data'])
        try:
            click = json.loads(data)
            clicks.append((int(click['event_id']), click['page']))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Bad record {record['kinesis']['sequenceNumber']}: {exc}")
    counted = 0
    for event_id, page in clicks:
        # Lambda delivers records at least once, and a retried batch repeats records that already counted.
        # Mark the event as seen and add the view in one transaction, so each click counts exactly once.
        try:
            dynamodb.transact_write_items(TransactItems=[
                {'Put': {'TableName': TABLE, 'Item': {'page': {'S': f'event#{event_id}'}},
                         'ConditionExpression': 'attribute_not_exists(page)'}},
                {'Update': {'TableName': TABLE, 'Key': {'page': {'S': page}}, 'UpdateExpression': 'ADD #views :n',
                            'ExpressionAttributeNames': {'#views': 'views'},  # VIEWS is a reserved word
                            'ExpressionAttributeValues': {':n': {'N': '1'}}}},
            ])
            counted += 1
        except dynamodb.exceptions.TransactionCanceledException:
            pass  # already counted
    return {'counted': counted, 'duplicates': len(clicks) - counted}
'''


def _counter_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('index.py', COUNTER_SOURCE)
    return buffer.getvalue()


TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
FUNCTION_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': ['kinesis:GetRecords', 'kinesis:GetShardIterator', 'kinesis:DescribeStream',
                                   'kinesis:DescribeStreamSummary', 'kinesis:ListShards', 'kinesis:ListStreams'],
     'Resource': STREAM_ARN},
    {'Effect': 'Allow', 'Action': ['dynamodb:PutItem', 'dynamodb:UpdateItem'], 'Resource': TABLE_ARN},
    {'Effect': 'Allow', 'Action': 'sqs:SendMessage', 'Resource': QUEUE_ARN},
    {'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
     'Resource': '*'},
]}
MAPPING = {
    'StartingPosition': 'TRIM_HORIZON',
    'BatchSize': BATCH_SIZE,
    'MaximumBatchingWindowInSeconds': BATCH_WINDOW,
    'BisectBatchOnFunctionError': True,
    'MaximumRetryAttempts': RETRIES,
    'MaximumRecordAgeInSeconds': 3600,
    'DestinationConfig': {'OnFailure': {'Destination': QUEUE_ARN}},
}

# ── Commands ─────────────────────────────────────────────────────────────────

STREAM_COMMAND = (
    f'aws kinesis create-stream --stream-name {STREAM} --shard-count 1\n'
    f'aws kinesis increase-stream-retention-period --stream-name {STREAM} --retention-period-hours {RETENTION_HOURS}\n'
    f'aws dynamodb create-table --table-name {TABLE} --attribute-definitions AttributeName=page,AttributeType=S '
    '--key-schema AttributeName=page,KeyType=HASH --billing-mode PAY_PER_REQUEST\n'
    f'aws sqs create-queue --queue-name {FAILED_QUEUE}'
)
CONSUMER_COMMAND = (
    f'aws iam create-role --role-name {FUNCTION_ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {FUNCTION_ROLE} --policy-name {INLINE_POLICY} '
    '--policy-document file://counter-policy.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime python3.12 --handler index.handler '
    f'--role {FUNCTION_ROLE_ARN} --zip-file fileb://counter.zip --environment Variables={{TABLE={TABLE}}}\n'
    f'aws lambda create-event-source-mapping --function-name {FUNCTION} --event-source-arn {STREAM_ARN} '
    f'--starting-position TRIM_HORIZON --batch-size {BATCH_SIZE} --maximum-batching-window-in-seconds {BATCH_WINDOW} '
    f'--bisect-batch-on-function-error --maximum-retry-attempts {RETRIES} --maximum-record-age-in-seconds 3600 '
    f'--destination-config OnFailure={{Destination={QUEUE_ARN}}}'
)
PRODUCE_COMMAND = (
    f'aws kinesis put-records --stream-name {STREAM} --records file://clicks.json   # 24 clicks, one cut off\n'
    f'aws dynamodb scan --table-name {TABLE}\n'
    f'aws sqs receive-message --queue-url http://localhost:4566/{ACCOUNT}/{FAILED_QUEUE}'
)
POISON_COMMAND = (
    f'aws sqs receive-message --queue-url http://localhost:4566/{ACCOUNT}/{FAILED_QUEUE}   # KinesisBatchInfo\n'
    f'aws kinesis get-shard-iterator --stream-name {STREAM} --shard-id <shardId> '
    '--shard-iterator-type AT_SEQUENCE_NUMBER --starting-sequence-number <startSequenceNumber>\n'
    'aws kinesis get-records --shard-iterator <iterator> --limit 1'
)
REPLAY_COMMAND = (
    f'aws kinesis get-shard-iterator --stream-name {STREAM} --shard-id shardId-000000000000 '
    '--shard-iterator-type AT_TIMESTAMP --timestamp <time-of-the-first-click>\n'
    'aws kinesis get-records --shard-iterator <iterator>'
)
EFO_COMMAND = (
    f'aws kinesis register-stream-consumer --stream-arn {STREAM_ARN} --consumer-name {EFO_CONSUMER}\n'
    f'aws kinesis describe-stream-consumer --stream-arn {STREAM_ARN} --consumer-name {EFO_CONSUMER}\n'
    f'aws kinesis list-stream-consumers --stream-arn {STREAM_ARN}'
)
RESHARD_COMMAND = (
    f'aws kinesis split-shard --stream-name {STREAM} --shard-to-split shardId-000000000000 '
    '--new-starting-hash-key 170141183460469231731687303715884105728\n'
    f'aws kinesis list-shards --stream-name {STREAM}\n'
    f'aws kinesis put-records --stream-name {STREAM} --records file://more-clicks.json'
)
MSK_COMMAND = (
    f'aws kafka create-cluster --cluster-name {MSK_CLUSTER} --kafka-version {KAFKA_VERSION} '
    '--number-of-broker-nodes 2 --broker-node-group-info file://brokers.json\n'
    'aws kafka list-clusters\n'
    'aws kafka get-bootstrap-brokers --cluster-arn <cluster-arn>'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Process a click stream with Kinesis and Lambda: batching, poison records, replay, fan-out, and resharding',
    'description': (
        'Create a Kinesis stream with longer retention, count page views with a Lambda consumer whose event source '
        'mapping batches records and isolates a bad one, find the poison record from its failure metadata, replay '
        'the stream from a timestamp, add an enhanced fan-out consumer, split a hot shard, and compare with an MSK '
        'cluster.'
    ),
    'steps': [
        {
            'key': 'create-stream',
            'title': f'Create the {STREAM} stream with {RETENTION_HOURS}-hour retention, a counts table, and a '
                     'failure queue',
            'command': STREAM_COMMAND,
            'explanation': (
                'A Kinesis data stream is an ordered log split into shards. Each shard takes 1 MB/s or 1,000 records/s '
                'in and gives 2 MB/s out, and records with the same partition key always land in the same shard, in '
                'order. Retention is 24 hours by default and can go up to 365 days; longer retention is what makes '
                f'a replay possible. Provisioned mode (one shard here) is cheaper for steady load; on-demand scales '
                f'shards for you. {TABLE} will hold page views, and {FAILED_QUEUE} the batches the consumer gives up on. '
                + FLOCI + 'the stream, retention, table and queue behave as on AWS.'
            ),
        },
        {
            'key': 'consumer-function',
            'title': f'Attach the {FUNCTION} function with an event source mapping that bisects failing batches',
            'command': CONSUMER_COMMAND,
            'explanation': (
                'Lambda polls each shard and invokes the function with a batch of up to '
                f'{BATCH_SIZE} records, waiting up to {BATCH_WINDOW} seconds to fill it (the batching window trades '
                'latency for fewer invocations). If the function fails, the whole batch is retried and the shard '
                'waits, so one bad record can block a shard until it expires. The mapping limits that: '
                f'BisectBatchOnFunctionError splits a failing batch in half and retries each half, {RETRIES} retries '
                'per batch, records older than an hour are skipped, and what still fails goes to the SQS on-failure '
                'destination as metadata (shard and sequence numbers, not the data). Delivery is at least once, and a '
                'retried batch repeats records that already counted, so the function marks each event as seen and '
                'adds its view in one DynamoDB transaction: a repeated click is skipped. ReportBatchItemFailures '
                'is the alternative: the function returns the first failed sequence number. TumblingWindowInSeconds '
                'keeps state across invocations for windowed sums, and ParallelizationFactor runs up to ten batches '
                'per shard at once. '
                + FLOCI + 'Floci polls, batches, retries, bisects and writes to the destination for real, in a real '
                'Lambda container. It drops TumblingWindowInSeconds and ParallelizationFactor from the mapping.'
            ),
        },
        {
            'key': 'produce-clicks',
            'title': f'Send {len(FIRST_BATCH)} clicks, one of them cut off, and watch the counts',
            'command': PRODUCE_COMMAND,
            'explanation': (
                f'PutRecords sends the clicks in one call, with the user as partition key. The first click is cut '
                'off mid-record. The first batch fails, is split, and the half with the bad record is retried until '
                'that record is alone; each good click is counted once, and the record that still fails after its '
                'retries goes to '
                f'{FAILED_QUEUE}. The counts table ends at {expected_views(FIRST_BATCH)}. Check PutRecords\' '
                'FailedRecordCount too: a throttled record in a successful call has to be resent by the producer. '
                + FLOCI + 'the bisecting and the destination message are real.'
            ),
        },
        {
            'key': 'find-poison-record',
            'title': 'Find the poison record from the failure metadata',
            'command': POISON_COMMAND,
            'explanation': (
                'The destination message says why the batch failed (RetryAttemptsExhausted) and where it is: '
                'KinesisBatchInfo gives the shard, the first and last sequence numbers, and the batch size. An '
                'AT_SEQUENCE_NUMBER iterator reads exactly that record back from the stream, while it is still '
                'within retention. From there the record can be fixed and written again, or kept for the producer '
                'team. '
                + FLOCI + 'the message carries the real shard and sequence numbers.'
            ),
        },
        {
            'key': 'replay-from-timestamp',
            'title': 'Replay the stream from a timestamp and recount',
            'command': REPLAY_COMMAND,
            'explanation': (
                'Consumers do not delete records; each one keeps its own position. An AT_TIMESTAMP iterator starts at '
                'the first record written at or after a time, which is how a new consumer backfills or a fixed '
                'consumer reprocesses after a bug. Recounting the replayed records gives the same page views as the '
                'table, plus the one invalid record. Kinesis Client Library consumers keep their position in a '
                'DynamoDB lease table; Lambda keeps it for you. '
                + FLOCI + 'AT_TIMESTAMP and AT_SEQUENCE_NUMBER iterators work as on AWS.'
            ),
        },
        {
            'key': 'enhanced-fan-out',
            'title': f'Register the {EFO_CONSUMER} enhanced fan-out consumer',
            'command': EFO_COMMAND,
            'explanation': (
                'Without enhanced fan-out, every consumer of a shard shares its 2 MB/s and its five GetRecords calls '
                'a second, so adding a third consumer slows the others. A registered consumer gets its own 2 MB/s '
                'per shard, pushed over HTTP/2 with SubscribeToShard, with about 70 ms latency. It costs extra per '
                'consumer-shard hour and per GB, so use it when several applications read the same stream or latency '
                'matters. A Lambda mapping uses it when its EventSourceArn is the consumer ARN. '
                + FLOCI + 'registration, the ACTIVE status, and the consumer list are real.'
            ),
        },
        {
            'key': 'reshard',
            'title': 'Split the shard for more throughput and keep counting',
            'command': RESHARD_COMMAND,
            'explanation': (
                'When a stream hits its shard limits (WriteProvisionedThroughputExceeded, rising IteratorAge), '
                'split the busy shard at a hash key or use UpdateShardCount. The parent shard closes and two child '
                'shards take over its hash range; consumers finish the parent before reading the children, so order '
                f'per partition key holds. After the split, {len(SECOND_BATCH)} more clicks are counted from the '
                'child shards. A hot partition key cannot be split: spread it with a better key. '
                + FLOCI + 'the split closes the parent and opens two children, and the mapping reads them.'
            ),
        },
        {
            'key': 'msk-cluster',
            'title': f'Create the {MSK_CLUSTER} MSK cluster and compare it with Kinesis',
            'command': MSK_COMMAND,
            'explanation': (
                'Amazon MSK runs Apache Kafka. Choose it when applications already speak Kafka, need Kafka Connect or '
                'Kafka Streams, keep data longer at lower cost, or need messages larger than Kinesis\' 1 MB. Kinesis '
                'needs no broker sizing and integrates with Lambda, Firehose and Managed Flink with less setup. Both '
                'keep order per partition (shard or Kafka partition), and Lambda can consume either. MSK Serverless '
                'removes broker sizing for Kafka too. '
                + FLOCI + 'Floci starts a Redpanda broker, which speaks the Kafka protocol, behind the cluster.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _stream() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('kinesis').describe_stream_summary(
        StreamName=STREAM)['StreamDescriptionSummary'])


def _shards() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('kinesis').list_shards(StreamName=STREAM)['Shards'], []) or []


def _open_shards() -> list[dict[str, Any]]:
    return [shard for shard in _shards() if 'EndingSequenceNumber' not in shard.get('SequenceNumberRange', {})]


def views() -> dict[str, int]:
    items = _safe_call(lambda: _client('dynamodb').scan(TableName=TABLE)['Items'], []) or []
    return dict(sorted((item['page']['S'], int(item['views']['N'])) for item in items if 'views' in item))


def _queue_url() -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=FAILED_QUEUE)['QueueUrl'])


def _function() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('lambda').get_function(FunctionName=FUNCTION)['Configuration'])


def _mapping() -> dict[str, Any] | None:
    mappings = _safe_call(lambda: _client('lambda').list_event_source_mappings(
        FunctionName=FUNCTION)['EventSourceMappings'], []) or []
    return next((item for item in mappings if item.get('EventSourceArn') == STREAM_ARN), None)


def _consumer() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('kinesis').describe_stream_consumer(
        StreamARN=STREAM_ARN, ConsumerName=EFO_CONSUMER)['ConsumerDescription'])


def _cluster() -> dict[str, Any] | None:
    clusters = _safe_call(lambda: _client('kafka').list_clusters(ClusterNameFilter=MSK_CLUSTER)['ClusterInfoList'],
                          []) or []
    return next((item for item in clusters if item.get('ClusterName') == MSK_CLUSTER), None)


def failure_records(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The KinesisBatchInfo of each on-failure destination message."""
    found = []
    for message in messages:
        try:
            body = json.loads(message['Body'])
        except (ValueError, KeyError):
            continue
        info = body.get('KinesisBatchInfo')
        if info:
            found.append({'condition': (body.get('requestContext') or {}).get('condition'), **info})
    return found


def _receive_failures(wait: float) -> list[dict[str, Any]]:
    url = _queue_url()
    if not url:
        return []
    messages: list[dict[str, Any]] = []

    def received() -> bool:
        messages.extend(_client('sqs').receive_message(QueueUrl=url, MaxNumberOfMessages=10,
                                                       WaitTimeSeconds=1).get('Messages', []))
        return bool(messages)
    _wait(received, wait, delay=0.5)
    return messages


def _decode(data: Any) -> str:
    if isinstance(data, (bytes, bytearray)):
        return data.decode(errors='replace')
    return base64.b64decode(data).decode(errors='replace') if isinstance(data, str) else str(data)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_stream() -> tuple[bool, str]:
    stream = _stream()
    if not stream:
        return False, f'Create {STREAM}.'
    if stream.get('RetentionPeriodHours') != RETENTION_HOURS:
        return False, f'Raise retention to {RETENTION_HOURS} hours.'
    if not _queue_url():
        return False, f'Create {FAILED_QUEUE}.'
    return True, f'{STREAM} keeps records {RETENTION_HOURS} hours; {TABLE} and {FAILED_QUEUE} are ready.'


def _check_consumer() -> tuple[bool, str]:
    mapping = _mapping()
    if not _function() or not mapping:
        return False, f'Create {FUNCTION} and its mapping from {STREAM}.'
    if not mapping.get('BisectBatchOnFunctionError') or mapping.get('MaximumRetryAttempts') != RETRIES:
        return False, 'Turn on bisecting and set the retries.'
    if (mapping.get('DestinationConfig') or {}).get('OnFailure', {}).get('Destination') != QUEUE_ARN:
        return False, f'Send failed batches to {FAILED_QUEUE}.'
    return True, f'{FUNCTION} reads {STREAM} in batches of {BATCH_SIZE}, bisects failures, and sends them to {FAILED_QUEUE}.'


def _check_produce() -> tuple[bool, str]:
    marker = _marked('produce-clicks') or {}
    if marker.get('views') != expected_views(FIRST_BATCH) or not marker.get('failures'):
        return False, f'Counts should reach {expected_views(FIRST_BATCH)} and one failure reach {FAILED_QUEUE}.'
    return True, f'Counted {expected_views(FIRST_BATCH)}; one record went to {FAILED_QUEUE}.'


def _check_poison() -> tuple[bool, str]:
    marker = _marked('find-poison-record') or {}
    if marker.get('data') != POISON_DATA.decode():
        return False, 'Read the failed record back from the stream with its sequence number.'
    return True, f'The failed record is click {POISON_ID}, cut off mid-record.'


def _check_replay() -> tuple[bool, str]:
    marker = _marked('replay-from-timestamp') or {}
    if marker.get('views') != expected_views(FIRST_BATCH) or marker.get('invalid') != 1:
        return False, 'Replay from the first click and recount.'
    return True, f'The replay recounts {marker["views"]} plus one invalid record, matching {TABLE}.'


def _check_efo() -> tuple[bool, str]:
    consumer = _consumer()
    if not consumer or consumer.get('ConsumerStatus') != 'ACTIVE':
        return False, f'Register {EFO_CONSUMER}.'
    return True, f'{EFO_CONSUMER} is ACTIVE with its own throughput.'


def _check_reshard() -> tuple[bool, str]:
    if len(_open_shards()) != 2:
        return False, 'Split the shard into two open child shards.'
    if views() != expected_views(FIRST_BATCH + SECOND_BATCH):
        return False, f'Counts should reach {expected_views(FIRST_BATCH + SECOND_BATCH)} after the split.'
    return True, f'Two open shards; the counts reached {expected_views(FIRST_BATCH + SECOND_BATCH)}.'


def _check_msk() -> tuple[bool, str]:
    cluster = _cluster()
    if not cluster:
        return False, f'Create {MSK_CLUSTER}.'
    if cluster.get('State') != 'ACTIVE':
        return False, f'{MSK_CLUSTER} is {cluster.get("State")}; run the step again in a minute.'
    marker = _marked('msk-cluster') or {}
    if not marker.get('bootstrap'):
        return False, 'Get the bootstrap brokers.'
    return True, f'{MSK_CLUSTER} is ACTIVE at {marker["bootstrap"]}.'


CHECKS = {
    'create-stream': _check_stream,
    'consumer-function': _check_consumer,
    'produce-clicks': _check_produce,
    'find-poison-record': _check_poison,
    'replay-from-timestamp': _check_replay,
    'enhanced-fan-out': _check_efo,
    'reshard': _check_reshard,
    'msk-cluster': _check_msk,
}


def _blocked(step: str, command: str, started: float, needs: str) -> dict[str, Any] | None:
    ready = {'stream': lambda: _stream() is not None, 'mapping': lambda: _mapping() is not None}[needs]()
    if ready:
        return None
    return _result(step, command, {}, False, 'Run the earlier steps first.', started)


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_stream() -> dict[str, Any]:
    started = time.perf_counter()
    kinesis, dynamodb, sqs = _client('kinesis'), _client('dynamodb'), _client('sqs')
    if not _stream():
        kinesis.create_stream(StreamName=STREAM, ShardCount=1)
        kinesis.get_waiter('stream_exists').wait(StreamName=STREAM, WaiterConfig={'Delay': 1, 'MaxAttempts': 30})
    if (_stream() or {}).get('RetentionPeriodHours', 24) < RETENTION_HOURS:
        kinesis.increase_stream_retention_period(StreamName=STREAM, RetentionPeriodHours=RETENTION_HOURS)
    if not _safe_call(lambda: dynamodb.describe_table(TableName=TABLE)):
        dynamodb.create_table(TableName=TABLE, BillingMode='PAY_PER_REQUEST',
                              AttributeDefinitions=[{'AttributeName': 'page', 'AttributeType': 'S'}],
                              KeySchema=[{'AttributeName': 'page', 'KeyType': 'HASH'}])
        dynamodb.get_waiter('table_exists').wait(TableName=TABLE, WaiterConfig={'Delay': 1, 'MaxAttempts': 30})
    sqs.create_queue(QueueName=FAILED_QUEUE, tags={'Lab': LAB_KEY})
    stream = _stream() or {}
    verified, message = _check_stream()
    return _result('create-stream', STREAM_COMMAND, {
        'Stream': STREAM, 'Shards': stream.get('OpenShardCount'), 'RetentionPeriodHours':
        stream.get('RetentionPeriodHours'), 'StreamMode': (stream.get('StreamModeDetails') or {}).get('StreamMode'),
        'Table': TABLE, 'FailedQueue': FAILED_QUEUE,
    }, verified, message, started)


def step_consumer_function() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('consumer-function', CONSUMER_COMMAND, started, 'stream')
    if blocked:
        return blocked
    iam, lam = _client('iam'), _client('lambda')
    try:
        iam.get_role(RoleName=FUNCTION_ROLE)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=FUNCTION_ROLE, AssumeRolePolicyDocument=json.dumps(TRUST),
                        Description='Counts page views from the click stream')
    iam.put_role_policy(RoleName=FUNCTION_ROLE, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(FUNCTION_POLICY))
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_counter_zip())
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12', Role=FUNCTION_ROLE_ARN,
                            Handler='index.handler', Code={'ZipFile': _counter_zip()}, Timeout=30,
                            Environment={'Variables': {'TABLE': TABLE}}, Tags={'Lab': LAB_KEY})
    _wait(lambda: (_function() or {}).get('State', 'Active') == 'Active'
          and (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)
    mapping = _mapping()
    if mapping:
        settings = {key: value for key, value in MAPPING.items() if key != 'StartingPosition'}
        lam.update_event_source_mapping(UUID=mapping['UUID'], **settings)
    else:
        lam.create_event_source_mapping(FunctionName=FUNCTION, EventSourceArn=STREAM_ARN, Enabled=True,
                                        TumblingWindowInSeconds=60, **MAPPING)
    mapping = _mapping() or {}
    verified, message = _check_consumer()
    return _result('consumer-function', CONSUMER_COMMAND, {
        'Function': FUNCTION, 'Mapping': {key: mapping.get(key) for key in (
            'UUID', 'State', 'BatchSize', 'MaximumBatchingWindowInSeconds', 'BisectBatchOnFunctionError',
            'MaximumRetryAttempts', 'MaximumRecordAgeInSeconds', 'DestinationConfig', 'TumblingWindowInSeconds')},
    }, verified, message, started)


def _put(event_ids: list[int]) -> dict[str, Any]:
    response = _client('kinesis').put_records(StreamName=STREAM, Records=[
        {'Data': record_data(event_id), 'PartitionKey': f'u{event_id % 6}'} for event_id in event_ids])
    return {'FailedRecordCount': response.get('FailedRecordCount'), 'Records': len(response.get('Records', []))}


def step_produce_clicks() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('produce-clicks', PRODUCE_COMMAND, started, 'mapping')
    if blocked:
        return blocked
    _marker('first-click-at', datetime.now(timezone.utc).isoformat())
    put = _put(FIRST_BATCH)
    expected = expected_views(FIRST_BATCH)
    _wait(lambda: views() == expected, EVENT_WAIT_SECONDS, delay=2)
    messages = _receive_failures(EVENT_WAIT_SECONDS / 2)
    failures = failure_records(messages)
    _marker('produce-clicks', {'views': views(), 'failures': failures})
    if failures:
        _marker('failure', failures[0])
    verified, message = _check_produce()
    return _result('produce-clicks', PRODUCE_COMMAND, {
        'PutRecords': put, 'PageViews': views(), 'Expected': expected, 'Failures': failures,
    }, verified, message, started)


def step_find_poison_record() -> dict[str, Any]:
    started = time.perf_counter()
    failure = _marked('failure') or {}
    if not failure.get('startSequenceNumber'):
        return _result('find-poison-record', POISON_COMMAND, {}, False, 'Send the clicks first.', started)
    kinesis = _client('kinesis')
    iterator = kinesis.get_shard_iterator(StreamName=STREAM, ShardId=failure['shardId'],
                                          ShardIteratorType='AT_SEQUENCE_NUMBER',
                                          StartingSequenceNumber=failure['startSequenceNumber'])['ShardIterator']
    records = kinesis.get_records(ShardIterator=iterator, Limit=max(1, int(failure.get('batchSize') or 1)))['Records']
    data = _decode(records[0]['Data']) if records else None
    _marker('find-poison-record', {'data': data})
    verified, message = _check_poison()
    return _result('find-poison-record', POISON_COMMAND, {
        'KinesisBatchInfo': failure, 'Record': {'SequenceNumber': records[0]['SequenceNumber'],
                                                'PartitionKey': records[0]['PartitionKey'], 'Data': data}
        if records else None,
    }, verified, message, started)


def step_replay_from_timestamp() -> dict[str, Any]:
    started = time.perf_counter()
    first = _marked('first-click-at')
    if not first:
        return _result('replay-from-timestamp', REPLAY_COMMAND, {}, False, 'Send the clicks first.', started)
    kinesis = _client('kinesis')
    shard = min(_shards(), key=lambda item: item['ShardId'])['ShardId']
    iterator = kinesis.get_shard_iterator(StreamName=STREAM, ShardId=shard, ShardIteratorType='AT_TIMESTAMP',
                                          Timestamp=datetime.fromisoformat(first))['ShardIterator']
    records: list[dict[str, Any]] = []
    for _ in range(10):
        page = kinesis.get_records(ShardIterator=iterator, Limit=1000)
        records.extend(page['Records'])
        iterator = page.get('NextShardIterator')
        if not iterator or not page['Records'] or len(records) >= len(FIRST_BATCH):
            break
    replayed, invalid = count_records(records[:len(FIRST_BATCH)])
    _marker('replay-from-timestamp', {'views': replayed, 'invalid': invalid})
    verified, message = _check_replay()
    return _result('replay-from-timestamp', REPLAY_COMMAND, {
        'From': first, 'Records': len(records), 'Recounted': replayed, 'Invalid': invalid, 'Table': views(),
    }, verified, message, started)


def step_enhanced_fan_out() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('enhanced-fan-out', EFO_COMMAND, started, 'stream')
    if blocked:
        return blocked
    kinesis = _client('kinesis')
    if not _consumer():
        kinesis.register_stream_consumer(StreamARN=STREAM_ARN, ConsumerName=EFO_CONSUMER)
    _wait(lambda: (_consumer() or {}).get('ConsumerStatus') == 'ACTIVE', 30)
    consumers = kinesis.list_stream_consumers(StreamARN=STREAM_ARN).get('Consumers', [])
    verified, message = _check_efo()
    return _result('enhanced-fan-out', EFO_COMMAND, {
        'Consumer': _consumer(), 'Consumers': [item.get('ConsumerName') for item in consumers],
    }, verified, message, started)


def step_reshard() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('reshard', RESHARD_COMMAND, started, 'mapping')
    if blocked:
        return blocked
    kinesis = _client('kinesis')
    if len(_open_shards()) == 1:
        shard = _open_shards()[0]
        hash_range = shard['HashKeyRange']
        middle = (int(hash_range['StartingHashKey']) + int(hash_range['EndingHashKey'])) // 2 + 1
        kinesis.split_shard(StreamName=STREAM, ShardToSplit=shard['ShardId'], NewStartingHashKey=str(middle))
        _wait(lambda: len(_open_shards()) == 2, 30)
    expected = expected_views(FIRST_BATCH + SECOND_BATCH)
    put = _put(SECOND_BATCH) if views() != expected else {'Skipped': 'already counted'}
    _wait(lambda: views() == expected, EVENT_WAIT_SECONDS, delay=2)
    verified, message = _check_reshard()
    return _result('reshard', RESHARD_COMMAND, {
        'Shards': [{'ShardId': item['ShardId'], 'ParentShardId': item.get('ParentShardId'),
                    'Open': 'EndingSequenceNumber' not in item.get('SequenceNumberRange', {})} for item in _shards()],
        'PutRecords': put, 'PageViews': views(), 'Expected': expected,
    }, verified, message, started)


def step_msk_cluster() -> dict[str, Any]:
    started = time.perf_counter()
    kafka = _client('kafka')
    if not _cluster():
        kafka.create_cluster(ClusterName=MSK_CLUSTER, KafkaVersion=KAFKA_VERSION, NumberOfBrokerNodes=2,
                             BrokerNodeGroupInfo={'InstanceType': 'kafka.m5.large',
                                                  'ClientSubnets': ['subnet-private-a', 'subnet-private-b'],
                                                  'SecurityGroups': ['sg-kafka'],
                                                  'StorageInfo': {'EbsStorageInfo': {'VolumeSize': 100}}},
                             Tags={'Lab': LAB_KEY})
    _wait(lambda: (_cluster() or {}).get('State') in ('ACTIVE', 'FAILED'), 120, delay=3)
    cluster = _cluster() or {}
    bootstrap = None
    if cluster.get('State') == 'ACTIVE':
        brokers = _safe_call(lambda: kafka.get_bootstrap_brokers(ClusterArn=cluster['ClusterArn']), {}) or {}
        bootstrap = brokers.get('BootstrapBrokerString') or brokers.get('BootstrapBrokerStringTls')
    _marker('msk-cluster', {'bootstrap': bootstrap})
    verified, message = _check_msk()
    return _result('msk-cluster', MSK_COMMAND, {
        'Cluster': {key: cluster.get(key) for key in ('ClusterName', 'State', 'CurrentBrokerSoftwareInfo',
                                                      'NumberOfBrokerNodes')},
        'BootstrapBrokers': bootstrap,
    }, verified, message, started)


RUNNERS = {
    'create-stream': step_create_stream,
    'consumer-function': step_consumer_function,
    'produce-clicks': step_produce_clicks,
    'find-poison-record': step_find_poison_record,
    'replay-from-timestamp': step_replay_from_timestamp,
    'enhanced-fan-out': step_enhanced_fan_out,
    'reshard': step_reshard,
    'msk-cluster': step_msk_cluster,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step])
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
    """Delete the MSK cluster, the mapping and function, the role, the stream, the table, and the queue."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'NonExistent' in code or code in (
                    'ResourceNotFoundException', 'ResourceInUseException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cluster = _cluster()
    if cluster:
        attempt(f'msk:{MSK_CLUSTER}', lambda: _client('kafka').delete_cluster(ClusterArn=cluster['ClusterArn']))
    lam = _client('lambda')
    mapping = _mapping()
    if mapping:
        attempt('event-source-mapping', lambda: lam.delete_event_source_mapping(UUID=mapping['UUID']))
    if _function():
        attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    iam = _client('iam')
    if _safe_call(lambda: iam.get_role(RoleName=FUNCTION_ROLE)):
        attempt(f'policy:{FUNCTION_ROLE}', lambda: iam.delete_role_policy(RoleName=FUNCTION_ROLE,
                                                                          PolicyName=INLINE_POLICY))
        attempt(f'role:{FUNCTION_ROLE}', lambda: iam.delete_role(RoleName=FUNCTION_ROLE))
    kinesis = _client('kinesis')
    if _consumer():
        attempt(f'consumer:{EFO_CONSUMER}', lambda: kinesis.deregister_stream_consumer(
            StreamARN=STREAM_ARN, ConsumerName=EFO_CONSUMER))
    if _stream():
        attempt(f'stream:{STREAM}', lambda: kinesis.delete_stream(StreamName=STREAM, EnforceConsumerDeletion=True))
        _wait(lambda: _stream() is None, 30)
    if _safe_call(lambda: _client('dynamodb').describe_table(TableName=TABLE)):
        attempt(f'table:{TABLE}', lambda: _client('dynamodb').delete_table(TableName=TABLE))
    url = _queue_url()
    if url:
        attempt(f'queue:{FAILED_QUEUE}', lambda: _client('sqs').delete_queue(QueueUrl=url))
    cache.delete_many([CACHE_PREFIX + key for key in [*STEP_KEYS, 'first-click-at', 'failure']])

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': f'aws kinesis delete-stream --stream-name {STREAM} --enforce-consumer-deletion  # and the rest',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verified': not errors,
        'verification': {
            'status': 'passed' if not errors else 'failed',
            'message': 'Stream processing lab resources removed.' if not errors else '; '.join(errors)[:500],
        },
    }
