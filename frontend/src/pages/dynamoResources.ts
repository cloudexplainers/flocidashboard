export function normalizeDynamoTable(table: any) {
  const t = typeof table === 'string' ? { name: table } : table;
  const keys = t.KeySchema || t.key_schema || [];
  const ttl = t.TimeToLiveDescription || t.ttl;
  return {
    ...t,
    TableName: t.TableName || t.name,
    TableArn: t.TableArn || t.arn,
    TableStatus: t.TableStatus || t.status || 'Not reported',
    ItemCount: t.ItemCount ?? t.item_count,
    TableSizeBytes: t.TableSizeBytes ?? t.size_bytes,
    CreationDateTime: t.CreationDateTime || t.created || 'Not reported',
    PartitionKey: t.PartitionKey || keys.find((key: any) => key.KeyType === 'HASH')?.AttributeName,
    SortKey: t.SortKey || keys.find((key: any) => key.KeyType === 'RANGE')?.AttributeName,
    KeySchema: keys,
    AttributeDefinitions: t.AttributeDefinitions || t.attribute_definitions || [],
    ProvisionedThroughput: t.ProvisionedThroughput || t.provisioned_throughput,
    BillingMode: t.BillingModeSummary?.BillingMode || t.BillingMode || t.billing_mode?.BillingMode,
    GlobalSecondaryIndexes: t.GlobalSecondaryIndexes || t.global_secondary_indexes || [],
    LocalSecondaryIndexes: t.LocalSecondaryIndexes || t.local_secondary_indexes || [],
    StreamSpecification: t.StreamSpecification || t.stream_specification,
    LatestStreamArn: t.LatestStreamArn || t.latest_stream_arn,
    PointInTimeRecovery: t.ContinuousBackupsDescription?.PointInTimeRecoveryDescription?.PointInTimeRecoveryStatus === 'ENABLED',
    TimeToLive: ttl ? { Status: ttl.TimeToLiveStatus || ttl.Status, AttributeName: ttl.AttributeName } : undefined,
  };
}
