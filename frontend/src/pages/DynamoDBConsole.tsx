import { CodeSnippet } from '../components/CodeSnippet';
import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import {
  fetchServiceInventory,
  executeServiceAction,
  fetchDynamoDbTableScan,
  executeDynamoDbPartiQL,
  putDynamoDbItem,
  deleteDynamoDbItem,
  updateDynamoDbTableCapacity,
  updateDynamoDbPitr,
  updateDynamoDbTtl,
} from '../api/client';

interface TableItem {
  TableName: string;
  TableArn?: string;
  TableStatus: string;
  ItemCount?: number;
  TableSizeBytes?: number;
  CreationDateTime?: string;
  PartitionKey?: string;
  SortKey?: string;
  BillingMode?: string;
  GlobalSecondaryIndexes?: any[];
  LocalSecondaryIndexes?: any[];
  StreamSpecification?: any;
  LatestStreamArn?: string;
  PointInTimeRecovery?: boolean;
  TimeToLive?: { Status: string; AttributeName?: string };
}

interface DynamoDBConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const DynamoDBConsole: React.FC<DynamoDBConsoleProps> = ({ activeTab, onTabChange }) => {
  const [tables, setTables] = useState<TableItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTables, setSelectedTables] = useState<TableItem[]>([]);
  const [tableFilter, setTableFilter] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'items');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Create Table Modal
  const [createTableOpen, setCreateTableOpen] = useState(false);
  const [tableName, setTableName] = useState('');
  const [partitionKeyName, setPartitionKeyName] = useState('id');
  const [partitionKeyType, setPartitionKeyType] = useState({ label: 'String (S)', value: 'S' });
  const [sortKeyName, setSortKeyName] = useState('');
  const [sortKeyType, setSortKeyType] = useState({ label: 'String (S)', value: 'S' });
  const [creatingTable, setCreatingTable] = useState(false);

  // Items Explorer for Active Table
  const [tableItems, setTableItems] = useState<any[]>([]);
  const [selectedItemRows, setSelectedItemRows] = useState<any[]>([]);
  const [loadingItems, setLoadingItems] = useState(false);

  // Create / Edit Item Modal
  const [itemModalOpen, setItemModalOpen] = useState(false);
  const [isEditingItem, setIsEditingItem] = useState(false);
  const [itemJson, setItemJson] = useState('{\n  "id": "item-101",\n  "name": "Sample Widget",\n  "price": 19.99,\n  "status": "ACTIVE"\n}');
  const [savingItem, setSavingItem] = useState(false);

  // PartiQL SQL Query Editor
  const [partiqlQuery, setPartiqlQuery] = useState('');
  const [partiqlResults, setPartiqlResults] = useState<any[]>([]);
  const [runningPartiql, setRunningPartiql] = useState(false);
  const [partiqlLatency, setPartiqlLatency] = useState<number | null>(null);

  // Capacity & Backups State
  const [billingMode, setBillingMode] = useState<'PAY_PER_REQUEST' | 'PROVISIONED'>('PAY_PER_REQUEST');
  const [readUnits, setReadUnits] = useState('5');
  const [writeUnits, setWriteUnits] = useState('5');
  const [pitrEnabled, setPitrEnabled] = useState(false);
  const [savingCapacity, setSavingCapacity] = useState(false);
  const [savingPitr, setSavingPitr] = useState(false);

  // TTL State
  const [ttlEnabled, setTtlEnabled] = useState(false);
  const [ttlAttribute, setTtlAttribute] = useState('expiresAt');
  const [savingTtl, setSavingTtl] = useState(false);

  const loadTables = async () => {
    setLoading(true);
    try {
      const data = await fetchServiceInventory('dynamodb');
      const list = (data.tables || data.TableNames || []).map((t: any) => ({
        TableName: typeof t === 'string' ? t : t.TableName || t.name,
        TableArn: t.TableArn || t.arn,
        TableStatus: t.TableStatus || 'ACTIVE',
        ItemCount: t.ItemCount ?? 0,
        TableSizeBytes: t.TableSizeBytes ?? 0,
        CreationDateTime: t.CreationDateTime || new Date().toISOString().split('T')[0],
        PartitionKey: t.PartitionKey || t.KeySchema?.find((k: any) => k.KeyType === 'HASH')?.AttributeName || 'id',
        SortKey: t.SortKey || t.KeySchema?.find((k: any) => k.KeyType === 'RANGE')?.AttributeName || undefined,
        BillingMode: t.BillingModeSummary?.BillingMode || t.BillingMode || 'PAY_PER_REQUEST',
        GlobalSecondaryIndexes: t.GlobalSecondaryIndexes || [],
        LocalSecondaryIndexes: t.LocalSecondaryIndexes || [],
        StreamSpecification: t.StreamSpecification,
        LatestStreamArn: t.LatestStreamArn,
        PointInTimeRecovery: t.ContinuousBackupsDescription?.PointInTimeRecoveryDescription?.PointInTimeRecoveryStatus === 'ENABLED',
        TimeToLive: t.TimeToLiveDescription || { Status: 'DISABLED', AttributeName: 'expiresAt' },
      }));
      setTables(list);
      if (list.length > 0 && selectedTables.length === 0) {
        setSelectedTables([list[0]]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTables();
  }, []);

  const activeTable = selectedTables[0] || null;

  const loadTableItems = async (tbl: TableItem) => {
    setLoadingItems(true);
    try {
      const res = await fetchDynamoDbTableScan(tbl.TableName);
      setTableItems(res.items || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingItems(false);
    }
  };

  useEffect(() => {
    if (activeTable) {
      loadTableItems(activeTable);
      setSelectedItemRows([]);
      setPartiqlQuery(`SELECT * FROM "${activeTable.TableName}"`);
      setPartiqlResults([]);
      setBillingMode((activeTable.BillingMode as any) || 'PAY_PER_REQUEST');
      setPitrEnabled(activeTable.PointInTimeRecovery ?? false);
      setTtlEnabled(activeTable.TimeToLive?.Status === 'ENABLED');
      setTtlAttribute(activeTable.TimeToLive?.AttributeName || 'expiresAt');
    } else {
      setTableItems([]);
    }
  }, [activeTable?.TableName]);

  const handleCreateTable = async () => {
    if (!tableName.trim() || !partitionKeyName.trim()) return;
    setCreatingTable(true);
    setActionMessage(null);
    try {
      const keySchema = [{ AttributeName: partitionKeyName.trim(), KeyType: 'HASH' }];
      const attrDefs = [{ AttributeName: partitionKeyName.trim(), AttributeType: partitionKeyType.value }];

      if (sortKeyName.trim()) {
        keySchema.push({ AttributeName: sortKeyName.trim(), KeyType: 'RANGE' });
        attrDefs.push({ AttributeName: sortKeyName.trim(), AttributeType: sortKeyType.value });
      }

      await executeServiceAction('dynamodb', 'create_table', {
        TableName: tableName.trim(),
        KeySchema: keySchema,
        AttributeDefinitions: attrDefs,
        BillingMode: 'PAY_PER_REQUEST',
      });
      setActionMessage({ type: 'success', text: `Table "${tableName.trim()}" created successfully.` });
      setCreateTableOpen(false);
      setTableName('');
      await loadTables();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create table' });
    } finally {
      setCreatingTable(false);
    }
  };

  const handleDeleteTable = async () => {
    if (!activeTable) return;
    try {
      await executeServiceAction('dynamodb', 'delete_table', { TableName: activeTable.TableName });
      setActionMessage({ type: 'success', text: `Table "${activeTable.TableName}" deleted.` });
      setSelectedTables([]);
      await loadTables();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete table' });
    }
  };

  const handleSaveItem = async () => {
    if (!activeTable) return;
    setSavingItem(true);
    try {
      const parsed = JSON.parse(itemJson);
      await putDynamoDbItem(activeTable.TableName, parsed);
      setActionMessage({ type: 'success', text: 'Item saved successfully.' });
      setItemModalOpen(false);
      await loadTableItems(activeTable);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to save item (must be valid JSON)' });
    } finally {
      setSavingItem(false);
    }
  };

  const handleDeleteItem = async () => {
    if (!activeTable || !selectedItemRows.length) return;
    const row = selectedItemRows[0];
    const pk = activeTable.PartitionKey || 'id';
    const keyObj: Record<string, any> = { [pk]: row[pk] };
    if (activeTable.SortKey && row[activeTable.SortKey]) {
      keyObj[activeTable.SortKey] = row[activeTable.SortKey];
    }
    try {
      await deleteDynamoDbItem(activeTable.TableName, keyObj);
      setActionMessage({ type: 'success', text: 'Item deleted.' });
      setSelectedItemRows([]);
      await loadTableItems(activeTable);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete item' });
    }
  };

  const handleRunPartiQL = async () => {
    if (!partiqlQuery.trim()) return;
    setRunningPartiql(true);
    const start = Date.now();
    try {
      const res = await executeDynamoDbPartiQL(partiqlQuery.trim());
      setPartiqlResults(res.items || []);
      setPartiqlLatency(Date.now() - start);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'PartiQL query failed' });
    } finally {
      setRunningPartiql(false);
    }
  };

  const handleSaveCapacity = async () => {
    if (!activeTable) return;
    setSavingCapacity(true);
    try {
      await updateDynamoDbTableCapacity(
        activeTable.TableName,
        billingMode,
        Number(readUnits) || 5,
        Number(writeUnits) || 5
      );
      setActionMessage({ type: 'success', text: `Table capacity updated to ${billingMode}.` });
      await loadTables();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update capacity' });
    } finally {
      setSavingCapacity(false);
    }
  };

  const handleTogglePitr = async () => {
    if (!activeTable) return;
    setSavingPitr(true);
    try {
      await updateDynamoDbPitr(activeTable.TableName, !pitrEnabled);
      setPitrEnabled(!pitrEnabled);
      setActionMessage({ type: 'success', text: `Point-in-time recovery ${!pitrEnabled ? 'enabled' : 'disabled'}.` });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update PITR' });
    } finally {
      setSavingPitr(false);
    }
  };

  const handleToggleTtl = async () => {
    if (!activeTable) return;
    setSavingTtl(true);
    try {
      await updateDynamoDbTtl(activeTable.TableName, ttlAttribute.trim(), !ttlEnabled);
      setTtlEnabled(!ttlEnabled);
      setActionMessage({ type: 'success', text: `TTL ${!ttlEnabled ? 'enabled' : 'disabled'}.` });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update TTL' });
    } finally {
      setSavingTtl(false);
    }
  };

  const filteredTables = tables.filter((t) =>
    t.TableName.toLowerCase().includes(tableFilter.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            counter={`(${tables.length})`}
            description="Fast, flexible NoSQL database service for single-digit millisecond performance at any scale."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadTables} loading={loading}>
                  Refresh
                </Button>
                <Button disabled={!activeTable} onClick={handleDeleteTable}>
                  Delete table
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateTableOpen(true)}>
                  Create table
                </Button>
              </SpaceBetween>
            }
          >
            Amazon DynamoDB
          </Header>
        }
      >
        {actionMessage && (
          <Box margin={{ bottom: 'm' }}>
            <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
              {actionMessage.text}
            </Alert>
          </Box>
        )}

        <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Total Tables</Box>
            <Box variant="h1" color="text-status-info">
              {tables.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active Region</Box>
            <Box variant="h2" color="text-status-info">
              us-east-1
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">DynamoDB Engine</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">Storage Engine Ready</StatusIndicator>
            </Box>
          </Box>
        </Grid>
      </Container>

      {/* Tables Table */}
      <Container
        header={
          <Header variant="h2" description="DynamoDB tables hosted on Floci local storage engine.">
            Tables ({tables.length})
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={tableFilter}
            filteringPlaceholder="Find tables by name..."
            onChange={({ detail }) => setTableFilter(detail.filteringText)}
          />

          <Table
            columnDefinitions={[
              {
                id: 'name',
                header: 'Table name',
                cell: (item) => (
                  <Button variant="inline-link" onClick={() => setSelectedTables([item])}>
                    <strong>{item.TableName}</strong>
                  </Button>
                ),
              },
              {
                id: 'status',
                header: 'Status',
                cell: (item) => (
                  <StatusIndicator type={item.TableStatus === 'ACTIVE' ? 'success' : 'in-progress'}>
                    {item.TableStatus}
                  </StatusIndicator>
                ),
                width: 130,
              },
              {
                id: 'partitionKey',
                header: 'Partition key',
                cell: (item) => <code>{item.PartitionKey} (HASH)</code>,
                width: 180,
              },
              {
                id: 'sortKey',
                header: 'Sort key',
                cell: (item) => (item.SortKey ? <code>{item.SortKey} (RANGE)</code> : '—'),
                width: 180,
              },
              {
                id: 'items',
                header: 'Item count',
                cell: (item) => item.ItemCount ?? 0,
                width: 120,
              },
            ]}
            items={filteredTables}
            selectionType="single"
            selectedItems={selectedTables}
            onSelectionChange={({ detail }) => setSelectedTables(detail.selectedItems)}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No DynamoDB tables found</b>
                <p>Create a table to start inserting items and testing queries.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {/* Active Table Details Inspector */}
      {activeTable && (
        <Container
          header={
            <Header variant="h2" description={`Inspecting table: ${activeTable.TableName}`}>
              Table: {activeTable.TableName}
            </Header>
          }
        >
          <Tabs
            activeTabId={selectedTabId}
            onChange={({ detail }) => {
              setSelectedTabId(detail.activeTabId);
              onTabChange?.(detail.activeTabId);
            }}
            tabs={[
              {
                label: `Explore Items (${tableItems.length})`,
                id: 'items',
                content: (
                  <SpaceBetween size="m">
                    <Box float="right">
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button
                          disabled={!selectedItemRows.length}
                          onClick={() => {
                            setItemJson(JSON.stringify(selectedItemRows[0], null, 2));
                            setIsEditingItem(true);
                            setItemModalOpen(true);
                          }}
                        >
                          Edit item
                        </Button>
                        <Button disabled={!selectedItemRows.length} onClick={handleDeleteItem}>
                          Delete item
                        </Button>
                        <Button
                          variant="primary"
                          iconName="add-plus"
                          onClick={() => {
                            setItemJson(
                              JSON.stringify(
                                {
                                  [activeTable.PartitionKey || 'id']: `item-${Date.now().toString().slice(-4)}`,
                                  name: 'New Item',
                                  status: 'ACTIVE',
                                },
                                null,
                                2
                              )
                            );
                            setIsEditingItem(false);
                            setItemModalOpen(true);
                          }}
                        >
                          Create item
                        </Button>
                      </SpaceBetween>
                    </Box>

                    <Table
                      columnDefinitions={[
                        {
                          id: 'pk',
                          sortingField: activeTable.PartitionKey || 'id',
                          header: `Partition Key (${activeTable.PartitionKey})`,
                          cell: (row) => <strong>{String(row[activeTable.PartitionKey || 'id'] ?? '—')}</strong>,
                        },
                        ...(activeTable.SortKey
                          ? [
                              {
                                id: 'sk',
                                sortingField: activeTable.SortKey,
                                header: `Sort Key (${activeTable.SortKey})`,
                                cell: (row: any) => String(row[activeTable.SortKey!] ?? '—'),
                              },
                            ]
                          : []),
                        ...[...new Set(tableItems.flatMap(row => Object.keys(row)))].filter(key => key !== activeTable.PartitionKey && key !== activeTable.SortKey).map(key => ({
                          id: key, header: key,
                          cell: (row: any) => <span style={{ overflowWrap: 'anywhere' }}>{row[key] == null ? '—' : typeof row[key] === 'object' ? JSON.stringify(row[key]) : String(row[key])}</span>,
                        })),
                      ]}
                      items={tableItems}
                      loading={loadingItems}
                      selectionType="single"
                      selectedItems={selectedItemRows}
                      onSelectionChange={({ detail }) => setSelectedItemRows(detail.selectedItems)}
                      empty={<Box textAlign="center">No items returned from scan.</Box>}
                    />
                    {selectedItemRows[0] && <Container header={<Header variant="h3">Selected item document</Header>}><CodeSnippet language="json" code={JSON.stringify(selectedItemRows[0], null, 2)} /></Container>}
                  </SpaceBetween>
                ),
              },
              {
                label: 'PartiQL SQL Editor',
                id: 'partiql',
                content: (
                  <SpaceBetween size="m">
                    <FormField label="SQL (PartiQL) Statement" description="Execute standard SELECT queries on DynamoDB tables.">
                      <Textarea
                        rows={3}
                        value={partiqlQuery}
                        onChange={({ detail }) => setPartiqlQuery(detail.value)}
                        placeholder={`SELECT * FROM "${activeTable.TableName}"`}
                      />
                    </FormField>

                    <SpaceBetween direction="horizontal" size="s">
                      <Button variant="primary" iconName="caret-right-filled" loading={runningPartiql} onClick={handleRunPartiQL}>
                        Run PartiQL Query
                      </Button>
                      {partiqlLatency !== null && (
                        <Box padding={{ top: 'xs' }}>
                          <Badge color="blue">Latency: {partiqlLatency}ms</Badge>
                        </Box>
                      )}
                    </SpaceBetween>

                    {partiqlResults.length > 0 && (
                      <Table
                        columnDefinitions={[
                          { id: 'row', header: 'Query Result Item', cell: (r) => <code>{JSON.stringify(r)}</code> },
                        ]}
                        items={partiqlResults}
                      />
                    )}
                  </SpaceBetween>
                ),
              },
              {
                label: `Indexes (${(activeTable.GlobalSecondaryIndexes?.length || 0) + (activeTable.LocalSecondaryIndexes?.length || 0)})`,
                id: 'indexes',
                content: (
                  <SpaceBetween size="l">
                    <Container header={<Header variant="h3">Global Secondary Indexes (GSIs)</Header>}>
                      <Table
                        columnDefinitions={[
                          { id: 'name', header: 'Index Name', cell: (i) => <strong>{i.IndexName}</strong> },
                          { id: 'status', header: 'Status', cell: (i) => <StatusIndicator type="success">{i.IndexStatus || 'ACTIVE'}</StatusIndicator> },
                          { id: 'keys', header: 'Key Schema', cell: (i) => i.KeySchema?.map((k: any) => `${k.AttributeName} (${k.KeyType})`).join(', ') || '—' },
                          { id: 'proj', header: 'Projection', cell: (i) => i.Projection?.ProjectionType || 'ALL' },
                        ]}
                        items={activeTable.GlobalSecondaryIndexes || []}
                        empty={<Box textAlign="center">No Global Secondary Indexes configured on this table.</Box>}
                      />
                    </Container>

                    <Container header={<Header variant="h3">Local Secondary Indexes (LSIs)</Header>}>
                      <Table
                        columnDefinitions={[
                          { id: 'name', header: 'Index Name', cell: (i) => <strong>{i.IndexName}</strong> },
                          { id: 'keys', header: 'Key Schema', cell: (i) => i.KeySchema?.map((k: any) => `${k.AttributeName} (${k.KeyType})`).join(', ') || '—' },
                          { id: 'proj', header: 'Projection', cell: (i) => i.Projection?.ProjectionType || 'ALL' },
                        ]}
                        items={activeTable.LocalSecondaryIndexes || []}
                        empty={<Box textAlign="center">No Local Secondary Indexes configured on this table.</Box>}
                      />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Capacity & Backups',
                id: 'capacity',
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Manage Read/Write capacity modes and throughput."
                          actions={
                            <Button variant="primary" loading={savingCapacity} onClick={handleSaveCapacity}>
                              Save Capacity
                            </Button>
                          }
                        >
                          Read/Write Capacity Settings
                        </Header>
                      }
                    >
                      <SpaceBetween size="m">
                        <FormField label="Capacity Mode">
                          <Select
                            selectedOption={{ label: billingMode === 'PAY_PER_REQUEST' ? 'On-Demand (Pay per request)' : 'Provisioned', value: billingMode }}
                            onChange={({ detail }) => setBillingMode(detail.selectedOption.value as any)}
                            options={[
                              { label: 'On-Demand (Pay per request)', value: 'PAY_PER_REQUEST' },
                              { label: 'Provisioned', value: 'PROVISIONED' },
                            ]}
                          />
                        </FormField>

                        {billingMode === 'PROVISIONED' && (
                          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                            <FormField label="Read Capacity Units (RCU)">
                              <Input value={readUnits} onChange={({ detail }) => setReadUnits(detail.value)} type="number" />
                            </FormField>
                            <FormField label="Write Capacity Units (WCU)">
                              <Input value={writeUnits} onChange={({ detail }) => setWriteUnits(detail.value)} type="number" />
                            </FormField>
                          </Grid>
                        )}
                      </SpaceBetween>
                    </Container>

                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Continuous backups allow point-in-time recovery for table restoration."
                          actions={
                            <Button loading={savingPitr} onClick={handleTogglePitr}>
                              {pitrEnabled ? 'Disable PITR' : 'Enable PITR'}
                            </Button>
                          }
                        >
                          Point-in-Time Recovery (PITR)
                        </Header>
                      }
                    >
                      <Box>
                        Status:{' '}
                        {pitrEnabled ? (
                          <StatusIndicator type="success">Enabled</StatusIndicator>
                        ) : (
                          <StatusIndicator type="stopped">Disabled</StatusIndicator>
                        )}
                      </Box>
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'TTL & Streams',
                id: 'ttl',
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Time to Live (TTL) automatically deletes items based on an epoch timestamp attribute."
                          actions={
                            <Button loading={savingTtl} onClick={handleToggleTtl}>
                              {ttlEnabled ? 'Disable TTL' : 'Enable TTL'}
                            </Button>
                          }
                        >
                          Time to Live (TTL)
                        </Header>
                      }
                    >
                      <SpaceBetween size="m">
                        <Box>
                          Status:{' '}
                          {ttlEnabled ? (
                            <StatusIndicator type="success">Enabled</StatusIndicator>
                          ) : (
                            <StatusIndicator type="stopped">Disabled</StatusIndicator>
                          )}
                        </Box>
                        <FormField label="TTL Attribute Name">
                          <Input value={ttlAttribute} onChange={({ detail }) => setTtlAttribute(detail.value)} placeholder="expiresAt" />
                        </FormField>
                      </SpaceBetween>
                    </Container>

                    <Container header={<Header variant="h3">DynamoDB Streams</Header>}>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Streams Status', value: activeTable.StreamSpecification ? 'Enabled' : 'Disabled' },
                          { label: 'Stream ARN', value: activeTable.LatestStreamArn || 'N/A' },
                          { label: 'View Type', value: activeTable.StreamSpecification?.StreamViewType || 'NEW_AND_OLD_IMAGES' },
                        ]}
                      />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Table Overview',
                id: 'overview',
                content: (
                  <KeyValuePairs
                    columns={3}
                    items={[
                      { label: 'Table ARN', value: activeTable.TableArn || 'Not reported' },
                      { label: 'Table Status', value: activeTable.TableStatus },
                      { label: 'Creation Date', value: activeTable.CreationDateTime },
                      { label: 'Partition Key', value: `${activeTable.PartitionKey} (HASH)` },
                      { label: 'Sort Key', value: activeTable.SortKey ? `${activeTable.SortKey} (RANGE)` : 'None' },
                      { label: 'Table Size (Bytes)', value: `${activeTable.TableSizeBytes || 0} Bytes` },
                      { label: 'Item Count', value: activeTable.ItemCount ?? 0 },
                      { label: 'Billing Mode', value: activeTable.BillingMode || 'PAY_PER_REQUEST' },
                    ]}
                  />
                ),
              },
            ]}
          />
        </Container>
      )}

      {/* Create Table Modal */}
      <Modal
        visible={createTableOpen}
        onDismiss={() => setCreateTableOpen(false)}
        header="Create DynamoDB Table"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateTableOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingTable} onClick={handleCreateTable}>
                Create Table
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Table Name" description="Unique table name.">
            <Input
              value={tableName}
              onChange={({ detail }) => setTableName(detail.value)}
              placeholder="Orders"
            />
          </FormField>

          <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
            <FormField label="Partition Key Name (Required)">
              <Input
                value={partitionKeyName}
                onChange={({ detail }) => setPartitionKeyName(detail.value)}
                placeholder="id"
              />
            </FormField>
            <FormField label="Partition Key Type">
              <Select
                selectedOption={partitionKeyType}
                onChange={({ detail }) => setPartitionKeyType(detail.selectedOption as any)}
                options={[
                  { label: 'String (S)', value: 'S' },
                  { label: 'Number (N)', value: 'N' },
                  { label: 'Binary (B)', value: 'B' },
                ]}
              />
            </FormField>
          </Grid>

          <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
            <FormField label="Sort Key Name (Optional)">
              <Input
                value={sortKeyName}
                onChange={({ detail }) => setSortKeyName(detail.value)}
                placeholder="createdAt or sk"
              />
            </FormField>
            <FormField label="Sort Key Type">
              <Select
                selectedOption={sortKeyType}
                onChange={({ detail }) => setSortKeyType(detail.selectedOption as any)}
                options={[
                  { label: 'String (S)', value: 'S' },
                  { label: 'Number (N)', value: 'N' },
                  { label: 'Binary (B)', value: 'B' },
                ]}
              />
            </FormField>
          </Grid>
        </SpaceBetween>
      </Modal>

      {/* Create / Edit Item Modal */}
      <Modal
        visible={itemModalOpen}
        onDismiss={() => setItemModalOpen(false)}
        header={isEditingItem ? `Edit Item in ${activeTable?.TableName}` : `Insert Item into ${activeTable?.TableName}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setItemModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingItem} onClick={handleSaveItem}>
                Save Item
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Item Document (JSON)"
            description={`Must include partition key "${activeTable?.PartitionKey || 'id'}".`}
          >
            <Textarea
              rows={10}
              value={itemJson}
              onChange={({ detail }) => setItemJson(detail.value)}
              placeholder="{\n  &quot;id&quot;: &quot;123&quot;\n}"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
