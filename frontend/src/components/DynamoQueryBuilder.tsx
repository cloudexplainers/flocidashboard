import { useEffect, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import Checkbox from '@cloudscape-design/components/checkbox';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';

/** A Query request body for /api/dynamodb/tables/<table>/query/. */
export interface DynamoQueryRequest {
  key_condition_expression: string;
  expression_attribute_names: Record<string, string>;
  expression_attribute_values: Record<string, string | number>;
  index_name?: string;
  scan_index_forward: boolean;
}

/** What the items view loads: a Scan (of the table or an index) or a Query. */
export type DynamoItemsRequest = { mode: 'scan'; index_name?: string } | ({ mode: 'query' } & DynamoQueryRequest);

interface KeySchemaElement { AttributeName: string; KeyType: string }
interface IndexLike { IndexName: string; KeySchema?: KeySchemaElement[] }
interface TableLike {
  TableName: string;
  PartitionKey?: string;
  SortKey?: string;
  AttributeDefinitions?: { AttributeName: string; AttributeType: string }[];
  GlobalSecondaryIndexes?: IndexLike[];
  LocalSecondaryIndexes?: IndexLike[];
}

const SORT_CONDITIONS = [
  { value: '=', label: 'Equal to' },
  { value: '<', label: 'Less than' },
  { value: '<=', label: 'Less than or equal to' },
  { value: '>', label: 'Greater than' },
  { value: '>=', label: 'Greater than or equal to' },
  { value: 'between', label: 'Between' },
  { value: 'begins_with', label: 'Begins with' },
];

/** Builds a Query KeyConditionExpression from key values, as the console's "Scan or query items" panel does. */
export function buildDynamoQuery(
  partitionKey: { name: string; type?: string; value: string },
  sortKey: { name: string; type?: string; condition: string; value: string; value2: string } | null,
  indexName: string,
  descending: boolean,
): DynamoQueryRequest {
  const typed = (value: string, type?: string) => (type === 'N' ? Number(value) : value);
  const names: Record<string, string> = { '#pk': partitionKey.name };
  const values: Record<string, string | number> = { ':pk': typed(partitionKey.value, partitionKey.type) };
  let expression = '#pk = :pk';
  if (sortKey && sortKey.value !== '') {
    names['#sk'] = sortKey.name;
    values[':sk'] = typed(sortKey.value, sortKey.type);
    if (sortKey.condition === 'begins_with') expression += ' AND begins_with(#sk, :sk)';
    else if (sortKey.condition === 'between') {
      values[':sk2'] = typed(sortKey.value2, sortKey.type);
      expression += ' AND #sk BETWEEN :sk AND :sk2';
    } else expression += ` AND #sk ${sortKey.condition} :sk`;
  }
  return {
    key_condition_expression: expression,
    expression_attribute_names: names,
    expression_attribute_values: values,
    ...(indexName ? { index_name: indexName } : {}),
    scan_index_forward: !descending,
  };
}

export function DynamoQueryBuilder({ table, running, onRun }: {
  table: TableLike;
  running: boolean;
  onRun: (request: DynamoItemsRequest) => void;
}) {
  const [mode, setMode] = useState<'scan' | 'query'>('scan');
  const [indexName, setIndexName] = useState('');
  const [pkValue, setPkValue] = useState('');
  const [condition, setCondition] = useState('=');
  const [skValue, setSkValue] = useState('');
  const [skValue2, setSkValue2] = useState('');
  const [descending, setDescending] = useState(false);
  useEffect(() => { setMode('scan'); setIndexName(''); setPkValue(''); setSkValue(''); setSkValue2(''); setDescending(false); }, [table.TableName]);

  const indexes = [...(table.GlobalSecondaryIndexes || []), ...(table.LocalSecondaryIndexes || [])];
  const index = indexes.find(item => item.IndexName === indexName);
  const pkName = index ? index.KeySchema?.find(key => key.KeyType === 'HASH')?.AttributeName : table.PartitionKey;
  const skName = index ? index.KeySchema?.find(key => key.KeyType === 'RANGE')?.AttributeName : table.SortKey;
  const typeOf = (name?: string) => table.AttributeDefinitions?.find(attribute => attribute.AttributeName === name)?.AttributeType;
  const numberError = (value: string, name?: string) => (value !== '' && typeOf(name) === 'N' && Number.isNaN(Number(value)) ? 'Enter a number.' : undefined);
  const invalid = mode === 'query' && (!pkName || !pkValue || Boolean(numberError(pkValue, pkName)) || Boolean(numberError(skValue, skName))
    || (condition === 'between' && skValue !== '' && (skValue2 === '' || Boolean(numberError(skValue2, skName)))));

  const run = () => {
    if (mode === 'scan' || !pkName) return onRun({ mode: 'scan', ...(indexName ? { index_name: indexName } : {}) });
    onRun({ mode: 'query', ...buildDynamoQuery(
      { name: pkName, type: typeOf(pkName), value: pkValue },
      skName ? { name: skName, type: typeOf(skName), condition, value: skValue, value2: skValue2 } : null,
      indexName,
      descending,
    ) });
  };

  return (
    <Container header={<Header variant="h3">Scan or query items</Header>}>
      <SpaceBetween size="m">
        <SegmentedControl selectedId={mode} onChange={({ detail }) => setMode(detail.selectedId as 'scan' | 'query')}
          options={[{ id: 'scan', text: 'Scan' }, { id: 'query', text: 'Query' }]} />
        <FormField label="Select a table or index">
          <Select ariaLabel="Table or index" selectedOption={{ value: indexName, label: indexName ? `Index - ${indexName}` : `Table - ${table.TableName}` }}
            options={[{ value: '', label: `Table - ${table.TableName}` }, ...indexes.map(item => ({ value: item.IndexName, label: `Index - ${item.IndexName}` }))]}
            onChange={({ detail }) => setIndexName(detail.selectedOption.value || '')} />
        </FormField>
        {mode === 'query' && (
          <ColumnLayout columns={2}>
            <FormField label={`${pkName || 'Partition key'} (Partition key)`} errorText={numberError(pkValue, pkName)}>
              <Input ariaLabel="Partition key value" value={pkValue} placeholder="Enter partition key value" onChange={({ detail }) => setPkValue(detail.value)} />
            </FormField>
            {skName && (
              <FormField label={`${skName} (Sort key) - optional`} errorText={numberError(skValue, skName) || numberError(skValue2, skName)}>
                <SpaceBetween size="xs">
                  <Select ariaLabel="Sort key condition" selectedOption={SORT_CONDITIONS.find(item => item.value === condition) || null}
                    options={SORT_CONDITIONS.filter(item => item.value !== 'begins_with' || typeOf(skName) !== 'N')}
                    onChange={({ detail }) => setCondition(detail.selectedOption.value || '=')} />
                  <Input ariaLabel="Sort key value" value={skValue} placeholder="Enter sort key value" onChange={({ detail }) => setSkValue(detail.value)} />
                  {condition === 'between' && <Input ariaLabel="Sort key upper value" value={skValue2} placeholder="And" onChange={({ detail }) => setSkValue2(detail.value)} />}
                </SpaceBetween>
              </FormField>
            )}
            <Checkbox checked={descending} onChange={({ detail }) => setDescending(detail.checked)}>Sort descending</Checkbox>
          </ColumnLayout>
        )}
        <Button variant="primary" loading={running} disabled={invalid} onClick={run}>Run</Button>
      </SpaceBetween>
    </Container>
  );
}
