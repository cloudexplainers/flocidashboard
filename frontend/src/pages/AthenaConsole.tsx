import React, { useState, useEffect } from 'react';
import Table from '../components/ResourceTable';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Container from '@cloudscape-design/components/container';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import Tabs from '@cloudscape-design/components/tabs';
import {
  fetchInventory,
  startAthenaQuery,
  stopAthenaQuery,
  fetchAthenaQueryResults,
  fetchAthenaQueryDetail,
  createAthenaWorkgroup,
} from '../api/client';

interface AthenaConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const AthenaConsole: React.FC<AthenaConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({
    queries: [],
    workgroups: [],
    data_catalogs: [],
  });
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Query Editor State
  const [queryString, setQueryString] = useState(
    'SELECT * FROM "default"."sample_table" LIMIT 10;'
  );
  const [database, setDatabase] = useState('default');
  const [workgroup, setWorkgroup] = useState('primary');
  const [outputLocation, setOutputLocation] = useState('s3://athena-query-results/workbench/');
  const [executing, setExecuting] = useState(false);
  const [lastQueryId, setLastQueryId] = useState<string | null>(null);
  const [queryStatus, setQueryStatus] = useState<string | null>(null);
  const [queryStats, setQueryStats] = useState<{ timeMs?: number; dataScanned?: string } | null>(null);
  const [resultsColumns, setResultsColumns] = useState<string[]>([]);
  const [resultsRows, setResultsRows] = useState<any[]>([]);

  // Create Workgroup Modal
  const [createWgOpen, setCreateWgOpen] = useState(false);
  const [wgName, setWgName] = useState('');
  const [wgDesc, setWgDesc] = useState('');
  const [wgOutput, setWgOutput] = useState('s3://athena-query-results/analytics/');
  const [creatingWg, setCreatingWg] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('athena');
      setData(res || { queries: [], workgroups: [], data_catalogs: [] });
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleRunQuery = async () => {
    if (!queryString.trim()) return;
    setExecuting(true);
    setQueryStatus('RUNNING');
    setActionMessage(null);
    setResultsColumns([]);
    setResultsRows([]);
    const startTime = Date.now();

    try {
      const res = await startAthenaQuery({
        query_string: queryString.trim(),
        database: database.trim() || undefined,
        workgroup: workgroup.trim() || undefined,
        output_location: outputLocation.trim() || undefined,
      });

      const qId = res.query_execution_id;
      setLastQueryId(qId);

      // Fetch details & results
      const [detailRes, resultsRes]: any[] = await Promise.all([
        fetchAthenaQueryDetail(qId),
        fetchAthenaQueryResults(qId),
      ]);

      const execution = detailRes.query_execution || detailRes.QueryExecution || detailRes;
      const elapsed = Date.now() - startTime;
      setQueryStats({
        timeMs: elapsed,
        dataScanned: execution.Statistics?.DataScannedInBytes != null ? `${(execution.Statistics.DataScannedInBytes / 1024).toFixed(1)} KB` : 'Unavailable',
      });
      setQueryStatus(execution.Status?.State || 'UNKNOWN');

      // Parse columns and rows from ResultSet
      const resultSet = resultsRes?.result_set || resultsRes?.ResultSet;
      if (resultSet?.Rows?.length > 0) {
        const headerRow = resultSet.Rows[0];
        const cols = (headerRow.Data || []).map((d: any) => d.VarCharValue || 'col');
        setResultsColumns(cols);

        const rows = resultSet.Rows.slice(1).map((r: any, idx: number) => {
          const rowObj: any = { _rowId: idx };
          (r.Data || []).forEach((d: any, colIdx: number) => {
            rowObj[cols[colIdx] || `col_${colIdx}`] = d.VarCharValue ?? '';
          });
          return rowObj;
        });
        setResultsRows(rows);
      } else {
        setResultsColumns([]);
        setResultsRows([]);
      }

      await loadData();
    } catch (err: any) {
      setQueryStatus('FAILED');
      setActionMessage({ type: 'error', text: err.message || 'Athena query execution failed' });
    } finally {
      setExecuting(false);
    }
  };

  const handleStopQuery = async (qId: string) => {
    try {
      await stopAthenaQuery(qId);
      setActionMessage({ type: 'success', text: `Query execution ${qId} stopped.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to stop query' });
    }
  };

  const handleCreateWorkgroup = async () => {
    if (!wgName.trim()) return;
    setCreatingWg(true);
    try {
      await createAthenaWorkgroup(wgName.trim(), wgDesc.trim(), wgOutput.trim());
      setActionMessage({ type: 'success', text: `Workgroup "${wgName.trim()}" created.` });
      setCreateWgOpen(false);
      setWgName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create workgroup' });
    } finally {
      setCreatingWg(false);
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Amazon Athena is an interactive query service that makes it easy to analyze data in Amazon S3 using standard SQL."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateWgOpen(true)}>
              Create Workgroup
            </Button>
          </SpaceBetween>
        }
      >
        Amazon Athena
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {/* Overview stats */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
        <Container>
          <Box variant="awsui-key-label">Active Workgroup</Box>
          <Box variant="awsui-value-large">{workgroup}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Recent Query Executions</Box>
          <Box variant="awsui-value-large">{(data.queries || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Workgroups</Box>
          <Box variant="awsui-value-large">{(data.workgroups || []).length || 1}</Box>
        </Container>
      </Grid>

      <Tabs
        activeTabId={activeTab || 'editor'}
        onChange={({ detail }) => onTabChange && onTabChange(detail.activeTabId)}
        tabs={[
          {
            label: 'Query Editor',
            id: 'editor',
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            onClick={() =>
                              setQueryString('SELECT * FROM "default"."sample_table" LIMIT 10;')
                            }
                          >
                            SELECT Query Preset
                          </Button>
                          <Button onClick={() => setQueryString('SHOW DATABASES;')}>
                            SHOW DATABASES
                          </Button>
                          <Button onClick={() => setQueryString('SHOW TABLES;')}>
                            SHOW TABLES
                          </Button>
                          <Button
                            variant="primary"
                            loading={executing}
                            iconName="play"
                            onClick={handleRunQuery}
                          >
                            Run Query
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      SQL Query Workbench
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
                      <FormField label="Database">
                        <Input value={database} onChange={({ detail }) => setDatabase(detail.value)} />
                      </FormField>
                      <FormField label="Workgroup">
                        <Input value={workgroup} onChange={({ detail }) => setWorkgroup(detail.value)} />
                      </FormField>
                      <FormField label="Query Result Location (S3)">
                        <Input value={outputLocation} onChange={({ detail }) => setOutputLocation(detail.value)} />
                      </FormField>
                    </Grid>

                    <FormField label="SQL Query">
                      <Textarea
                        rows={7}
                        value={queryString}
                        onChange={({ detail }) => setQueryString(detail.value)}
                      />
                    </FormField>

                    {queryStatus && (
                      <Box>
                        <SpaceBetween direction="horizontal" size="m">
                          <StatusIndicator type={queryStatus === 'SUCCEEDED' ? 'success' : queryStatus === 'RUNNING' ? 'in-progress' : 'error'}>
                            Status: {queryStatus}
                          </StatusIndicator>
                          {queryStats && (
                            <Box color="text-status-inactive">
                              Run Time: {queryStats.timeMs}ms | Data Scanned: {queryStats.dataScanned} | Execution ID: {lastQueryId}
                            </Box>
                          )}
                        </SpaceBetween>
                      </Box>
                    )}
                  </SpaceBetween>
                </Container>

                {/* Query Results Table */}
                <Container header={<Header variant="h2">Query Results ({resultsRows.length} rows)</Header>}>
                  {resultsColumns.length > 0 ? (
                    <Table
                      columnDefinitions={resultsColumns.map((col) => ({
                        id: col,
                        header: col,
                        cell: (item: any) => <code>{item[col]}</code>,
                      }))}
                      items={resultsRows}
                      empty={<Box textAlign="center">No data returned by query.</Box>}
                    />
                  ) : (
                    <Box textAlign="center" padding="l" color="text-status-inactive">
                      {queryStatus ? 'No data returned by query.' : 'Run a SQL query above to view tabular results.'}
                    </Box>
                  )}
                </Container>
              </SpaceBetween>
            ),
          },
          {
            label: `Recent Queries (${(data.queries || []).length})`,
            id: 'history',
            content: (
              <Container header={<Header variant="h2">Query Execution History</Header>}>
                <Table
                  columnDefinitions={[
                    {
                      id: 'id',
                      header: 'Query Execution ID',
                      cell: (item: any) => <code>{item.QueryExecutionId || item.id}</code>,
                      width: 260,
                    },
                    {
                      id: 'query',
                      header: 'SQL Query',
                      cell: (item: any) => (
                        <Button
                          variant="inline-link"
                          onClick={() => {
                            setQueryString(item.Query || item.query);
                            if (onTabChange) onTabChange('editor');
                          }}
                        >
                          {item.Query || item.query}
                        </Button>
                      ),
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      cell: (item: any) => {
                        const st = item.Status?.State || item.state || 'SUCCEEDED';
                        return (
                          <StatusIndicator type={st === 'SUCCEEDED' ? 'success' : st === 'RUNNING' ? 'in-progress' : 'error'}>
                            {st}
                          </StatusIndicator>
                        );
                      },
                      width: 140,
                    },
                    {
                      id: 'submission',
                      header: 'Submitted At',
                      cell: (item: any) => item.Status?.SubmissionDateTime || 'Recent',
                      width: 160,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            onClick={() => {
                              setQueryString(item.Query || item.query);
                              handleRunQuery();
                            }}
                          >
                            Re-run
                          </Button>
                          <Button
                            iconName="remove"
                            onClick={() => handleStopQuery(item.QueryExecutionId || item.id)}
                          >
                            Stop
                          </Button>
                        </SpaceBetween>
                      ),
                      width: 170,
                    },
                  ]}
                  items={data.queries || []}
                  empty={<Box textAlign="center">No recent query executions on record.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Workgroups (${(data.workgroups || []).length || 1})`,
            id: 'workgroups',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateWgOpen(true)}>
                        Create Workgroup
                      </Button>
                    }
                  >
                    Athena Workgroups
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'Workgroup Name',
                      cell: (item: any) => <strong>{item.Name || item.name || 'primary'}</strong>,
                    },
                    {
                      id: 'state',
                      header: 'State',
                      cell: (item: any) => <StatusIndicator type="success">{item.State || 'ENABLED'}</StatusIndicator>,
                      width: 130,
                    },
                    {
                      id: 'output',
                      header: 'Query Output Location',
                      cell: (item: any) => <code>{item.Configuration?.ResultConfiguration?.OutputLocation || outputLocation}</code>,
                    },
                    {
                      id: 'desc',
                      header: 'Description',
                      cell: (item: any) => item.Description || 'Default workbench workgroup',
                    },
                  ]}
                  items={
                    data.workgroups?.length > 0
                      ? data.workgroups
                      : [{ Name: 'primary', State: 'ENABLED', Description: 'Primary interactive workgroup' }]
                  }
                />
              </Container>
            ),
          },
        ]}
      />

      {/* Create Workgroup Modal */}
      <Modal
        visible={createWgOpen}
        onDismiss={() => setCreateWgOpen(false)}
        header="Create Athena Workgroup"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateWgOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingWg} onClick={handleCreateWorkgroup}>
                Create Workgroup
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Workgroup Name">
            <Input value={wgName} onChange={({ detail }) => setWgName(detail.value)} placeholder="analytics-team" />
          </FormField>
          <FormField label="Description">
            <Input value={wgDesc} onChange={({ detail }) => setWgDesc(detail.value)} placeholder="Workgroup for BI analytics" />
          </FormField>
          <FormField label="Output Location (S3 URI)">
            <Input value={wgOutput} onChange={({ detail }) => setWgOutput(detail.value)} placeholder="s3://my-query-results/" />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
