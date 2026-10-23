"""Data Firehose inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def firehose_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    firehose = factory.client('firehose')
    operations = set(firehose.meta.service_model.operation_names)
    stream_names = _safe_value(lambda: core._paginate(firehose, 'list_delivery_streams', 'DeliveryStreamNames'), [])

    def stream_tags(name: str) -> list[dict[str, Any]]:
        tags: list[dict[str, Any]] = []
        exclusive_start_key = None

        while True:
            kwargs = {'DeliveryStreamName': name, 'Limit': 50}
            if exclusive_start_key:
                kwargs['ExclusiveStartTagKey'] = exclusive_start_key
            response = _safe_value(lambda kwargs=kwargs: firehose.list_tags_for_delivery_stream(**kwargs), {})
            tags.extend(response.get('Tags', []) if isinstance(response, dict) else [])
            if not isinstance(response, dict) or not response.get('HasMoreTags') or not response.get('Tags'):
                break
            exclusive_start_key = response.get('Tags', [])[-1].get('Key')

        return tags

    def stream_detail(name: str) -> dict[str, Any]:
        response = _safe_value(
            lambda: firehose.describe_delivery_stream(DeliveryStreamName=name).get('DeliveryStreamDescription', {}),
            {},
        )
        destinations = response.get('Destinations', []) if isinstance(response, dict) else []

        return {
            'name': name,
            'arn': response.get('DeliveryStreamARN'),
            'status': response.get('DeliveryStreamStatus'),
            'type': response.get('DeliveryStreamType'),
            'version_id': response.get('VersionId'),
            'created': response.get('CreateTimestamp'),
            'updated': response.get('LastUpdateTimestamp'),
            'source': response.get('Source'),
            'delivery_stream_encryption_configuration': response.get('DeliveryStreamEncryptionConfiguration'),
            'failure_description': response.get('FailureDescription'),
            'has_more_destinations': response.get('HasMoreDestinations'),
            'destination_count': len(destinations),
            'destinations': destinations,
            'tags': stream_tags(name),
        }

    streams = [stream_detail(name) for name in stream_names]
    destinations = [
        {
            'name': destination.get('DestinationId'),
            'delivery_stream_name': stream.get('name'),
            'destination_id': destination.get('DestinationId'),
            's3_destination_description': destination.get('S3DestinationDescription'),
            'extended_s3_destination_description': destination.get('ExtendedS3DestinationDescription'),
            'redshift_destination_description': destination.get('RedshiftDestinationDescription'),
            'elasticsearch_destination_description': destination.get('ElasticsearchDestinationDescription'),
            'amazonopensearchservice_destination_description': destination.get('AmazonopensearchserviceDestinationDescription'),
            'splunk_destination_description': destination.get('SplunkDestinationDescription'),
            'http_endpoint_destination_description': destination.get('HttpEndpointDestinationDescription'),
            'snowflake_destination_description': destination.get('SnowflakeDestinationDescription'),
            'iceberg_destination_description': destination.get('IcebergDestinationDescription'),
        }
        for stream in streams
        for destination in stream.get('destinations', [])
    ]

    return {
        'summary': {
            'delivery_streams': len(streams),
            'active_streams': sum(1 for stream in streams if stream.get('status') == 'ACTIVE'),
            'destinations': len(destinations),
            'tagged_streams': sum(1 for stream in streams if stream.get('tags')),
            'extended_s3_destinations': sum(
                1 for destination in destinations
                if destination.get('extended_s3_destination_description')
            ),
            'available_sdk_operations': len(operations),
        },
        'delivery_streams': streams,
        'destinations': destinations,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateDeliveryStream',
                'DescribeDeliveryStream',
                'ListDeliveryStreams',
                'DeleteDeliveryStream',
                'PutRecord',
                'PutRecordBatch',
                'UpdateDestination',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            "Records are buffered and delivered when the stream's buffering hints are reached (size or interval, 5 MiB / 300 seconds by default), as in AWS. FLOCI_SERVICES_FIREHOSE_FLUSH_RECORD_COUNT makes Floci flush after a fixed number of records instead.",
            'A stream with an S3 destination delivers to its bucket. Floci also accepts a stream with no destination, which AWS rejects, and delivers its records to the floci-firehose-results bucket.',
            'Floci adds a newline after each record that does not end with one; AWS writes record bytes exactly as sent, with no separator.',
        ],
    }


def list_firehose_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    firehose = factory.client('firehose')
    stream_names = _safe_value(lambda: core._paginate(firehose, 'list_delivery_streams', 'DeliveryStreamNames'), [])
    resources: list[dict[str, Any]] = []

    for name in stream_names:
        description = _safe_value(
            lambda name=name: firehose.describe_delivery_stream(DeliveryStreamName=name).get('DeliveryStreamDescription', {}),
            {},
        )
        resources.append({
            'type': 'delivery_stream',
            'name': name,
            'arn': description.get('DeliveryStreamARN'),
            'status': description.get('DeliveryStreamStatus'),
            'stream_type': description.get('DeliveryStreamType'),
        })
        resources.extend(
            {
                'type': 'destination',
                'delivery_stream': name,
                'id': destination.get('DestinationId'),
            }
            for destination in description.get('Destinations', [])
        )

    return resources
