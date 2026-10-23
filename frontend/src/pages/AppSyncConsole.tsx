import React, { useState, useEffect, useRef } from "react";
import Table from "../components/ResourceTable";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Container from "@cloudscape-design/components/container";
import Modal from "@cloudscape-design/components/modal";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { CodeSnippet } from "../components/CodeSnippet";
import { useSession } from "../components/SessionContext";
import { AppSyncOverview } from "../components/AppSyncOverview";
import {
  appSyncApis,
  appSyncExpiry,
  appSyncKeyState,
  AppSyncApi,
} from "./appsyncResources";
import {
  createAppSyncGraphQLApi,
  runAppSyncGraphQL,
  deleteAppSyncGraphQLApi,
  createAppSyncSchema,
  createAppSyncApiKey,
  deleteAppSyncApiKey,
  createAppSyncDataSource,
  deleteAppSyncDataSource,
  createAppSyncResolver,
  deleteAppSyncResolver,
} from "../api/client";

type Props = { resourcePath?: string; onNavigate?: (path: string) => void };
type Message = { type: "success" | "error" | "info"; text: string };
type Confirmation = {
  title: string;
  description: string;
  run: () => Promise<any>;
  destination?: string;
};
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const jsonObject = (text: string, label: string): Record<string, unknown> => {
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label} must be a JSON object.`);
  return value;
};
const schemaRequest = async (id: string) => {
  const response = await fetch(
    `/api/appsync/apis/${encodeURIComponent(id)}/schema/`,
  );
  const result = await response.json();
  if (!response.ok || result.error)
    throw new Error(result.error || "Unable to load schema.");
  return result;
};

export const AppSyncConsole: React.FC<Props> = (props) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <StatusIndicator type="loading">Loading session context</StatusIndicator>
    );
  const api = new URLSearchParams(props.resourcePath?.split("?")[1] || "").get(
    "api",
  );
  return (
    <AppSyncWorkspace
      key={JSON.stringify([
        api,
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

const AppSyncWorkspace: React.FC<Props> = ({
  resourcePath = "appsync",
  onNavigate,
}) => {
  const query = new URLSearchParams(resourcePath.split("?")[1] || "");
  const apiId = query.get("api") || "";
  const tab =
    resourcePath.split("?")[0].split("/")[1] || (apiId ? "overview" : "apis");
  const path = (section: string, id = apiId) =>
    `appsync/${section}${id ? `?api=${encodeURIComponent(id)}` : ""}`;
  const [apis, setApis] = useState<AppSyncApi[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");
  const [message, setMessage] = useState<Message | null>(null);
  const [modal, setModal] = useState("");
  const [busy, setBusy] = useState("");
  const lock = useRef(false);
  const alive = useRef(true);
  const generation = useRef(0);
  const revision = useRef(0);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [name, setName] = useState("");
  const [auth, setAuth] = useState("API_KEY");
  const [description, setDescription] = useState("");
  const [sourceType, setSourceType] = useState("NONE");
  const [sourceConfig, setSourceConfig] = useState("");
  const [sourceRole, setSourceRole] = useState("");
  const [typeName, setTypeName] = useState("Query");
  const [fieldName, setFieldName] = useState("");
  const [dataSource, setDataSource] = useState("");
  const [requestTemplate, setRequestTemplate] = useState("");
  const [responseTemplate, setResponseTemplate] = useState(
    "$util.toJson($ctx.result)",
  );
  const [expiry, setExpiry] = useState("");
  const [schema, setSchema] = useState("");
  const [schemaState, setSchemaState] = useState<any>(null);
  const [schemaError, setSchemaError] = useState("");
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaDirty, setSchemaDirty] = useState(false);
  const dirty = useRef(false);
  const [gqlQuery, setGqlQuery] = useState("query Example {\n  __typename\n}");
  const [variables, setVariables] = useState("{}");
  const [operationName, setOperationName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [result, setResult] = useState<any>(null);
  const [queryError, setQueryError] = useState("");
  const [resolverDetail, setResolverDetail] = useState<any>(null);
  const api = apis.find((item) => item.apiId === apiId);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      generation.current++;
      revision.current++;
    };
  }, []);
  const load = async () => {
    const current = ++generation.current;
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/appsync/");
      const payload = await response.json();
      if (!response.ok || payload.error)
        throw new Error(payload.error || "Unable to load AppSync APIs.");
      if (alive.current && current === generation.current)
        setApis(appSyncApis(payload));
    } catch (error: any) {
      if (alive.current && current === generation.current) {
        setLoadError(error.message);
        setApis([]);
      }
    } finally {
      if (alive.current && current === generation.current) setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  // Fetch the deployed SDL once. Inventory refreshes must not replace an edited draft.
  useEffect(() => {
    if (!api) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      setSchemaLoading(true);
      try {
        const payload = await schemaRequest(apiId);
        if (disposed || !alive.current) return;
        setSchemaState(payload);
        setSchemaError("");
        if (!dirty.current && typeof payload.definition === "string")
          setSchema(payload.definition);
        if (payload.status === "PROCESSING") timer = setTimeout(read, 2000);
      } catch (error: any) {
        if (!disposed && alive.current) setSchemaError(error.message);
      } finally {
        if (!disposed && alive.current) setSchemaLoading(false);
      }
    };
    void read();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [apiId, Boolean(api)]);

  const mutate = async (
    action: string,
    run: () => Promise<any>,
    success: string,
    destination?: string,
  ) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(action);
    setMessage(null);
    try {
      await run();
      if (!alive.current) return;
      setMessage({ type: "success", text: success });
      setModal("");
      setConfirmation(null);
      if (destination) onNavigate?.(destination);
      else await load();
    } catch (error: any) {
      if (alive.current)
        setMessage({
          type: "error",
          text: error.message || "AppSync operation failed.",
        });
    } finally {
      lock.current = false;
      if (alive.current) setBusy("");
    }
  };
  const open = (next: string) => {
    setName("");
    setDescription("");
    if (next === "resolver") {
      setDataSource("");
      setFieldName("");
      setRequestTemplate("");
    }
    if (next === "source") {
      setSourceType("NONE");
      setSourceRole("");
      setSourceConfig("");
    }
    if (next === "key") setExpiry("");
    setModal(next);
    setMessage(null);
  };
  const editQuery = () => {
    revision.current++;
    setResult(null);
    setQueryError("");
  };
  const execute = async () => {
    if (lock.current || !api) return;
    const current = ++revision.current;
    setResult(null);
    setQueryError("");
    lock.current = true;
    setBusy("query");
    try {
      const parsed = jsonObject(variables || "{}", "Query variables");
      const response = await runAppSyncGraphQL(
        apiId,
        gqlQuery,
        parsed,
        apiKey || undefined,
        operationName.trim() || undefined,
      );
      if (alive.current && current === revision.current) setResult(response);
    } catch (error: any) {
      if (alive.current && current === revision.current)
        setQueryError(error.message);
    } finally {
      lock.current = false;
      if (alive.current) setBusy("");
    }
  };
  const deploy = async () => {
    if (lock.current || !schema.trim()) return;
    lock.current = true;
    setBusy("schema");
    setMessage(null);
    try {
      const started = await createAppSyncSchema(apiId, schema);
      if (!alive.current) return;
      setSchemaState(started);
      setMessage({
        type: "info",
        text: `Schema submission reported ${started.status || "no status"}. Checking deployment status.`,
      });
      // Poll status without replacing the user's SDL draft.
      for (let attempt = 0; attempt < 30 && alive.current; attempt++) {
        const status = await schemaRequest(apiId);
        if (!alive.current) return;
        setSchemaState(status);
        if (status.status !== "PROCESSING") {
          const successful = ["SUCCESS", "ACTIVE"].includes(status.status);
          setMessage({
            type:
              status.status === "FAILED"
                ? "error"
                : successful
                  ? "success"
                  : "info",
            text: `Schema status: ${status.status || "Not reported"}${status.details ? `. ${status.details}` : ""}`,
          });
          if (successful) {
            dirty.current = false;
            setSchemaDirty(false);
          }
          await load();
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      if (alive.current)
        setMessage({
          type: "info",
          text: "Schema is still processing. Check deployment status before submitting again.",
        });
    } catch (error: any) {
      if (alive.current) setMessage({ type: "error", text: error.message });
    } finally {
      lock.current = false;
      if (alive.current) setBusy("");
    }
  };

  const filtered = apis.filter((item) =>
    `${item.name} ${item.apiId}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const sources = api?.data_sources || [];
  const resolvers = api?.resolvers || [];
  const keys = api?.api_keys || [];
  const schemaStatus = schemaState?.status || api?.schema_status?.status;
  const statusType =
    schemaStatus === "PROCESSING"
      ? "loading"
      : schemaStatus === "FAILED"
        ? "error"
        : ["SUCCESS", "ACTIVE"].includes(schemaStatus)
          ? "success"
          : "not-started";
  const confirmRemove = (
    title: string,
    detail: string,
    run: () => Promise<any>,
    destination?: string,
  ) => setConfirmation({ title, description: detail, run, destination });
  const apiTable = (
    <Table
      header={
        <Header variant="h2" counter={`(${filtered.length})`}>
          GraphQL APIs
        </Header>
      }
      loading={loading}
      loadingText="Loading AppSync APIs"
      items={filtered}
      filter={
        <TextFilter
          filteringText={filter}
          onChange={({ detail }) => setFilter(detail.filteringText)}
          filteringPlaceholder="Find APIs by name or ID"
        />
      }
      columnDefinitions={[
        {
          id: "name",
          header: "Name",
          cell: (item) => (
            <Button
              variant="inline-link"
              onClick={() => onNavigate?.(path("overview", item.apiId))}
            >
              {item.name}
            </Button>
          ),
          isRowHeader: true,
          sortingField: "name",
        },
        { id: "id", header: "API ID", cell: (item) => item.apiId },
        {
          id: "auth",
          header: "Authorization",
          cell: (item) => item.authenticationType || "Not reported",
        },
        {
          id: "schema",
          header: "Schema status",
          cell: (item) => item.schema_status?.status || "Not reported",
        },
        {
          id: "sources",
          header: "Data sources",
          cell: (item) => item.data_sources.length,
        },
        {
          id: "resolvers",
          header: "Resolvers",
          cell: (item) => item.resolvers.length,
        },
      ]}
      empty={
        <Box textAlign="center">
          No GraphQL APIs reported. Create an API to start configuring its
          schema and resolvers.
        </Box>
      }
    />
  );

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={
          apiId
            ? `API ID: ${apiId}`
            : "Define a GraphQL schema, connect data sources through resolvers, and test requests."
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={load}>
              Refresh
            </Button>
            {api ? (
              <Button
                onClick={() =>
                  confirmRemove(
                    "Delete GraphQL API",
                    `Delete ${api.name} and its AppSync configuration?`,
                    () => deleteAppSyncGraphQLApi(apiId),
                    "appsync/apis",
                  )
                }
              >
                Delete API
              </Button>
            ) : !apiId ? (
              <Button variant="primary" onClick={() => open("api")}>
                Create GraphQL API
              </Button>
            ) : null}
          </SpaceBetween>
        }
      >
        {apiId ? `API: ${api?.name || apiId}` : "AWS AppSync"}
      </Header>
      {apiId && (
        <Button
          variant="link"
          iconName="arrow-left"
          onClick={() => onNavigate?.("appsync/apis")}
        >
          APIs
        </Button>
      )}
      {loadError && (
        <Alert type="error" header="Unable to load AppSync">
          {loadError}
        </Alert>
      )}
      {message && !modal && !confirmation && (
        <Alert
          type={message.type}
          dismissible
          onDismiss={() => setMessage(null)}
        >
          {message.text}
        </Alert>
      )}
      {!apiId ? (
        <>
          {tab !== "apis" && (
            <Alert type="info">
              Select an API to view its schema, data sources, resolvers, keys,
              or queries.
            </Alert>
          )}
          {apiTable}
        </>
      ) : loading && !api ? (
        <StatusIndicator type="loading">Loading API workspace</StatusIndicator>
      ) : !api ? (
        !loadError && (
          <Alert type="warning" header="API not found">
            This API is not reported in the current session. Choose an available
            API from the list.
          </Alert>
        )
      ) : (
        <>
          {api.issues.length > 0 && (
            <Alert type="warning" header="Some API data could not be loaded">
              {api.issues.join(". ")}
            </Alert>
          )}
          <Tabs
            activeTabId={tab === "apis" ? "overview" : tab}
            onChange={({ detail }) => onNavigate?.(path(detail.activeTabId))}
            tabs={[
              {
                id: "overview",
                label: "Overview",
                content: <AppSyncOverview api={api} />,
              },
              {
                id: "schema",
                label: "Schema",
                content: (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        description="Load and edit this API’s deployed schema in GraphQL schema definition language (SDL)."
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              loading={schemaLoading}
                              onClick={async () => {
                                setSchemaLoading(true);
                                try {
                                  const payload = await schemaRequest(apiId);
                                  if (alive.current) {
                                    setSchemaState(payload);
                                    setSchemaError("");
                                    if (
                                      !dirty.current &&
                                      typeof payload.definition === "string"
                                    )
                                      setSchema(payload.definition);
                                  }
                                } catch (error: any) {
                                  if (alive.current)
                                    setSchemaError(error.message);
                                } finally {
                                  if (alive.current) setSchemaLoading(false);
                                }
                              }}
                            >
                              Check schema status
                            </Button>
                            <Button
                              variant="primary"
                              disabled={
                                !schema.trim() ||
                                schemaLoading ||
                                schemaStatus === "PROCESSING"
                              }
                              loading={busy === "schema"}
                              onClick={deploy}
                            >
                              Save & Deploy Schema
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        GraphQL schema
                      </Header>
                    }
                  >
                    <SpaceBetween size="m">
                      <StatusIndicator type={statusType}>
                        {schemaStatus || "Status not reported"}
                      </StatusIndicator>
                      {(schemaState?.details || api.schema_status?.details) && (
                        <Box>
                          {schemaState?.details || api.schema_status.details}
                        </Box>
                      )}
                      {schemaError && (
                        <Alert
                          type="error"
                          header="Unable to load deployed schema"
                        >
                          {schemaError}
                        </Alert>
                      )}
                      {schemaLoading && (
                        <StatusIndicator type="loading">
                          Loading schema
                        </StatusIndicator>
                      )}
                      {schemaDirty && (
                        <Alert type="info">
                          You have an edited schema draft. Refreshing inventory
                          does not replace it.
                        </Alert>
                      )}
                      <FormField
                        label="Schema Definition"
                        description="An empty editor means no deployed SDL was returned. Enter your schema before deploying."
                      >
                        <Textarea
                          rows={18}
                          value={schema}
                          disabled={busy === "schema"}
                          onChange={({ detail }) => {
                            dirty.current = true;
                            setSchemaDirty(true);
                            setSchema(detail.value);
                          }}
                        />
                      </FormField>
                    </SpaceBetween>
                  </Container>
                ),
              },
              {
                id: "datasources",
                label: `Data sources (${sources.length})`,
                content: (
                  <Table
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <Button
                            variant="primary"
                            onClick={() => open("source")}
                          >
                            Create Data Source
                          </Button>
                        }
                      >
                        Data sources
                      </Header>
                    }
                    items={sources}
                    columnDefinitions={[
                      {
                        id: "name",
                        header: "Name",
                        cell: (source: any) => source.name,
                        isRowHeader: true,
                      },
                      {
                        id: "type",
                        header: "Type",
                        cell: (source: any) => source.type || "Not reported",
                      },
                      {
                        id: "target",
                        header: "Resource",
                        cell: (source: any) =>
                          source.dynamodbConfig?.tableName ||
                          source.lambdaConfig?.lambdaFunctionArn ||
                          source.httpConfig?.endpoint ||
                          (source.type === "NONE"
                            ? "Local resolver · no external resource"
                            : "Not reported"),
                      },
                      {
                        id: "role",
                        header: "Service role",
                        cell: (source: any) => (
                          <span className="appsync-resource-value">
                            {source.serviceRoleArn || "Not configured"}
                          </span>
                        ),
                      },
                      {
                        id: "actions",
                        header: "Actions",
                        cell: (source: any) => (
                          <Button
                            onClick={() =>
                              confirmRemove(
                                "Delete data source",
                                `Delete data source ${source.name} from ${api.name}? Check any resolvers that reference it.`,
                                () =>
                                  deleteAppSyncDataSource(apiId, source.name),
                              )
                            }
                          >
                            Delete
                          </Button>
                        ),
                      },
                    ]}
                    empty={<Box>No data sources reported for this API.</Box>}
                  />
                ),
              },
              {
                id: "resolvers",
                label: `Resolvers (${resolvers.length})`,
                content: (
                  <SpaceBetween size="l">
                    <Table
                      header={
                        <Header
                          variant="h2"
                          description="Unit resolvers connect a field to a data source. Pipeline resolvers execute their configured functions in order."
                          actions={
                            <Button
                              variant="primary"
                              onClick={() => open("resolver")}
                            >
                              Attach Resolver
                            </Button>
                          }
                        >
                          Field resolvers
                        </Header>
                      }
                      items={resolvers}
                      columnDefinitions={[
                        {
                          id: "field",
                          header: "Field",
                          cell: (resolver: any) => (
                            <Button
                              variant="inline-link"
                              onClick={() => setResolverDetail(resolver)}
                            >
                              {resolver.typeName}.{resolver.fieldName}
                            </Button>
                          ),
                          isRowHeader: true,
                        },
                        {
                          id: "kind",
                          header: "Kind",
                          cell: (resolver: any) =>
                            resolver.kind || "Not reported",
                        },
                        {
                          id: "runtime",
                          header: "Runtime",
                          cell: (resolver: any) =>
                            resolver.runtime?.name ||
                            (resolver.requestMappingTemplate !== undefined ||
                            resolver.responseMappingTemplate !== undefined
                              ? "VTL"
                              : "Not reported"),
                        },
                        {
                          id: "source",
                          header: "Data source / pipeline",
                          cell: (resolver: any) =>
                            resolver.kind === "PIPELINE"
                              ? (resolver.pipelineConfig?.functions || []).join(
                                  " → ",
                                ) || "Functions not reported"
                              : resolver.dataSourceName || "Not reported",
                        },
                        {
                          id: "actions",
                          header: "Actions",
                          cell: (resolver: any) => (
                            <Button
                              onClick={() =>
                                confirmRemove(
                                  "Delete resolver",
                                  `Delete ${resolver.typeName}.${resolver.fieldName} from ${api.name}?`,
                                  () =>
                                    deleteAppSyncResolver(
                                      apiId,
                                      resolver.typeName,
                                      resolver.fieldName,
                                    ),
                                )
                              }
                            >
                              Delete
                            </Button>
                          ),
                        },
                      ]}
                      empty={<Box>No resolvers reported for this API.</Box>}
                    />
                    {resolverDetail && (
                      <Container
                        header={
                          <Header variant="h2">
                            {resolverDetail.typeName}.{resolverDetail.fieldName}
                          </Header>
                        }
                      >
                        <SpaceBetween size="m">
                          <KeyValuePairs
                            minColumnWidth={260}
                            columns={2}
                            items={[
                              {
                                label: "Resolver ARN",
                                value:
                                  resolverDetail.resolverArn || "Not reported",
                              },
                              {
                                label: "Data source",
                                value:
                                  resolverDetail.dataSourceName ||
                                  "Not reported",
                              },
                            ]}
                          />
                          {resolverDetail.code !== undefined ? (
                            <>
                              <Header variant="h3">Resolver code</Header>
                              <Textarea
                                value={resolverDetail.code}
                                readOnly
                                rows={10}
                              />
                            </>
                          ) : (
                            <>
                              <Header variant="h3">
                                Request mapping template
                              </Header>
                              <Textarea
                                value={
                                  resolverDetail.requestMappingTemplate ??
                                  "Not reported"
                                }
                                readOnly
                                rows={8}
                              />
                              <Header variant="h3">
                                Response mapping template
                              </Header>
                              <Textarea
                                value={
                                  resolverDetail.responseMappingTemplate ??
                                  "Not reported"
                                }
                                readOnly
                                rows={6}
                              />
                            </>
                          )}
                        </SpaceBetween>
                      </Container>
                    )}
                  </SpaceBetween>
                ),
              },
              {
                id: "apikeys",
                label: `API keys (${keys.length})`,
                content: (
                  <Table
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <Button variant="primary" onClick={() => open("key")}>
                            Generate API Key
                          </Button>
                        }
                      >
                        API keys
                      </Header>
                    }
                    items={keys}
                    columnDefinitions={[
                      {
                        id: "id",
                        header: "Key ID",
                        cell: (key: any) => key.id || "Not reported",
                        isRowHeader: true,
                      },
                      {
                        id: "description",
                        header: "Description",
                        cell: (key: any) => key.description || "Not configured",
                      },
                      {
                        id: "expires",
                        header: "Expires (UTC)",
                        cell: (key: any) => appSyncExpiry(key.expires),
                      },
                      {
                        id: "status",
                        header: "Status",
                        cell: (key: any) => (
                          <StatusIndicator
                            type={
                              appSyncKeyState(key) === "Active"
                                ? "success"
                                : appSyncKeyState(key) === "Expired"
                                  ? "stopped"
                                  : "not-started"
                            }
                          >
                            {appSyncKeyState(key)}
                          </StatusIndicator>
                        ),
                      },
                      {
                        id: "actions",
                        header: "Actions",
                        cell: (key: any) => (
                          <Button
                            disabled={!key.id}
                            onClick={() =>
                              confirmRemove(
                                "Delete API key",
                                `Delete key ${key.id} from ${api.name}? Clients using this key will lose access.`,
                                () => deleteAppSyncApiKey(apiId, key.id),
                              )
                            }
                          >
                            Delete
                          </Button>
                        ),
                      },
                    ]}
                    empty={<Box>No API keys reported for this API.</Box>}
                  />
                ),
              },
              {
                id: "sandbox",
                label: "Queries",
                content: (
                  <SpaceBetween size="l">
                    <Container
                      header={
                        <Header
                          variant="h2"
                          description="Run a query or mutation against this API through the current session’s local GraphQL endpoint."
                          actions={
                            <Button
                              variant="primary"
                              disabled={!gqlQuery.trim()}
                              loading={busy === "query"}
                              onClick={execute}
                            >
                              Execute Query
                            </Button>
                          }
                        >
                          GraphQL explorer
                        </Header>
                      }
                    >
                      <SpaceBetween size="m">
                        <KeyValuePairs
                          minColumnWidth={260}
                          columns={2}
                          items={[
                            { label: "API", value: api.name },
                            {
                              label: "Reported endpoint",
                              value: (
                                <span className="appsync-resource-value">
                                  {api.uris?.GRAPHQL || "Not reported"}
                                </span>
                              ),
                            },
                          ]}
                        />
                        <ColumnLayout columns={2}>
                          <FormField
                            label="API key"
                            description="Choose a reported API key when the API requires API_KEY authorization. Expired keys are excluded."
                          >
                            <Select
                              selectedOption={{
                                label:
                                  apiKey ||
                                  "No API key (unsigned local request)",
                                value: apiKey,
                              }}
                              options={[
                                {
                                  label: "No API key (unsigned local request)",
                                  value: "",
                                },
                                ...keys
                                  .filter(
                                    (key: any) =>
                                      key.id &&
                                      appSyncKeyState(key) !== "Expired",
                                  )
                                  .map((key: any) => ({
                                    label: key.id,
                                    value: key.id,
                                  })),
                              ]}
                              onChange={({ detail }) => {
                                editQuery();
                                setApiKey(detail.selectedOption.value || "");
                              }}
                            />
                          </FormField>
                          <FormField label="Operation name (optional)">
                            <Input
                              value={operationName}
                              onChange={({ detail }) => {
                                editQuery();
                                setOperationName(detail.value);
                              }}
                            />
                          </FormField>
                        </ColumnLayout>
                        <ColumnLayout columns={2}>
                          <FormField label="GraphQL Query / Mutation">
                            <Textarea
                              rows={12}
                              value={gqlQuery}
                              onChange={({ detail }) => {
                                editQuery();
                                setGqlQuery(detail.value);
                              }}
                            />
                          </FormField>
                          <FormField label="Query Variables (JSON)">
                            <Textarea
                              rows={12}
                              value={variables}
                              onChange={({ detail }) => {
                                editQuery();
                                setVariables(detail.value);
                              }}
                            />
                          </FormField>
                        </ColumnLayout>
                      </SpaceBetween>
                    </Container>
                    {queryError && <Alert type="error">{queryError}</Alert>}
                    {result && (
                      <Container
                        header={<Header variant="h2">Execution result</Header>}
                      >
                        <SpaceBetween size="m">
                          <Alert
                            type={
                              result.errors?.length ||
                              Number(result.status_code) >= 400
                                ? "error"
                                : result.status_code
                                  ? "success"
                                  : "info"
                            }
                          >
                            {result.errors?.length
                              ? "GraphQL returned errors. Partial data, when present, is shown below."
                              : Number(result.status_code) >= 400
                                ? "The GraphQL endpoint rejected the request."
                                : result.status_code
                                  ? "The endpoint returned a response."
                                  : "Response received; HTTP status was not reported."}
                          </Alert>
                          <KeyValuePairs
                            minColumnWidth={260}
                            columns={3}
                            items={[
                              {
                                label: "HTTP status",
                                value: String(
                                  result.status_code ?? "Not reported",
                                ),
                              },
                              {
                                label: "Request duration",
                                value:
                                  result.latency_ms !== undefined
                                    ? `${result.latency_ms} ms`
                                    : "Not reported",
                              },
                              {
                                label: "Runtime endpoint",
                                value: (
                                  <span className="appsync-resource-value">
                                    {result.endpoint_url || "Not reported"}
                                  </span>
                                ),
                              },
                            ]}
                          />
                          <FormField label="Response data">
                            <Textarea
                              rows={10}
                              value={pretty(result.data ?? null)}
                              readOnly
                            />
                          </FormField>
                          {result.errors && (
                            <>
                              <Header variant="h3">GraphQL errors</Header>
                              <CodeSnippet
                                language="json"
                                code={pretty(result.errors)}
                              />
                            </>
                          )}
                          {result.raw && (
                            <CodeSnippet
                              language="json"
                              code={pretty(result.raw)}
                            />
                          )}
                        </SpaceBetween>
                      </Container>
                    )}
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </>
      )}

      <Modal
        visible={modal !== ""}
        onDismiss={() => !busy && setModal("")}
        header={
          modal === "api"
            ? "Create GraphQL API"
            : modal === "source"
              ? "Create Data Source"
              : modal === "resolver"
                ? "Attach Field Resolver"
                : "Generate API Key"
        }
        size={modal === "resolver" ? "large" : "medium"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={Boolean(busy)}
                onClick={() => setModal("")}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={Boolean(busy)}
                disabled={
                  modal === "api" || modal === "source"
                    ? !name.trim()
                    : modal === "resolver"
                      ? !typeName.trim() ||
                        !fieldName.trim() ||
                        !dataSource ||
                        !requestTemplate.trim() ||
                        !responseTemplate.trim()
                      : false
                }
                onClick={() => {
                  if (modal === "api")
                    void mutate(
                      "api",
                      () =>
                        createAppSyncGraphQLApi(
                          name.trim(),
                          auth as "API_KEY" | "AWS_IAM",
                        ),
                      `API ${name.trim()} created.`,
                    );
                  else if (modal === "source")
                    void mutate(
                      "source",
                      async () => {
                        const config =
                          sourceType === "NONE"
                            ? undefined
                            : jsonObject(
                                sourceConfig,
                                "Data source configuration",
                              );
                        if (
                          ["AMAZON_DYNAMODB", "AWS_LAMBDA"].includes(
                            sourceType,
                          ) &&
                          !sourceRole.trim()
                        )
                          throw new Error(
                            "A service role ARN is required for this data source.",
                          );
                        return createAppSyncDataSource(
                          apiId,
                          name.trim(),
                          sourceType,
                          description,
                          {
                            ...(sourceRole.trim()
                              ? { service_role_arn: sourceRole.trim() }
                              : {}),
                            ...(config ? { config } : {}),
                          },
                        );
                      },
                      `Data source ${name.trim()} created.`,
                    );
                  else if (modal === "resolver")
                    void mutate(
                      "resolver",
                      () =>
                        createAppSyncResolver(
                          apiId,
                          typeName.trim(),
                          fieldName.trim(),
                          dataSource,
                          {
                            request_mapping_template: requestTemplate,
                            response_mapping_template: responseTemplate,
                          },
                        ),
                      `Resolver ${typeName}.${fieldName} attached.`,
                    );
                  else
                    void mutate(
                      "key",
                      async () => {
                        // The console asks for days until expiry (1-365, default 7).
                        // AppSync takes seconds since the epoch, rounded down to the hour.
                        const days = Number(expiry.trim() || "7");
                        if (!Number.isInteger(days) || days < 1 || days > 365)
                          throw new Error("Enter a whole number of days from 1 to 365.");
                        const seconds = Math.floor((Date.now() / 1000 + days * 86400) / 3600) * 3600;
                        return createAppSyncApiKey(apiId, description, seconds);
                      },
                      "API key generated.",
                    );
                }}
              >
                {modal === "api"
                  ? "Create API"
                  : modal === "source"
                    ? "Create Data Source"
                    : modal === "resolver"
                      ? "Attach Resolver"
                      : "Generate Key"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {(modal === "api" || modal === "source") && (
            <FormField
              label={modal === "api" ? "API Name" : "Data Source Name"}
            >
              <Input
                value={name}
                onChange={({ detail }) => setName(detail.value)}
              />
            </FormField>
          )}
          {modal === "api" && (
            <FormField label="Default Authorization Mode">
              <Select
                selectedOption={{ label: auth, value: auth }}
                options={[
                  { label: "API_KEY", value: "API_KEY" },
                  { label: "AWS_IAM", value: "AWS_IAM" },
                ]}
                onChange={({ detail }) =>
                  setAuth(detail.selectedOption.value || "API_KEY")
                }
              />
            </FormField>
          )}
          {modal === "source" && (
            <>
              <FormField label="Data Source Type">
                <Select
                  selectedOption={{ label: sourceType, value: sourceType }}
                  options={[
                    "NONE",
                    "AMAZON_DYNAMODB",
                    "AWS_LAMBDA",
                    "HTTP",
                  ].map((value) => ({
                    label: value === "NONE" ? "NONE (local resolver)" : value,
                    value,
                  }))}
                  onChange={({ detail }) =>
                    setSourceType(detail.selectedOption.value || "NONE")
                  }
                />
              </FormField>
              <FormField label="Description">
                <Input
                  value={description}
                  onChange={({ detail }) => setDescription(detail.value)}
                />
              </FormField>
              {sourceType !== "NONE" && (
                <>
                  <FormField
                    label="Service role ARN"
                    description="Required for DynamoDB and Lambda. The role must trust AppSync and permit access to the target resource."
                  >
                    <Input
                      value={sourceRole}
                      onChange={({ detail }) => setSourceRole(detail.value)}
                    />
                  </FormField>
                  <FormField
                    label="Data source configuration (JSON)"
                    description={
                      sourceType === "AMAZON_DYNAMODB"
                        ? "Use tableName and awsRegion."
                        : sourceType === "AWS_LAMBDA"
                          ? "Use lambdaFunctionArn."
                          : "Use endpoint with the HTTP or HTTPS URL."
                    }
                  >
                    <Textarea
                      rows={6}
                      value={sourceConfig}
                      onChange={({ detail }) => setSourceConfig(detail.value)}
                    />
                  </FormField>
                </>
              )}
            </>
          )}
          {modal === "resolver" && (
            <>
              <Alert type="info">
                Create a unit resolver using VTL mapping templates. Choose a
                data source that belongs to this API.
              </Alert>
              <ColumnLayout columns={2}>
                <FormField label="Type Name">
                  <Input
                    value={typeName}
                    onChange={({ detail }) => setTypeName(detail.value)}
                  />
                </FormField>
                <FormField label="Field Name">
                  <Input
                    value={fieldName}
                    onChange={({ detail }) => setFieldName(detail.value)}
                  />
                </FormField>
              </ColumnLayout>
              <FormField label="Data Source Name">
                <Select
                  selectedOption={
                    dataSource ? { label: dataSource, value: dataSource } : null
                  }
                  placeholder="Choose a data source"
                  options={sources.map((source: any) => ({
                    label: `${source.name} (${source.type || "Type not reported"})`,
                    value: source.name,
                  }))}
                  onChange={({ detail }) =>
                    setDataSource(detail.selectedOption.value || "")
                  }
                />
              </FormField>
              <FormField
                label="Request mapping template"
                description="Write the VTL request for your selected data source. A NONE source uses version and payload."
              >
                <Textarea
                  rows={8}
                  value={requestTemplate}
                  onChange={({ detail }) => setRequestTemplate(detail.value)}
                />
              </FormField>
              <FormField label="Response mapping template">
                <Textarea
                  rows={6}
                  value={responseTemplate}
                  onChange={({ detail }) => setResponseTemplate(detail.value)}
                />
              </FormField>
            </>
          )}
          {modal === "key" && (
            <>
              <FormField label="Description">
                <Input
                  value={description}
                  onChange={({ detail }) => setDescription(detail.value)}
                />
              </FormField>
              <FormField
                label="Expires after (days)"
                description="1 to 365 days. The default is 7 days."
              >
                <Input
                  type="number"
                  value={expiry}
                  placeholder="7"
                  onChange={({ detail }) => setExpiry(detail.value)}
                />
              </FormField>
            </>
          )}
          {modal && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={Boolean(confirmation)}
        onDismiss={() => !busy && setConfirmation(null)}
        header={confirmation?.title || "Confirm deletion"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={Boolean(busy)}
                onClick={() => setConfirmation(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy === "delete"}
                onClick={() =>
                  confirmation &&
                  mutate(
                    "delete",
                    confirmation.run,
                    `${confirmation.title} completed.`,
                    confirmation.destination,
                  )
                }
              >
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {confirmation?.description}
        {confirmation && message?.type === "error" && (
          <Alert type="error">{message.text}</Alert>
        )}
      </Modal>
    </SpaceBetween>
  );
};
