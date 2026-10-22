export function normalizeDynamoTable(table: any) {
  const t = typeof table === 'string' ? { name: table } : table;
  const keys = t.KeySchema || t.key_schema || [];
  const ttl = t.TimeToLiveDescription || t.ttl;
  return {
    ...t,
    TableName: t.TableName || t.name,
    TableArn: t.TableArn || t.arn,
    TableStatus: t.TableStatus || t.status || 'Not reported',
    DeletionProtectionEnabled: t.DeletionProtectionEnabled ?? t.deletion_protection,
    TableClass: t.TableClassSummary?.TableClass || t.table_class,
    ItemCount: t.ItemCount ?? t.item_count,
    TableSizeBytes: t.TableSizeBytes ?? t.size_bytes,
    CreationDateTime: t.CreationDateTime || t.created,
    PartitionKey: t.PartitionKey || keys.find((key: any) => key.KeyType === 'HASH')?.AttributeName,
    SortKey: t.SortKey || keys.find((key: any) => key.KeyType === 'RANGE')?.AttributeName,
    KeySchema: keys,
    AttributeDefinitions: t.AttributeDefinitions || t.attribute_definitions || [],
    ProvisionedThroughput: t.ProvisionedThroughput || t.provisioned_throughput,
    // DescribeTable omits BillingModeSummary for tables that have always been provisioned.
    BillingMode: t.BillingModeSummary?.BillingMode || t.BillingMode || t.billing_mode?.BillingMode || ((t.ProvisionedThroughput || t.provisioned_throughput)?.ReadCapacityUnits ? 'PROVISIONED' : undefined),
    GlobalSecondaryIndexes: t.GlobalSecondaryIndexes || t.global_secondary_indexes || [],
    LocalSecondaryIndexes: t.LocalSecondaryIndexes || t.local_secondary_indexes || [],
    StreamSpecification: t.StreamSpecification || t.stream_specification,
    LatestStreamArn: t.LatestStreamArn || t.latest_stream_arn,
    PointInTimeRecovery: (t.ContinuousBackupsDescription || t.continuous_backups)?.PointInTimeRecoveryDescription?.PointInTimeRecoveryStatus === undefined
      ? undefined
      : (t.ContinuousBackupsDescription || t.continuous_backups).PointInTimeRecoveryDescription.PointInTimeRecoveryStatus === 'ENABLED',
    TimeToLive: ttl ? { Status: ttl.TimeToLiveStatus || ttl.Status, AttributeName: ttl.AttributeName } : undefined,
  };
}
