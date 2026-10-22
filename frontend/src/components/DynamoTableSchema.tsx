import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';

export const capacityModeLabel = (mode?: string) => (mode === 'PAY_PER_REQUEST' ? 'On-demand' : mode === 'PROVISIONED' ? 'Provisioned' : '-');

export function DynamoTableSchema({ table }: { table: any }) {
  const key = (name?: string) => {
    if (!name) return 'Not reported';
    const type = table.AttributeDefinitions?.find((attribute: any) => attribute.AttributeName === name)?.AttributeType;
    return `${name}${type ? ` (${({ S: 'String', N: 'Number', B: 'Binary' } as Record<string, string>)[type] || type})` : ''}`;
  };
  return <Container header={<Header variant="h3">Table schema and storage</Header>}>
    <KeyValuePairs columns={3} items={[
      { label: 'Partition key', value: key(table.PartitionKey) },
      { label: 'Sort key', value: table.SortKey ? key(table.SortKey) : '-' },
      { label: 'Capacity mode', value: capacityModeLabel(table.BillingMode) },
      { label: 'Global / local secondary indexes', value: `${table.GlobalSecondaryIndexes.length} / ${table.LocalSecondaryIndexes.length}` },
      { label: 'Time to Live', value: `${table.TimeToLive?.Status || '-'}${table.TimeToLive?.AttributeName ? ` · ${table.TimeToLive.AttributeName}` : ''}` },
      // DescribeTable omits StreamSpecification when streams were never enabled.
      { label: 'DynamoDB stream', value: table.StreamSpecification?.StreamEnabled ? `On · ${table.StreamSpecification.StreamViewType}` : 'Off' },
    ]} />
  </Container>;
}
