import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';

export function DynamoTableSchema({ table }: { table: any }) {
  const key = (name?: string) => {
    if (!name) return 'Not reported';
    const type = table.AttributeDefinitions?.find((attribute: any) => attribute.AttributeName === name)?.AttributeType;
    return `${name}${type ? ` (${({ S: 'String', N: 'Number', B: 'Binary' } as Record<string, string>)[type] || type})` : ''}`;
  };
  return <Container header={<Header variant="h3">Table schema and storage</Header>}>
    <KeyValuePairs columns={3} items={[
      { label: 'Partition key', value: key(table.PartitionKey) },
      { label: 'Sort key', value: table.SortKey ? key(table.SortKey) : 'Not reported' },
      { label: 'Capacity mode', value: table.BillingMode || 'Not reported' },
      { label: 'Global / local secondary indexes', value: `${table.GlobalSecondaryIndexes.length} / ${table.LocalSecondaryIndexes.length}` },
      { label: 'Time to Live', value: `${table.TimeToLive?.Status || 'Not reported'}${table.TimeToLive?.AttributeName ? ` · ${table.TimeToLive.AttributeName}` : ''}` },
      { label: 'DynamoDB Streams', value: table.StreamSpecification?.StreamEnabled === true ? table.StreamSpecification.StreamViewType || 'Enabled' : table.StreamSpecification?.StreamEnabled === false ? 'Disabled' : 'Not reported' },
    ]} />
  </Container>;
}
