"""Kinesis inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def kinesis_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    kinesis = factory.client('kinesis')
    stream_names = _safe_value(lambda: core._paginate(kinesis, 'list_streams', 'StreamNames'), [])
    account_settings = _safe_value(lambda: kinesis.describe_account_settings(), {})
    limits = _safe_value(lambda: kinesis.describe_limits(), {})

    def stream_tags(name: str) -> list[dict[str, Any]]:
        tags: list[dict[str, Any]] = []
        exclusive_start_key = None

        while True:
            kwargs = {'StreamName': name, 'Limit': 50}
            if exclusive_start_key:
                kwargs['ExclusiveStartTagKey'] = exclusive_start_key
            response = _safe_value(lambda kwargs=kwargs: kinesis.list_tags_for_stream(**kwargs), {})
            tags.extend(response.get('Tags', []) if isinstance(response, dict) else [])
            if not isinstance(response, dict) or not response.get('HasMoreTags') or not response.get('Tags'):
                break
            exclusive_start_key = response.get('Tags', [])[-1].get('Key')

        return tags

    def consumer_detail(stream_arn: str, consumer: dict[str, Any]) -> dict[str, Any]:
        consumer_arn = consumer.get('ConsumerARN')
        detail = _safe_value(
            lambda: kinesis.describe_stream_consumer(StreamARN=stream_arn, ConsumerARN=consumer_arn).get('ConsumerDescription', {}),
            {},
        )
        return {
            'name': consumer.get('ConsumerName') or detail.get('ConsumerName'),
            'stream_arn': stream_arn,
            'consumer_arn': consumer_arn or detail.get('ConsumerARN'),
            'status': consumer.get('ConsumerStatus') or detail.get('ConsumerStatus'),
            'creation_timestamp': consumer.get('ConsumerCreationTimestamp') or detail.get('ConsumerCreationTimestamp'),
        }

    def stream_detail(name: str) -> dict[str, Any]:
        summary = _safe_value(
            lambda: kinesis.describe_stream_summary(StreamName=name).get('StreamDescriptionSummary', {}),
            {},
        )
        description = _safe_value(
            lambda: kinesis.describe_stream(StreamName=name).get('StreamDescription', {}),
            {},
        )
        stream_arn = summary.get('StreamARN') or description.get('StreamARN')
        shards = _safe_value(lambda: core._paginate(kinesis, 'list_shards', 'Shards', StreamName=name), description.get('Shards', []))
        consumers = _safe_value(lambda: core._paginate(kinesis, 'list_stream_consumers', 'Consumers', StreamARN=stream_arn), []) if stream_arn else []
        consumer_details = [consumer_detail(stream_arn, consumer) for consumer in consumers] if stream_arn else []

        return {
            'name': name,
            'arn': stream_arn,
            'status': summary.get('StreamStatus') or description.get('StreamStatus'),
            'mode': summary.get('StreamModeDetails') or description.get('StreamModeDetails'),
            'retention_period_hours': summary.get('RetentionPeriodHours') or description.get('RetentionPeriodHours'),
            'created': summary.get('StreamCreationTimestamp') or description.get('StreamCreationTimestamp'),
            'enhanced_monitoring': summary.get('EnhancedMonitoring') or description.get('EnhancedMonitoring'),
            'encryption_type': summary.get('EncryptionType') or description.get('EncryptionType'),
            'key_id': summary.get('KeyId') or description.get('KeyId'),
            'open_shard_count': summary.get('OpenShardCount'),
            'consumer_count': summary.get('ConsumerCount') or len(consumer_details),
            'shard_count': len(shards),
            'shards': shards,
            'consumers': consumer_details,
            'tags': stream_tags(name),
        }

    streams = [stream_detail(name) for name in stream_names]
    shards = [
        {
            'name': shard.get('ShardId'),
            'stream_name': stream.get('name'),
            'shard_id': shard.get('ShardId'),
            'parent_shard_id': shard.get('ParentShardId'),
            'adjacent_parent_shard_id': shard.get('AdjacentParentShardId'),
            'hash_key_range': shard.get('HashKeyRange'),
            'sequence_number_range': shard.get('SequenceNumberRange'),
        }
        for stream in streams
        for shard in stream.get('shards', [])
    ]
    consumers = [
        consumer
        for stream in streams
        for consumer in stream.get('consumers', [])
    ]

    return {
        'summary': {
            'streams': len(streams),
            'active_streams': sum(1 for stream in streams if stream.get('status') == 'ACTIVE'),
            'shards': len(shards),
            'consumers': len(consumers),
            'tagged_streams': sum(1 for stream in streams if stream.get('tags')),
        },
        'streams': streams,
        'shards': shards,
        'consumers': consumers,
        'account_settings': account_settings,
        'limits': limits,
        'supported_from_sdk': [
            'CreateStream',
            'DescribeStream',
            'DescribeStreamSummary',
            'ListStreams',
            'DeleteStream',
            'ListShards',
            'PutRecord',
            'PutRecords',
            'GetShardIterator',
            'GetRecords',
            'RegisterStreamConsumer',
            'ListStreamConsumers',
            'DescribeStreamConsumer',
            'DeregisterStreamConsumer',
            'AddTagsToStream',
            'ListTagsForStream',
            'RemoveTagsFromStream',
            'EnableEnhancedMonitoring',
            'DisableEnhancedMonitoring',
            'StartStreamEncryption',
            'StopStreamEncryption',
            'UpdateShardCount',
            'UpdateStreamMode',
        ],
        'notes': [
            'This page is inferred from the Kinesis AWS SDK API because service docs were not provided for this pass.',
            'Shard and consumer details are flattened from each stream for scanning.',
        ],
    }


def list_kinesis_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    kinesis = factory.client('kinesis')
    stream_names = _safe_value(lambda: core._paginate(kinesis, 'list_streams', 'StreamNames'), [])
    resources: list[dict[str, Any]] = []

    for name in stream_names:
        summary = _safe_value(
            lambda name=name: kinesis.describe_stream_summary(StreamName=name).get('StreamDescriptionSummary', {}),
            {},
        )
        stream_arn = summary.get('StreamARN')
        resources.append({
            'type': 'stream',
            'name': name,
            'arn': stream_arn,
            'status': summary.get('StreamStatus'),
            'open_shard_count': summary.get('OpenShardCount'),
        })
        resources.extend(
            {
                'type': 'shard',
                'stream': name,
                'id': shard.get('ShardId'),
            }
            for shard in _safe_value(
                lambda name=name: core._paginate(kinesis, 'list_shards', 'Shards', StreamName=name),
                [],
            )
        )
        if stream_arn:
            resources.extend(
                {
                    'type': 'consumer',
                    'stream': name,
                    'name': consumer.get('ConsumerName'),
                    'arn': consumer.get('ConsumerARN'),
                    'status': consumer.get('ConsumerStatus'),
                }
                for consumer in _safe_value(
                    lambda stream_arn=stream_arn: core._paginate(kinesis, 'list_stream_consumers', 'Consumers', StreamARN=stream_arn),
                    [],
                )
            )

    return resources
