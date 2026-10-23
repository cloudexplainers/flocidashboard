import React, { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Tabs from "@cloudscape-design/components/tabs";
import Textarea from "@cloudscape-design/components/textarea";
import TextFilter from "@cloudscape-design/components/text-filter";
import Table from "../components/ResourceTable";
import { useSession } from "../components/SessionContext";
import {
  startAthenaQuery,
  stopAthenaQuery,
  fetchAthenaQueryResults,
  fetchAthenaQueryDetail,
  createAthenaWorkgroup,
} from "../api/client";
import {
  AthenaColumn,
  athenaBytes,
  athenaDate,
  athenaQueries,
  athenaResultPage,
  athenaWorkgroups,
  queryId,
  queryState,
} from "./athenaResources";

type Props = { activeTab?: string; onTabChange?: (tab: string) => void };
const terminal = (state?: string) =>
  ["SUCCEEDED", "FAILED", "CANCELLED"].includes(state || "");
const QueryState = ({ state }: { state?: string }) => (
  <StatusIndicator
    type={
      state === "SUCCEEDED"
        ? "success"
        : state === "FAILED"
          ? "error"
          : state === "CANCELLED"
            ? "stopped"
            : state === "RUNNING" || state === "QUEUED"
              ? "in-progress"
              : "not-started"
    }
  >
    {state || "Not reported"}
  </StatusIndicator>
);
export const AthenaConsole: React.FC<Props> = (props) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <SpaceBetween size="m">
        <Header variant="h1">Amazon Athena</Header>
        <StatusIndicator type="loading">
          Loading session context
        </StatusIndicator>
      </SpaceBetween>
    );
  return (
    <AthenaWorkspace
      key={JSON.stringify([
        identity?.arn,
        identity?.account_id,
        identity?.region,
        identity?.endpoint,
        identity?.expires_at,
        details?.session_identity?.access_key_id,
      ])}
      {...props}
    />
  );
};
const AthenaWorkspace: React.FC<Props> = ({
  activeTab = "editor",
  onTabChange,
}) => {
  const [tab, setTab] = useState(activeTab);
  useEffect(() => setTab(activeTab), [activeTab]);
  const showTab = (next: string) => {
    setTab(next);
    onTabChange?.(next);
  };
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [query, setQuery] = useState("SELECT 42 AS answer;");
  const [database, setDatabase] = useState("");
  const [catalog, setCatalog] = useState("");
  const [workgroup, setWorkgroup] = useState("");
  const [output, setOutput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [id, setId] = useState("");
  const [execution, setExecution] = useState<any>(null);
  const [queryError, setQueryError] = useState("");
  const [monitoring, setMonitoring] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [revision, setRevision] = useState(0);
  const [columns, setColumns] = useState<AthenaColumn[]>([]);
  const [rows, setRows] = useState<(string | null)[][]>([]);
  const [nextToken, setNextToken] = useState("");
  const [resultError, setResultError] = useState("");
  const [resultsLoading, setResultsLoading] = useState(false);
  const [updateCount, setUpdateCount] = useState<number>();
  const [filter, setFilter] = useState("");
  const [selectedWorkgroup, setSelectedWorkgroup] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [wgName, setWgName] = useState("");
  const [wgDescription, setWgDescription] = useState("");
  const [wgOutput, setWgOutput] = useState("");
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);
  const mounted = useRef(true);
  const loadGeneration = useRef(0);
  const queryGeneration = useRef(0);
  const submissionLock = useRef(false);
  const createLock = useRef(false);
  const moreLock = useRef(false);
  const queries = athenaQueries(data);
  const groups = athenaWorkgroups(data);
  const group = groups.find((item) => item.Name === selectedWorkgroup);
  const chosenGroup = groups.find((item) => item.Name === workgroup);
  const state = queryState(execution);
  const active = !!id && !terminal(state);
  const busy = submitting || (active && monitoring);
  const load = async () => {
    const version = ++loadGeneration.current;
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/athena/");
      const result = await response.json();
      if (!response.ok || result.error)
        throw new Error(result.error || "Unable to load Athena resources");
      if (mounted.current && version === loadGeneration.current)
        setData(result);
    } catch (error: any) {
      if (mounted.current && version === loadGeneration.current)
        setLoadError(error.message);
    } finally {
      if (mounted.current && version === loadGeneration.current)
        setLoading(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      queryGeneration.current++;
    };
  }, []);
  const clearResults = () => {
    setColumns([]);
    setRows([]);
    setNextToken("");
    setResultError("");
    setUpdateCount(undefined);
  };
  useEffect(() => {
    if (!id) return;
    let alive = true;
    const version = ++queryGeneration.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    clearResults();
    setResultsLoading(false);
    setQueryError("");
    setMonitoring(true);
    const valid = () =>
      alive && mounted.current && version === queryGeneration.current;
    const inspect = async () => {
      try {
        const result = await fetchAthenaQueryDetail(id);
        if (!valid()) return;
        const detail =
          result.query_execution || result.QueryExecution || result;
        setExecution(detail);
        const status = queryState(detail);
        if (status === "QUEUED" || status === "RUNNING") {
          timer = setTimeout(inspect, 1000);
          return;
        }
        setMonitoring(false);
        if (status === "SUCCEEDED") {
          setResultsLoading(true);
          try {
            const page = athenaResultPage(
              await fetchAthenaQueryResults(id),
              true,
            );
            if (valid()) {
              setColumns(page.columns);
              setRows(page.rows);
              setNextToken(page.next);
              setUpdateCount(page.updateCount);
            }
          } catch (error: any) {
            if (valid()) setResultError(error.message);
          } finally {
            if (valid()) setResultsLoading(false);
          }
        }
        void load();
      } catch (error: any) {
        if (valid()) {
          setQueryError(error.message);
          setMonitoring(false);
        }
      }
    };
    void inspect();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [id, revision]);
  const runQuery = async (sql = query, context?: any) => {
    if (!sql.trim() || submissionLock.current || active || resultsLoading)
      return;
    submissionLock.current = true;
    setSubmitting(true);
    setQueryError("");
    setMessage("");
    clearResults();
    setExecution(null);
    setId("");
    showTab("editor");
    const version = ++queryGeneration.current;
    try {
      const result = await startAthenaQuery({
        query_string: sql.trim(),
        database: (context?.Database ?? database).trim() || undefined,
        catalog: (context?.Catalog ?? catalog).trim() || undefined,
        workgroup: (context?.WorkGroup ?? workgroup).trim() || undefined,
        output_location:
          (context?.OutputLocation ?? output).trim() || undefined,
      });
      if (!mounted.current || version !== queryGeneration.current) return;
      if (!result.query_execution_id)
        throw new Error("Athena did not return a query execution ID.");
      setId(result.query_execution_id);
    } catch (error: any) {
      if (mounted.current && version === queryGeneration.current)
        setQueryError(error.message);
    } finally {
      submissionLock.current = false;
      if (mounted.current) setSubmitting(false);
    }
  };
  const inspectQuery = (item: any) => {
    if (busy || resultsLoading) return;
    setExecution(item);
    setId(queryId(item));
    setRevision((value) => value + 1);
    showTab("editor");
  };
  const stop = async () => {
    if (!id || stopping) return;
    setStopping(true);
    try {
      await stopAthenaQuery(id);
      if (mounted.current) {
        setMessage(
          "Stop requested. Waiting for the execution state to update.",
        );
        setRevision((value) => value + 1);
      }
    } catch (error: any) {
      if (mounted.current) setQueryError(error.message);
    } finally {
      if (mounted.current) setStopping(false);
    }
  };
  const more = async () => {
    if (!nextToken || moreLock.current) return;
    moreLock.current = true;
    setResultsLoading(true);
    setResultError("");
    const version = queryGeneration.current;
    try {
      const page = athenaResultPage(
        await fetchAthenaQueryResults(id, 50, nextToken),
        false,
        columns,
      );
      if (mounted.current && version === queryGeneration.current) {
        setRows((previous) => [...previous, ...page.rows]);
        setNextToken(page.next);
      }
    } catch (error: any) {
      if (mounted.current && version === queryGeneration.current)
        setResultError(error.message);
    } finally {
      moreLock.current = false;
      if (mounted.current && version === queryGeneration.current)
        setResultsLoading(false);
    }
  };
  const create = async () => {
    if (!wgName.trim() || createLock.current) return;
    createLock.current = true;
    setCreating(true);
    setCreateError("");
    try {
      await createAthenaWorkgroup(
        wgName.trim(),
        wgDescription.trim(),
        wgOutput.trim(),
      );
      if (mounted.current) {
        setCreateOpen(false);
        setMessage(`Created workgroup ${wgName.trim()}.`);
        setWgName("");
        await load();
      }
    } catch (error: any) {
      if (mounted.current) setCreateError(error.message);
    } finally {
      createLock.current = false;
      if (mounted.current) setCreating(false);
    }
  };
  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Query data with SQL and inspect execution state, results, and workgroup settings."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={load}>
              Refresh
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setCreateError("");
                setCreateOpen(true);
              }}
            >
              Create workgroup
            </Button>
          </SpaceBetween>
        }
      >
        Amazon Athena
      </Header>
      {loadError && (
        <Alert type="error" header="Unable to load Athena resources">
          {loadError}
        </Alert>
      )}
      {message && (
        <Alert type="success" dismissible onDismiss={() => setMessage("")}>
          {message}
        </Alert>
      )}
      <Container header={<Header variant="h2">Query overview</Header>}>
        <ColumnLayout columns={3} variant="text-grid">
          {[
            ["Recent executions", queries.length],
            [
              "Running or queued",
              queries.filter((item) =>
                ["RUNNING", "QUEUED"].includes(queryState(item)),
              ).length,
            ],
            ["Workgroups", groups.length],
          ].map(([label, value]) => (
            <div key={label}>
              <Box variant="awsui-key-label">{label}</Box>
              <Box variant="h1">{loading || loadError ? "—" : value}</Box>
            </div>
          ))}
        </ColumnLayout>
      </Container>
      <Tabs
        activeTabId={tab}
        onChange={({ detail }) => showTab(detail.activeTabId)}
        tabs={[
          {
            id: "editor",
            label: "Query Editor",
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            disabled={busy || active}
                            onClick={() => setQuery("SELECT 42 AS answer;")}
                          >
                            SELECT preset
                          </Button>
                          <Button
                            disabled={busy || active}
                            onClick={() => setQuery("SHOW DATABASES;")}
                          >
                            SHOW DATABASES
                          </Button>
                          <Button
                            variant="primary"
                            iconName="play"
                            disabled={!query.trim() || active || resultsLoading}
                            loading={submitting}
                            onClick={() => runQuery()}
                          >
                            Run Query
                          </Button>
                          <Button
                            disabled={!active}
                            loading={stopping}
                            onClick={stop}
                          >
                            Stop query
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      SQL query editor
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <ColumnLayout columns={2}>
                      <FormField
                        label="Workgroup"
                        description="Leave blank to use Athena's primary workgroup."
                      >
                        <Input
                          value={workgroup}
                          onChange={({ detail }) => setWorkgroup(detail.value)}
                          placeholder="primary"
                        />
                      </FormField>
                      <FormField
                        label="Result location (S3)"
                        description="Leave blank to inherit the workgroup's result configuration."
                      >
                        <Input
                          value={output}
                          onChange={({ detail }) => setOutput(detail.value)}
                          placeholder="s3://your-query-results/prefix/"
                        />
                      </FormField>
                      <FormField label="Data source (catalog)">
                        <Input
                          value={catalog}
                          onChange={({ detail }) => setCatalog(detail.value)}
                          placeholder="AwsDataCatalog"
                        />
                      </FormField>
                      <FormField label="Database">
                        <Input
                          value={database}
                          onChange={({ detail }) => setDatabase(detail.value)}
                          placeholder="Database name (optional)"
                        />
                      </FormField>
                    </ColumnLayout>
                    {chosenGroup && (
                      <Alert type="info">
                        Workgroup output:{" "}
                        {chosenGroup.Configuration?.ResultConfiguration
                          ?.OutputLocation || "Not configured"}
                        .{" "}
                        {chosenGroup.Configuration
                          ?.EnforceWorkGroupConfiguration
                          ? "This workgroup enforces its configuration."
                          : "Query settings can override workgroup settings."}
                      </Alert>
                    )}
                    <FormField label="SQL Query" stretch>
                      <Textarea
                        className="athena-sql-input"
                        rows={9}
                        value={query}
                        onChange={({ detail }) => setQuery(detail.value)}
                      />
                    </FormField>
                    {queryError && (
                      <Alert
                        type="error"
                        header="Unable to retrieve or submit query"
                      >
                        {queryError}
                        <Box>
                          <Button
                            onClick={() => setRevision((value) => value + 1)}
                            disabled={!id}
                          >
                            Retry execution status
                          </Button>
                        </Box>
                      </Alert>
                    )}
                  </SpaceBetween>
                </Container>
                {id && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <Button
                            iconName="refresh"
                            onClick={() => setRevision((value) => value + 1)}
                            loading={monitoring}
                          >
                            Refresh execution
                          </Button>
                        }
                      >
                        Execution details
                      </Header>
                    }
                  >
                    <SpaceBetween size="m">
                      <Box>
                        <span>Status: </span>
                        <QueryState state={state} />
                      </Box>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          {
                            label: "Execution ID",
                            value: (
                              <CopyToClipboard
                                variant="inline"
                                textToCopy={id}
                                copyButtonAriaLabel="Copy execution ID"
                                copySuccessText="Copied"
                                copyErrorText="Unable to copy"
                              />
                            ),
                          },
                          {
                            label: "Workgroup",
                            value: execution?.WorkGroup || "Not reported",
                          },
                          {
                            label: "Engine execution time",
                            value:
                              execution?.Statistics
                                ?.EngineExecutionTimeInMillis == null
                                ? "Not reported"
                                : `${execution.Statistics.EngineExecutionTimeInMillis} ms`,
                          },
                          {
                            label: "Total execution time",
                            value:
                              execution?.Statistics
                                ?.TotalExecutionTimeInMillis == null
                                ? "Not reported"
                                : `${execution.Statistics.TotalExecutionTimeInMillis} ms`,
                          },
                          {
                            label: "Data scanned",
                            value: athenaBytes(
                              execution?.Statistics?.DataScannedInBytes,
                            ),
                          },
                          {
                            label: "Output location",
                            value:
                              execution?.ResultConfiguration?.OutputLocation ||
                              "Not reported",
                          },
                          {
                            label: "Submitted",
                            value: athenaDate(
                              execution?.Status?.SubmissionDateTime,
                            ),
                          },
                          {
                            label: "Completed",
                            value: athenaDate(
                              execution?.Status?.CompletionDateTime,
                            ),
                          },
                          {
                            label: "Executed SQL",
                            value: (
                              <pre
                                style={{
                                  whiteSpace: "pre-wrap",
                                  overflowWrap: "anywhere",
                                  margin: 0,
                                }}
                              >
                                {execution?.Query || "Not reported"}
                              </pre>
                            ),
                          },
                        ]}
                      />
                      {execution?.Status?.StateChangeReason && (
                        <Alert type={state === "FAILED" ? "error" : "info"}>
                          {execution.Status.StateChangeReason}
                        </Alert>
                      )}
                    </SpaceBetween>
                  </Container>
                )}
                <Container
                  header={
                    <Header
                      variant="h2"
                      counter={`(${rows.length} ${rows.length === 1 ? "row" : "rows"} loaded)`}
                    >
                      Query results
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    {resultError && (
                      <Alert type="error" header="Unable to load query results">
                        {resultError}
                      </Alert>
                    )}
                    {updateCount != null && (
                      <Box>Rows updated: {updateCount}</Box>
                    )}
                    <Table
                      items={rows.map((values, index) => ({
                        id: String(index),
                        values,
                      }))}
                      loading={resultsLoading}
                      loadingText="Loading query results"
                      columnDefinitions={columns.map((column, index) => ({
                        id: column.id,
                        header: column.name,
                        minWidth: 150,
                        cell: (row: {
                          id: string;
                          values: (string | null)[];
                        }) =>
                          row.values[index] === null ? (
                            <Box color="text-body-secondary">NULL</Box>
                          ) : (
                            <span
                              style={{
                                whiteSpace: "pre-wrap",
                                overflowWrap: "anywhere",
                              }}
                            >
                              {row.values[index]}
                            </span>
                          ),
                      }))}
                      empty={
                        <Box textAlign="center">
                          {!id
                            ? "Run a SQL query above to view tabular results."
                            : state === "SUCCEEDED"
                              ? "No data returned by query."
                              : state === "FAILED" || state === "CANCELLED"
                                ? "This execution did not produce results."
                                : "Results will appear after the query succeeds."}
                        </Box>
                      }
                    />
                    {nextToken && (
                      <Button loading={resultsLoading} onClick={more}>
                        Load more results
                      </Button>
                    )}
                    {resultError && !nextToken && (
                      <Button onClick={() => setRevision((value) => value + 1)}>
                        Retry results
                      </Button>
                    )}
                  </SpaceBetween>
                </Container>
              </SpaceBetween>
            ),
          },
          {
            id: "history",
            label: `Recent Queries (${queries.length})`,
            content: (
              <Table
                items={queries.filter((item) =>
                  `${queryId(item)} ${item.Query || ""} ${item.WorkGroup || ""}`
                    .toLowerCase()
                    .includes(filter.toLowerCase()),
                )}
                loading={loading}
                header={<Header variant="h2">Query execution history</Header>}
                filter={
                  <TextFilter
                    filteringText={filter}
                    onChange={({ detail }) => setFilter(detail.filteringText)}
                    filteringPlaceholder="Find queries by SQL, execution ID, or workgroup"
                  />
                }
                columnDefinitions={[
                  {
                    id: "QueryExecutionId",
                    header: "Execution ID",
                    minWidth: 220,
                    cell: (item) => (
                      <Button
                        variant="inline-link"
                        disabled={busy || resultsLoading}
                        onClick={() => inspectQuery(item)}
                      >
                        {queryId(item)}
                      </Button>
                    ),
                  },
                  {
                    id: "Query",
                    header: "SQL",
                    minWidth: 240,
                    cell: (item) => (
                      <span style={{ whiteSpace: "pre-wrap" }}>
                        {item.Query || "Not reported"}
                      </span>
                    ),
                  },
                  {
                    id: "state",
                    header: "State",
                    minWidth: 150,
                    cell: (item) => <QueryState state={queryState(item)} />,
                  },
                  {
                    id: "WorkGroup",
                    header: "Workgroup",
                    cell: (item) => item.WorkGroup || "—",
                  },
                  {
                    id: "submitted",
                    header: "Submitted",
                    minWidth: 180,
                    cell: (item) => athenaDate(item.Status?.SubmissionDateTime),
                  },
                  {
                    id: "actions",
                    header: "Actions",
                    minWidth: 160,
                    cell: (item) => (
                      <Button
                        disabled={busy || active || !item.Query}
                        onClick={() => {
                          setQuery(item.Query);
                          setDatabase(
                            item.QueryExecutionContext?.Database || "",
                          );
                          setCatalog(item.QueryExecutionContext?.Catalog || "");
                          setWorkgroup(item.WorkGroup || "");
                          setOutput(
                            item.ResultConfiguration?.OutputLocation || "",
                          );
                          showTab("editor");
                        }}
                      >
                        Load query
                      </Button>
                    ),
                  },
                ]}
                empty="No recent query executions reported."
              />
            ),
          },
          {
            id: "workgroups",
            label: `Workgroups (${groups.length})`,
            content: (
              <SpaceBetween size="l">
                <Table
                  items={groups}
                  loading={loading}
                  header={<Header variant="h2">Athena workgroups</Header>}
                  columnDefinitions={[
                    {
                      id: "Name",
                      header: "Workgroup",
                      cell: (item) => (
                        <Button
                          variant="inline-link"
                          onClick={() => setSelectedWorkgroup(item.Name)}
                        >
                          {item.Name}
                        </Button>
                      ),
                    },
                    {
                      id: "State",
                      header: "State",
                      cell: (item) => (
                        <StatusIndicator
                          type={
                            item.State === "ENABLED"
                              ? "success"
                              : item.State === "DISABLED"
                                ? "stopped"
                                : "not-started"
                          }
                        >
                          {item.State || "Not reported"}
                        </StatusIndicator>
                      ),
                    },
                    {
                      id: "output",
                      header: "Result location",
                      minWidth: 240,
                      cell: (item) =>
                        item.Configuration?.ResultConfiguration
                          ?.OutputLocation || "Not configured",
                    },
                    {
                      id: "Description",
                      header: "Description",
                      cell: (item) => item.Description || "—",
                    },
                  ]}
                  empty="No workgroups reported."
                />
                {group && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <Button
                            disabled={busy || active}
                            onClick={() => {
                              setWorkgroup(group.Name);
                              showTab("editor");
                            }}
                          >
                            Use in query editor
                          </Button>
                        }
                      >
                        Workgroup: {group.Name}
                      </Header>
                    }
                  >
                    <KeyValuePairs
                      columns={2}
                      items={[
                        {
                          label: "Created",
                          value: athenaDate(group.CreationTime),
                        },
                        {
                          label: "Description",
                          value: group.Description || "—",
                        },
                        {
                          label: "Result location",
                          value:
                            group.Configuration?.ResultConfiguration
                              ?.OutputLocation || "Not configured",
                        },
                        {
                          label: "Enforce workgroup settings",
                          value:
                            group.Configuration
                              ?.EnforceWorkGroupConfiguration == null
                              ? "Not reported"
                              : group.Configuration
                                    .EnforceWorkGroupConfiguration
                                ? "Yes"
                                : "No",
                        },
                        {
                          label: "Selected engine version",
                          value:
                            group.Configuration?.EngineVersion
                              ?.SelectedEngineVersion || "Not reported",
                        },
                        {
                          label: "Effective engine version",
                          value:
                            group.Configuration?.EngineVersion
                              ?.EffectiveEngineVersion || "Not reported",
                        },
                        {
                          label: "Encryption",
                          value:
                            group.Configuration?.ResultConfiguration
                              ?.EncryptionConfiguration?.EncryptionOption ||
                            "Not configured",
                        },
                        {
                          label: "Data scan limit",
                          value: athenaBytes(
                            group.Configuration?.BytesScannedCutoffPerQuery,
                          ),
                        },
                      ]}
                    />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
        ]}
      />
      <Modal
        visible={createOpen}
        onDismiss={() => {
          if (!creating) setCreateOpen(false);
        }}
        header="Create Athena Workgroup"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button disabled={creating} onClick={() => setCreateOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creating}
                disabled={!wgName.trim()}
                onClick={create}
              >
                Create workgroup
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {createError && <Alert type="error">{createError}</Alert>}
          <FormField label="Workgroup name">
            <Input
              value={wgName}
              onChange={({ detail }) => setWgName(detail.value)}
            />
          </FormField>
          <FormField label="Description - optional">
            <Input
              value={wgDescription}
              onChange={({ detail }) => setWgDescription(detail.value)}
            />
          </FormField>
          <FormField label="Location of query result - optional" description="S3 path where Athena saves query results, such as s3://query-results-bucket/folder/.">
            <Input
              value={wgOutput}
              onChange={({ detail }) => setWgOutput(detail.value)}
              placeholder="s3://your-query-results/"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
