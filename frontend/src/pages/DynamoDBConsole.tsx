import { normalizeDynamoTable } from './dynamoResources';
import { DynamoTableSchema, capacityModeLabel } from '../components/DynamoTableSchema';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate, formatBytes } from '../format';
import { useSession } from '../components/SessionContext';
import { CodeSnippet } from '../components/CodeSnippet';
import React, { useState, useEffect, useRef } from 'react';
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
import Alert from '@cloudscape-design/components/alert';
import {
  fetchServiceInventory,
  createDynamoDbTable,
  deleteDynamoDbTable,
  fetchDynamoDbTableScan,
  executeDynamoDbPartiQL,
  putDynamoDbItem,
  deleteDynamoDbItem,
  updateDynamoDbTableCapacity,
  updateDynamoDbPitr,
  updateDynamoDbTtl,
} from '../api/client';

interface TableItem {
  KeySchema?: any[];
  AttributeDefinitions?: any[];
  ProvisionedThroughput?: any;
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
  DeletionProtectionEnabled?: boolean;
  TableClass?: string;
}

const titleCase = (value: string) => value.charAt(0) + value.slice(1).toLowerCase();

interface DynamoDBConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const DynamoDBConsole: React.FC<DynamoDBConsoleProps> = ({ activeTab, onTabChange, resourcePath, onNavigate }) => {
  const { identity } = useSession();
  const generation = useRef(0);
  const [inventoryError, setInventoryError] = useState('');
  const [itemsError, setItemsError] = useState('');
  const requested = new URLSearchParams((resourcePath || '').split('?')[1] || '').get('table');
  const navigateTable = (name: string, tab = selectedTabId) => {
    if (onNavigate) onNavigate(`dynamodb/${tab}?${new URLSearchParams({ table: name })}`);
    else onTabChange?.(tab);
  };
  const [tables, setTables] = useState<TableItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTables, setSelectedTables] = useState<TableItem[]>([]);
  const [tableFilter, setTableFilter] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab === 'schema' ? 'overview' : activeTab || 'items');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab === 'schema' ? 'overview' : activeTab);
    }
  }, [activeTab]);

  // Create Table Modal
  const [createTableOpen, setCreateTableOpen] = useState(false);
  const [tableName, setTableName] = useState('');
  const [partitionKeyName, setPartitionKeyName] = useState('id');
  const [partitionKeyType, setPartitionKeyType] = useState({ label: 'String (S)', value: 'S' });
  const [sortKeyName, setSortKeyName] = useState('');
  const [sortKeyType, setSortKeyType] = useState({ label: 'String (S)', value: 'S' });
  const [createCapacityMode, setCreateCapacityMode] = useState<'PAY_PER_REQUEST' | 'PROVISIONED'>('PAY_PER_REQUEST');
  const [createDeletionProtection, setCreateDeletionProtection] = useState(false);
  const [creatingTable, setCreatingTable] = useState(false);
  const [deleteTableOpen, setDeleteTableOpen] = useState(false);
  const [deleteItemOpen, setDeleteItemOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  // Items Explorer for Active Table
  const [tableItems, setTableItems] = useState<any[]>([]);
  const [selectedItemRows, setSelectedItemRows] = useState<any[]>([]);
  const [scanCursor, setScanCursor] = useState<any>(null);
  const [loadingItems, setLoadingItems] = useState(false);

  // Create / Edit Item Modal
  const [itemModalOpen, setItemModalOpen] = useState(false);
  const [isEditingItem, setIsEditingItem] = useState(false);
  const [itemJson, setItemJson] = useState('{}');
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
  const [pitrEnabled, setPitrEnabled] = useState<boolean | undefined>(undefined);
  const [savingCapacity, setSavingCapacity] = useState(false);
  const [savingPitr, setSavingPitr] = useState(false);

  // TTL State
  const [ttlEnabled, setTtlEnabled] = useState(false);
  const [ttlAttribute, setTtlAttribute] = useState('');
  const [savingTtl, setSavingTtl] = useState(false);

  const loadTables = async () => {
    setLoading(true);
    setInventoryError('');
    try {
      const data = await fetchServiceInventory('dynamodb');
      if (data.error) throw new Error(data.error);
      const list = (data.tables || data.TableNames || []).map(normalizeDynamoTable);
      setTables(list);
      setSelectedTables(previous => {
        const kept = previous.length ? list.filter((table: TableItem) => table.TableName === previous[0].TableName) : [];
        return kept.length ? kept : requested ? [] : list.slice(0, 1);
      });
    } catch (err) {
      setInventoryError(err instanceof Error ? err.message : 'Unable to load tables');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTables();
  }, []);

  useEffect(() => {
    if (requested) setSelectedTables(tables.filter(table => table.TableName === requested || table.TableArn === requested));
  }, [requested, tables]);

  const activeTable = selectedTables[0] || null;

  const loadTableItems = async (tbl: TableItem, append = false) => {
    const requestGeneration = ++generation.current;
    if (!append) { setTableItems([]); setScanCursor(null); }
    setItemsError('');
    setLoadingItems(true);
    try {
      const res = await fetchDynamoDbTableScan(tbl.TableName, 50, append ? scanCursor : undefined);
      if (requestGeneration !== generation.current) return;
      setTableItems(previous => append ? [...previous, ...(res.items || [])] : res.items || []);
      setScanCursor(res.last_evaluated_key || null);
    } catch (err) {
      if (requestGeneration !== generation.current) return;
      setItemsError(err instanceof Error ? err.message : 'Unable to scan table');
    } finally {
      if (requestGeneration === generation.current) setLoadingItems(false);
    }
  };

  useEffect(() => {
    if (activeTable) {
      loadTableItems(activeTable);
      setSelectedItemRows([]); setItemModalOpen(false);
      setReadUnits(String(activeTable.ProvisionedThroughput?.ReadCapacityUnits || 5));
      setWriteUnits(String(activeTable.ProvisionedThroughput?.WriteCapacityUnits || 5));
      setPartiqlQuery(`SELECT * FROM "${activeTable.TableName}"`);
      setPartiqlResults([]);
      setBillingMode((activeTable.BillingMode as any) || 'PAY_PER_REQUEST');
      setPitrEnabled(activeTable.PointInTimeRecovery);
      setTtlEnabled(activeTable.TimeToLive?.Status === 'ENABLED');
      setTtlAttribute(activeTable.TimeToLive?.AttributeName || '');
    } else {
      setTableItems([]);
    }
    return () => { generation.current += 1; };
  }, [activeTable?.TableName]);

  const handleCreateTable = async () => {
    if (!tableName.trim() || !partitionKeyName.trim()) return;
    setCreatingTable(true);
    setActionMessage(null);
    try {
      await createDynamoDbTable({
        table_name: tableName.trim(),
        partition_key: { name: partitionKeyName.trim(), type: partitionKeyType.value },
        sort_key: sortKeyName.trim() ? { name: sortKeyName.trim(), type: sortKeyType.value } : undefined,
        billing_mode: createCapacityMode,
        read_capacity_units: createCapacityMode === 'PROVISIONED' ? Number(readUnits) : undefined,
        write_capacity_units: createCapacityMode === 'PROVISIONED' ? Number(writeUnits) : undefined,
        deletion_protection: createDeletionProtection,
      });
      setActionMessage({ type: 'success', text: `The ${tableName.trim()} table was created successfully.` });
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
    setBusy(true);
    try {
      await deleteDynamoDbTable(activeTable.TableName);
      setActionMessage({ type: 'success', text: `The ${activeTable.TableName} table was deleted.` });
      setSelectedTables([]);
      await loadTables();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete table' });
    } finally {
      setBusy(false);
      setDeleteTableOpen(false);
    }
  };

  const handleSaveItem = async () => {
    if (!activeTable) return;
    setSavingItem(true);
    try {
      let parsed: any;
      try {
        parsed = JSON.parse(itemJson);
      } catch {
        throw new Error('Item must be valid JSON.');
      }
      const missing = [activeTable.PartitionKey, activeTable.SortKey].filter(key => key && (parsed?.[key] === undefined || parsed?.[key] === ''));
      if (missing.length) throw new Error(`The item must include the key attribute${missing.length > 1 ? 's' : ''} ${missing.join(' and ')}.`);
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
    if (!activeTable.PartitionKey) return;
    const keyObj: Record<string, any> = { [activeTable.PartitionKey]: row[activeTable.PartitionKey] };
    if (activeTable.SortKey && row[activeTable.SortKey] !== undefined) {
      keyObj[activeTable.SortKey] = row[activeTable.SortKey];
    }
    setBusy(true);
    try {
      await deleteDynamoDbItem(activeTable.TableName, keyObj);
      setActionMessage({ type: 'success', text: 'The item was deleted.' });
      setSelectedItemRows([]);
      await loadTableItems(activeTable);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete item' });
    } finally {
      setBusy(false);
      setDeleteItemOpen(false);
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
        billingMode === 'PROVISIONED' ? Number(readUnits) : undefined,
        billingMode === 'PROVISIONED' ? Number(writeUnits) : undefined,
      );
      setActionMessage({ type: 'success', text: `Capacity mode is now ${capacityModeLabel(billingMode)}.` });
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
                <Button disabled={!activeTable} onClick={() => setDeleteTableOpen(true)}>
                  Delete table
                </Button>
                <Button variant="primary" onClick={() => setCreateTableOpen(true)}>
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

        <div className="console-summary">
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Total Tables</Box>
            <Box variant="h1" color="text-status-info">
              {tables.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active Region</Box>
            <Box variant="h2" color="text-status-info">
              {identity?.region || 'Not reported'}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Inventory status</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type={inventoryError ? 'error' : loading ? 'loading' : 'success'}>{inventoryError || (loading ? 'Loading tables' : 'Inventory loaded')}</StatusIndicator>
            </Box>
          </Box>
        </div>
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
                  <Button variant="inline-link" onClick={() => { setSelectedTables([item]); navigateTable(item.TableName); }}>
                    <strong>{item.TableName}</strong>
                  </Button>
                ),
              },
              {
                id: 'status',
                header: 'Status',
                cell: (item) => (
                  <StatusIndicator type={item.TableStatus === 'ACTIVE' ? 'success' : item.TableStatus === 'Not reported' ? 'info' : item.TableStatus.includes('FAILED') ? 'error' : 'in-progress'}>
                    {item.TableStatus === 'Not reported' ? '-' : titleCase(item.TableStatus)}
                  </StatusIndicator>
                ),
                width: 130,
              },
              {
                id: 'partitionKey',
                header: 'Partition key',
                cell: (item) => item.PartitionKey || '-',
                width: 170,
              },
              {
                id: 'sortKey',
                header: 'Sort key',
                cell: (item) => item.SortKey || '-',
                width: 170,
              },
              { id: 'indexes', header: 'Indexes', cell: (item) => (item.GlobalSecondaryIndexes?.length || 0) + (item.LocalSecondaryIndexes?.length || 0), width: 100 },
              { id: 'protection', header: 'Deletion protection', cell: (item) => (item.DeletionProtectionEnabled == null ? '-' : item.DeletionProtectionEnabled ? 'On' : 'Off'), width: 160 },
              { id: 'capacity', header: 'Read/write capacity mode', cell: (item) => capacityModeLabel(item.BillingMode), width: 200 },
              { id: 'size', header: 'Total size', cell: (item) => formatBytes(item.TableSizeBytes), width: 120 },
            ]}
            items={filteredTables}
            selectionType="single"
            selectedItems={selectedTables}
            onSelectionChange={({ detail }) => { setSelectedTables(detail.selectedItems); if (detail.selectedItems[0]) navigateTable(detail.selectedItems[0].TableName); }}
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
      {requested && !loading && !activeTable && <Alert type="warning">Requested table was not found. Select an available table.</Alert>}
      {activeTable && (
        <Container
          header={
            <Header variant="h2" description={`Inspecting table: ${activeTable.TableName}`}>
              Table: {activeTable.TableName}
            </Header>
          }
        >
          <DynamoTableSchema table={activeTable} />
          {itemsError && <Alert type="error">{itemsError}</Alert>}
          <Tabs
            activeTabId={selectedTabId}
            onChange={({ detail }) => {
              setSelectedTabId(detail.activeTabId);
              if (activeTable) navigateTable(activeTable.TableName, detail.activeTabId); else onTabChange?.(detail.activeTabId);
            }}
            tabs={[
              {
                label: `Explore items (${tableItems.length})`,
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
                        <Button disabled={!selectedItemRows.length} onClick={() => setDeleteItemOpen(true)}>
                          Delete item
                        </Button>
                        <Button
                          variant="primary"
                          onClick={() => {
                            const keyType = (name?: string) => activeTable.AttributeDefinitions?.find((attribute: any) => attribute.AttributeName === name)?.AttributeType;
                            const template: Record<string, unknown> = {};
                            if (activeTable.PartitionKey) template[activeTable.PartitionKey] = keyType(activeTable.PartitionKey) === 'N' ? 0 : '';
                            if (activeTable.SortKey) template[activeTable.SortKey] = keyType(activeTable.SortKey) === 'N' ? 0 : '';
                            setItemJson(JSON.stringify(template, null, 2));
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
                          header: `${activeTable.PartitionKey} (Partition key)`,
                          cell: (row) => <strong>{String(row[activeTable.PartitionKey || 'id'] ?? '—')}</strong>,
                        },
                        ...(activeTable.SortKey
                          ? [
                              {
                                id: 'sk',
                                sortingField: activeTable.SortKey,
                                header: `${activeTable.SortKey} (Sort key)`,
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
                    {scanCursor && <Button loading={loadingItems} onClick={() => loadTableItems(activeTable, true)}>Load more items</Button>}
                    <Box variant="small">Showing {tableItems.length} scanned items{scanCursor ? '; more items are available.' : '.'} Table item counts are approximate.</Box>
                    {selectedItemRows[0] && <Container header={<Header variant="h3">Selected item document</Header>}><CodeSnippet language="json" code={JSON.stringify(selectedItemRows[0], null, 2)} /></Container>}
                  </SpaceBetween>
                ),
              },
              {
                label: 'PartiQL editor',
                id: 'partiql',
                content: (
                  <SpaceBetween size="m">
                    <FormField label="PartiQL statement" description="Runs ExecuteStatement. This console only allows read-only SELECT statements.">
                      <Textarea
                        rows={3}
                        value={partiqlQuery}
                        onChange={({ detail }) => setPartiqlQuery(detail.value)}
                        placeholder={`SELECT * FROM "${activeTable.TableName}"`}
                      />
                    </FormField>

                    <SpaceBetween direction="horizontal" size="s">
                      <Button variant="primary" iconName="caret-right-filled" loading={runningPartiql} onClick={handleRunPartiQL}>
                        Run
                      </Button>
                      {partiqlLatency !== null && (
                        <Box padding={{ top: 'xs' }}>
                          <Box color="text-status-inactive">Elapsed time (round trip): {partiqlLatency} ms</Box>
                        </Box>
                      )}
                    </SpaceBetween>

                    {partiqlResults.length > 0 && (
                      <Table
                        columnDefinitions={[
                          { id: 'row', header: 'Items returned', cell: (r) => <code>{JSON.stringify(r)}</code> },
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
                    <Container header={<Header variant="h3">Global secondary indexes</Header>}>
                      <Table
                        columnDefinitions={[
                          { id: 'name', header: 'Name', cell: (i) => i.IndexName },
                          { id: 'status', header: 'Status', cell: (i) => <StatusIndicator type={i.IndexStatus === 'ACTIVE' ? 'success' : i.IndexStatus ? 'in-progress' : 'info'}>{i.IndexStatus ? titleCase(i.IndexStatus) : '-'}</StatusIndicator> },
                          { id: 'pk', header: 'Partition key', cell: (i) => i.KeySchema?.find((k: any) => k.KeyType === 'HASH')?.AttributeName || '-' },
                          { id: 'sk', header: 'Sort key', cell: (i) => i.KeySchema?.find((k: any) => k.KeyType === 'RANGE')?.AttributeName || '-' },
                          { id: 'proj', header: 'Projected attributes', cell: (i) => (i.Projection?.ProjectionType === 'ALL' ? 'All' : i.Projection?.ProjectionType === 'KEYS_ONLY' ? 'Keys only' : i.Projection?.ProjectionType === 'INCLUDE' ? `Include: ${(i.Projection.NonKeyAttributes || []).join(', ')}` : '-') },
                        ]}
                        items={activeTable.GlobalSecondaryIndexes || []}
                        empty={<Box textAlign="center">No Global Secondary Indexes configured on this table.</Box>}
                      />
                    </Container>

                    <Container header={<Header variant="h3">Local secondary indexes</Header>}>
                      <Table
                        columnDefinitions={[
                          { id: 'name', header: 'Name', cell: (i) => i.IndexName },
                          { id: 'sk', header: 'Sort key', cell: (i) => i.KeySchema?.find((k: any) => k.KeyType === 'RANGE')?.AttributeName || '-' },
                          { id: 'proj', header: 'Projected attributes', cell: (i) => i.Projection?.ProjectionType || '-' },
                        ]}
                        items={activeTable.LocalSecondaryIndexes || []}
                        empty={<Box textAlign="center">No Local Secondary Indexes configured on this table.</Box>}
                      />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Capacity and backups',
                id: 'capacity',
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="On-demand bills per request. Provisioned sets read and write capacity units (RCU/WCU)."
                          actions={
                            <Button variant="primary" loading={savingCapacity} onClick={handleSaveCapacity}>
                              Save changes
                            </Button>
                          }
                        >
                          Read/write capacity
                        </Header>
                      }
                    >
                      <SpaceBetween size="m">
                        <FormField label="Capacity mode">
                          <Select
                            selectedOption={{ label: capacityModeLabel(billingMode), value: billingMode }}
                            onChange={({ detail }) => setBillingMode(detail.selectedOption.value as any)}
                            options={[
                              { label: 'On-demand', value: 'PAY_PER_REQUEST' },
                              { label: 'Provisioned', value: 'PROVISIONED' },
                            ]}
                          />
                        </FormField>

                        {billingMode === 'PROVISIONED' && (
                          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                            <FormField label="Read capacity units">
                              <Input value={readUnits} onChange={({ detail }) => setReadUnits(detail.value)} type="number" />
                            </FormField>
                            <FormField label="Write capacity units">
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
                          description="Point-in-time recovery (PITR) provides continuous backups, so you can restore the table to any second in the recovery period."
                          actions={
                            <Button loading={savingPitr} disabled={pitrEnabled === undefined} onClick={handleTogglePitr}>
                              {pitrEnabled ? 'Turn off' : 'Turn on'}
                            </Button>
                          }
                        >
                          Point-in-time recovery (PITR)
                        </Header>
                      }
                    >
                      <Box>
                        Status:{' '}
                        {pitrEnabled === undefined ? (
                          <StatusIndicator type="info">Not reported by DescribeContinuousBackups</StatusIndicator>
                        ) : pitrEnabled ? (
                          <StatusIndicator type="success">On</StatusIndicator>
                        ) : (
                          <StatusIndicator type="stopped">Off</StatusIndicator>
                        )}
                      </Box>
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'TTL and streams',
                id: 'ttl',
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header
                          variant="h3"
                          description="Time to Live (TTL) automatically deletes items based on an epoch timestamp attribute."
                          actions={
                            <Button loading={savingTtl} disabled={!ttlEnabled && !ttlAttribute.trim()} onClick={handleToggleTtl}>
                              {ttlEnabled ? 'Turn off' : 'Turn on'}
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
                            <StatusIndicator type="success">On</StatusIndicator>
                          ) : (
                            <StatusIndicator type="stopped">Off</StatusIndicator>
                          )}
                        </Box>
                        <FormField label="TTL attribute name" description="The attribute must hold an expiry time as a Unix epoch timestamp in seconds (Number type).">
                          <Input value={ttlAttribute} disabled={ttlEnabled} onChange={({ detail }) => setTtlAttribute(detail.value)} placeholder="expireAt" />
                        </FormField>
                      </SpaceBetween>
                    </Container>

                    <Container header={<Header variant="h3">DynamoDB stream details</Header>}>
                      <KeyValuePairs
                        columns={3}
                        items={[
                          { label: 'Stream status', value: activeTable.StreamSpecification?.StreamEnabled ? 'On' : 'Off' },
                          { label: 'View type', value: activeTable.StreamSpecification?.StreamEnabled ? activeTable.StreamSpecification.StreamViewType : '-' },
                          { label: 'Latest stream ARN', value: activeTable.LatestStreamArn || '-' },
                        ]}
                      />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                label: 'Overview',
                id: 'overview',
                content: (
                  <KeyValuePairs
                    columns={3}
                    items={[
                      { label: 'Amazon Resource Name (ARN)', value: activeTable.TableArn || '-' },
                      { label: 'Table status', value: activeTable.TableStatus === 'Not reported' ? '-' : titleCase(activeTable.TableStatus) },
                      { label: 'Creation date', value: formatAwsDate(activeTable.CreationDateTime) },
                      { label: 'Partition key', value: activeTable.PartitionKey || '-' },
                      { label: 'Sort key', value: activeTable.SortKey || '-' },
                      { label: 'Capacity mode', value: capacityModeLabel(activeTable.BillingMode) },
                      { label: 'Item count', value: activeTable.ItemCount ?? '-' },
                      { label: 'Table size', value: formatBytes(activeTable.TableSizeBytes) },
                      { label: 'Table class', value: activeTable.TableClass === 'STANDARD' ? 'DynamoDB Standard' : activeTable.TableClass === 'STANDARD_INFREQUENT_ACCESS' ? 'DynamoDB Standard-IA' : activeTable.TableClass || '-' },
                      { label: 'Deletion protection', value: activeTable.DeletionProtectionEnabled == null ? '-' : activeTable.DeletionProtectionEnabled ? 'On' : 'Off' },
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
        header="Create table"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateTableOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingTable} disabled={!tableName.trim() || !partitionKeyName.trim()} onClick={handleCreateTable}>
                Create table
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Table name" constraintText="Between 3 and 255 characters, containing only letters, numbers, underscores (_), hyphens (-), and periods (.).">
            <Input
              value={tableName}
              onChange={({ detail }) => setTableName(detail.value)}
              placeholder="Orders"
            />
          </FormField>

          <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
            <FormField label="Partition key" description="The partition key is part of the table's primary key. It is a hash value that is used to retrieve items from your table and allocate data across hosts for scalability and availability.">
              <Input
                value={partitionKeyName}
                onChange={({ detail }) => setPartitionKeyName(detail.value)}
                placeholder="id"
              />
            </FormField>
            <FormField label="Partition key type">
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
            <FormField label={<>Sort key <i>- optional</i></>} description="Use a sort key as the second part of a table's primary key to sort or search among all items sharing the same partition key.">
              <Input
                value={sortKeyName}
                onChange={({ detail }) => setSortKeyName(detail.value)}
                placeholder="createdAt or sk"
              />
            </FormField>
            <FormField label="Sort key type">
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

          <FormField label="Capacity mode">
            <Select
              selectedOption={{ label: capacityModeLabel(createCapacityMode), value: createCapacityMode }}
              onChange={({ detail }) => setCreateCapacityMode(detail.selectedOption.value as any)}
              options={[
                { label: 'On-demand', value: 'PAY_PER_REQUEST', description: 'Simplify billing by paying for the actual reads and writes your application performs.' },
                { label: 'Provisioned', value: 'PROVISIONED', description: 'Manage and optimize your costs by allocating read/write capacity in advance.' },
              ]}
            />
          </FormField>
          {createCapacityMode === 'PROVISIONED' && (
            <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
              <FormField label="Read capacity units"><Input type="number" value={readUnits} onChange={({ detail }) => setReadUnits(detail.value)} /></FormField>
              <FormField label="Write capacity units"><Input type="number" value={writeUnits} onChange={({ detail }) => setWriteUnits(detail.value)} /></FormField>
            </Grid>
          )}
          <FormField label="Deletion protection">
            <Select
              selectedOption={{ label: createDeletionProtection ? 'On' : 'Off', value: createDeletionProtection ? 'on' : 'off' }}
              onChange={({ detail }) => setCreateDeletionProtection(detail.selectedOption.value === 'on')}
              options={[{ label: 'Off', value: 'off' }, { label: 'On', value: 'on' }]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={deleteTableOpen}
        header={`Delete ${activeTable?.TableName}?`}
        confirmLabel="Delete"
        confirmationText="confirm"
        loading={busy}
        onConfirm={handleDeleteTable}
        onDismiss={() => setDeleteTableOpen(false)}
      >
        Deleting this table removes all of its items, indexes and streams. This can't be undone. Tables with deletion protection turned on can't be deleted.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={deleteItemOpen}
        header="Delete item?"
        confirmLabel="Delete"
        loading={busy}
        onConfirm={handleDeleteItem}
        onDismiss={() => setDeleteItemOpen(false)}
      >
        This permanently deletes the selected item from {activeTable?.TableName}.
      </ConfirmActionModal>

      {/* Create / Edit Item Modal */}
      <Modal
        visible={itemModalOpen}
        onDismiss={() => setItemModalOpen(false)}
        header={isEditingItem ? 'Edit item' : 'Create item'}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setItemModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingItem} onClick={handleSaveItem}>
                {isEditingItem ? 'Save and close' : 'Create item'}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Attributes (JSON)"
            description={`Plain JSON; numbers are stored as Number (N). Must include ${[activeTable?.PartitionKey, activeTable?.SortKey].filter(Boolean).join(' and ')}. Saving replaces the whole item (PutItem).`}
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
