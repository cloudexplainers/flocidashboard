import { ApiRequestFlow } from "../components/ApiRequestFlow";
import React, { useEffect, useRef, useState } from "react";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import Modal from "@cloudscape-design/components/modal";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import {
  createApiGatewayApi,
  deleteApiGatewayApi,
  testApiGatewayRequest,
} from "../api/client";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import ConfirmActionModal from "../components/ConfirmActionModal";
import { useSession } from "../components/SessionContext";
import { ApiTestResponse } from "../components/ApiTestResponse";
import {
  gatewayApis,
  gatewayRows as rows,
  gatewayStageName as stageName,
  gatewayPath,
  gatewayDate,
  gatewayInvokeUrl,
  GatewayApi as Api,
} from "./apiGatewayResources";

type Props = {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
};
export const ApiGatewayConsole: React.FC<Props> = (props) => {
  const { identity, details, loading: sessionLoading } = useSession();
  if (!identity && sessionLoading)
    return (
      <SpaceBetween size="m">
        <Header variant="h1">Amazon API Gateway</Header>
        <StatusIndicator type="loading">
          Loading session context
        </StatusIndicator>
      </SpaceBetween>
    );
  return (
    <GatewayWorkspace
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
const GatewayWorkspace: React.FC<Props> = ({
  activeTab,
  onTabChange,
  resourcePath = "apigateway",
  onNavigate,
}) => {
  const mounted = useRef(true);
  const loadSequence = useRef(0);
  const requestSequence = useRef(0);
  const lock = useRef(false);
  const [protocolFilter, setProtocolFilter] = useState("all");
  const [requestError, setRequestError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Api | null>(null);
  const [selectedStageDetail, setSelectedStageDetail] = useState("");
  const [apis, setApis] = useState<Api[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState(activeTab || "overview");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [apiType, setApiType] = useState("HTTP");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [method, setMethod] = useState("GET");
  const [path, setPath] = useState("/");
  const [headers, setHeaders] = useState("{}");
  const [body, setBody] = useState("");
  const [result, setResult] = useState<any>(null);
  const queryApi = new URLSearchParams(resourcePath.split("?")[1] || "").get(
    "api",
  );
  const effectiveApiId = onNavigate ? queryApi || "" : selectedId;
  const api = apis.find((item) => item.id === effectiveApiId);
  const selectApi = (id: string) => {
    setSelectedId(id);
    setTab("overview");
    onNavigate?.(gatewayPath(id));
  };
  const stages = rows(api?.stages);
  const selectedStage = stages.some((item) => stageName(item) === stage)
    ? stage
    : stages.length
      ? stageName(stages[0])
      : "";
  const http = api?.api_type === "http";
  const currentApiId = useRef(effectiveApiId);
  currentApiId.current = effectiveApiId;

  useEffect(() => {
    if (activeTab) setTab(activeTab);
  }, [activeTab]);
  const load = async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    try {
      const response = await fetch("/api/apigateway/");
      const data = await response.json();
      if (!response.ok || data.error)
        throw new Error(data.error || "Unable to load APIs");
      if (!mounted.current || sequence !== loadSequence.current) return;
      setApis(gatewayApis(data));
    } catch (error: any) {
      if (mounted.current && sequence === loadSequence.current) {
        setNotice({ type: "error", text: error.message });
        setApis([]);
      }
    } finally {
      if (mounted.current && sequence === loadSequence.current)
        setLoading(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => {
      mounted.current = false;
      requestSequence.current++;
    };
  }, []);
  useEffect(() => {
    requestSequence.current++;
    setStage("");
    setResult(null);
    setPath("/");
    setHeaders("{}");
    setBody("");
    setRequestError("");
    setSelectedStageDetail("");
    setDeleteOpen(false);
  }, [effectiveApiId]);
  const run = async (task: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setNotice(null);
    try {
      await task();
    } catch (error: any) {
      if (mounted.current) setNotice({ type: "error", text: error.message });
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const showTab = (id: string) => {
    setTab(id);
    if (api && onNavigate)
      onNavigate(`apigateway/${id}?${new URLSearchParams({ api: api.id })}`);
    else onTabChange?.(id);
  };
  const configured = (value: unknown, label: string) => {
    const error =
      typeof value === "string"
        ? value
        : value && typeof value === "object" && "error" in value
          ? String(value.error)
          : "";
    return error ? (
      <Alert type="warning">
        {label} could not be retrieved: {error}
      </Alert>
    ) : null;
  };
  const empty = (text: string) => (
    <Box textAlign="center" padding="m">
      {text}
    </Box>
  );

  const visibleApis = apis.filter(
    (item) =>
      `${item.name} ${item.id} ${item.api_type}`
        .toLowerCase()
        .includes(filter.toLowerCase()) &&
      (protocolFilter === "all" || item.api_type === protocolFilter),
  );
  const stageDetail = stages.find(
    (item) => stageName(item) === selectedStageDetail,
  );
  const backToList = () => {
    setSelectedId("");
    setResult(null);
    onNavigate?.("apigateway");
  };
  const sendRequest = async () => {
    if (!api || lock.current) return;
    const sequence = ++requestSequence.current;
    const submitted = { api_id: api.id, stage: selectedStage, path, method };
    setRequestError("");
    setResult(null);
    lock.current = true;
    setBusy(true);
    try {
      const parsedHeaders = JSON.parse(headers);
      if (
        !parsedHeaders ||
        Array.isArray(parsedHeaders) ||
        typeof parsedHeaders !== "object" ||
        Object.values(parsedHeaders).some((value) => typeof value !== "string")
      )
        throw new Error(
          "Request headers must be a JSON object with string values",
        );
      if (
        !path.trim().startsWith("/") ||
        path.trim().startsWith("//") ||
        path.includes("://")
      )
        throw new Error("Enter a relative request path starting with /.");
      const response = await testApiGatewayRequest(
        api.id,
        path.trim(),
        method,
        parsedHeaders,
        body || null,
        api.api_type,
        selectedStage,
      );
      if (
        mounted.current &&
        sequence === requestSequence.current &&
        currentApiId.current === api.id
      )
        setResult({ ...response, submitted });
    } catch (error: any) {
      if (mounted.current && sequence === requestSequence.current)
        setRequestError(error.message);
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <SpaceBetween size="l" className="gateway-workspace">
      {notice && (
        <Alert type={notice.type} dismissible onDismiss={() => setNotice(null)}>
          {notice.text}
        </Alert>
      )}
      {!effectiveApiId && (
        <Container header={<Header variant="h2">API overview</Header>}>
          <ColumnLayout columns={3} variant="text-grid">
            {[
              ["Total APIs", apis.length],
              [
                "REST APIs",
                apis.filter((item) => item.api_type === "rest").length,
              ],
              [
                "HTTP APIs",
                apis.filter((item) => item.api_type === "http").length,
              ],
            ].map(([label, count]) => (
              <div key={label}>
                <Box variant="awsui-key-label">{label}</Box>
                <Box variant="h1">{loading ? "—" : count}</Box>
              </div>
            ))}
          </ColumnLayout>
        </Container>
      )}
      {!effectiveApiId && (
        <Table
          header={
            <Header
              variant="h1"
              counter={`(${visibleApis.length}${filter || protocolFilter !== "all" ? ` of ${apis.length}` : ""})`}
              description="REST and HTTP APIs in the active account and Region."
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="refresh" loading={loading} onClick={load}>
                    Refresh
                  </Button>

                  <Button variant="primary" onClick={() => setCreateOpen(true)}>
                    Create API
                  </Button>
                </SpaceBetween>
              }
            >
              Amazon API Gateway
            </Header>
          }
          filter={
            <SpaceBetween size="m">
              <FormField label="API protocol">
                <Select
                  selectedOption={{
                    label:
                      protocolFilter === "all"
                        ? "All protocols"
                        : protocolFilter.toUpperCase(),
                    value: protocolFilter,
                  }}
                  options={["all", "rest", "http"].map((value) => ({
                    label:
                      value === "all" ? "All protocols" : value.toUpperCase(),
                    value,
                  }))}
                  onChange={({ detail }) =>
                    setProtocolFilter(detail.selectedOption.value!)
                  }
                />
              </FormField>
              <TextFilter
                filteringText={filter}
                filteringPlaceholder="Find APIs by name, ID, or protocol"
                onChange={({ detail }) => {
                  setFilter(detail.filteringText);
                  setSelectedId("");
                }}
              />
            </SpaceBetween>
          }
          items={visibleApis}
          loading={loading}
          loadingText="Loading APIs"
          selectionType="single"
          trackBy="id"
          selectedItems={api ? [api] : []}
          onSelectionChange={({ detail }) => {
            const item = detail.selectedItems[0];
            if (item) selectApi(item.id);
          }}
          columnDefinitions={[
            {
              id: "name",
              header: "API name",
              minWidth: 220,
              cell: (item) => (
                <Button
                  variant="inline-link"
                  onClick={() => selectApi(item.id)}
                >
                  {item.name}
                </Button>
              ),
            },
            { id: "id", header: "API ID", cell: (item) => item.id },
            {
              id: "api_type",
              header: "Protocol",
              minWidth: 120,
              cell: (item) => item.api_type.toUpperCase(),
            },
            {
              id: "stage_count",
              header: "Stages",
              cell: (item) =>
                Array.isArray(item.stages) ? item.stages.length : "-",
            },
          ]}
          empty={empty(
            filter
              ? "No APIs match your filter."
              : "No APIs found. Create an API to get started.",
          )}
        />
      )}
      {effectiveApiId && (
        <Header
          variant="h1"
          description={
            api ? `${http ? "HTTP" : "REST"} API · ${api.id}` : "API details"
          }
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" loading={loading} onClick={load}>
                Refresh
              </Button>
              <Button onClick={() => setCreateOpen(true)}>Create API</Button>
              <Button
                disabled={!api}
                onClick={() => {
                  if (api) {
                    setDeleteTarget({ ...api });
                    setDeleteOpen(true);
                  }
                }}
              >
                Delete API
              </Button>
            </SpaceBetween>
          }
        >
          {api?.name || "API Gateway"}
        </Header>
      )}
      {effectiveApiId && (
        <Button variant="link" iconName="arrow-left" onClick={backToList}>
          APIs
        </Button>
      )}
      {queryApi && !loading && !api && (
        <Alert type="warning" header="API not found">
          The selected API is not reported in the current account and Region.
        </Alert>
      )}
      {api && (
        <Container
          header={
            <Header
              variant="h2"
              description={`${api.api_type.toUpperCase()} API · ${api.id}`}
            >
              API details
            </Header>
          }
        >
          <SpaceBetween size="m">
            <KeyValuePairs
              columns={3}
              items={[
                { label: "API ID", value: api.id },
                { label: "Protocol", value: api.api_type.toUpperCase() },
                {
                  label: "Deployed stages",
                  value:
                    stages.map(stageName).join(", ") ||
                    "No deployed stages reported",
                },
                { label: "Description", value: api.description || "—" },
                { label: "Created", value: gatewayDate(api.created) },
              ]}
            />

            <Tabs
              activeTabId={tab}
              onChange={({ detail }) => showTab(detail.activeTabId)}
              tabs={[
                {
                  id: "overview",
                  label: "Overview",
                  content: (
                    <SpaceBetween size="l">
                      <ApiRequestFlow
                        key={api.id}
                        api={api}
                        onTest={(verb, requestPath, flowStage) => {
                          setMethod(verb);
                          setPath(requestPath);
                          setStage(flowStage);
                          showTab("test");
                        }}
                      />
                      <Container
                        header={<Header variant="h2">Configuration</Header>}
                      >
                        <KeyValuePairs
                          columns={2}
                          items={
                            http
                              ? [
                                  {
                                    label: "Route selection expression",
                                    value:
                                      api.route_selection_expression ||
                                      api.RouteSelectionExpression ||
                                      "Not reported",
                                  },
                                  {
                                    label: "API endpoint",
                                    value:
                                      api.api_endpoint ||
                                      api.ApiEndpoint ||
                                      "Not reported",
                                  },
                                ]
                              : [
                                  {
                                    label: "Endpoint type",
                                    value:
                                      (
                                        api.endpoint_configuration?.types || []
                                      ).join(", ") || "Not reported",
                                  },
                                  {
                                    label: "API key source",
                                    value: api.api_key_source || "Not reported",
                                  },
                                ]
                          }
                        />
                      </Container>
                      <Container header={<Header variant="h2">Tags</Header>}>
                        <Table
                          items={Object.entries(api.tags || {}).map(
                            ([key, value]) => ({ key, value: String(value) }),
                          )}
                          columnDefinitions={[
                            {
                              id: "key",
                              header: "Key",
                              cell: (item) => item.key,
                            },
                            {
                              id: "value",
                              header: "Value",
                              cell: (item) => item.value,
                            },
                          ]}
                          empty="No tags associated with this API."
                        />
                      </Container>
                    </SpaceBetween>
                  ),
                },
                {
                  id: "resources",
                  label: http ? "Routes" : "Resources and methods",
                  content: (
                    <SpaceBetween size="m">
                      {configured(
                        http ? api.routes : api.resources,
                        http ? "Routes" : "Resources",
                      )}
                      {!http && (
                        <ExpandableSection
                          defaultExpanded
                          headerText="Resource and method tree"
                        >
                          <ul
                            aria-label="API resource paths"
                            style={{ listStyle: "none", padding: 0 }}
                          >
                            {rows(api.resources)
                              .slice()
                              .sort((a, b) =>
                                String(a.path || "/").localeCompare(
                                  String(b.path || "/"),
                                ),
                              )
                              .map((resource) => (
                                <li
                                  key={resource.id}
                                  style={{
                                    padding: "8px",
                                    paddingInlineStart: `${
                                      Math.max(
                                        0,
                                        String(resource.path || "/")
                                          .split("/")
                                          .filter(Boolean).length - 1,
                                      ) *
                                        20 +
                                      8
                                    }px`,
                                  }}
                                >
                                  <strong>{resource.path || "/"}</strong>
                                  {Object.keys(
                                    resource.resourceMethods || {},
                                  ).map((verb) => (
                                    <Button
                                      key={verb}
                                      variant="inline-link"
                                      onClick={() => {
                                        setMethod(
                                          verb === "ANY" ? "GET" : verb,
                                        );
                                        setPath(resource.path || "/");
                                        showTab("test");
                                      }}
                                    >
                                      {verb}
                                    </Button>
                                  ))}
                                  {!Object.keys(resource.resourceMethods || {})
                                    .length && <span> · No methods</span>}
                                </li>
                              ))}
                          </ul>
                        </ExpandableSection>
                      )}
                      <Table
                        items={rows(http ? api.routes : api.resources)}
                        columnDefinitions={
                          http
                            ? [
                                {
                                  id: "RouteKey",
                                  header: "Route",
                                  cell: (item: any) => (
                                    <Button
                                      variant="inline-link"
                                      onClick={() => {
                                        const [verb, ...parts] = (
                                          item.RouteKey || ""
                                        ).split(" ");
                                        setMethod(
                                          [
                                            "GET",
                                            "POST",
                                            "PUT",
                                            "PATCH",
                                            "DELETE",
                                            "HEAD",
                                            "OPTIONS",
                                          ].includes(verb)
                                            ? verb
                                            : "GET",
                                        );
                                        setPath(parts.join(" ") || "/");
                                        showTab("test");
                                      }}
                                    >
                                      {item.RouteKey}
                                    </Button>
                                  ),
                                },
                                {
                                  id: "RouteId",
                                  header: "Route ID",
                                  cell: (item: any) => item.RouteId,
                                },
                                {
                                  id: "Target",
                                  header: "Integration target",
                                  cell: (item: any) =>
                                    item.Target || "Not configured",
                                },
                                {
                                  id: "AuthorizationType",
                                  header: "Authorization",
                                  cell: (item: any) =>
                                    item.AuthorizationType || "Not reported",
                                },
                              ]
                            : [
                                {
                                  id: "path",
                                  header: "Resource path",
                                  cell: (item: any) => item.path || "/",
                                },
                                {
                                  id: "id",
                                  header: "Resource ID",
                                  cell: (item: any) => item.id,
                                },
                                {
                                  id: "methods",
                                  header: "HTTP methods",
                                  cell: (item: any) =>
                                    Object.keys(
                                      item.resourceMethods || {},
                                    ).join(", ") || "None",
                                },
                              ]
                        }
                        empty={empty(
                          http
                            ? "No routes configured."
                            : "No resources configured.",
                        )}
                      />
                    </SpaceBetween>
                  ),
                },
                {
                  id: "integrations",
                  label: "Integrations",
                  content: (
                    <SpaceBetween size="m">
                      {configured(api.integrations, "Integrations")}
                      <Table
                        items={
                          http
                            ? rows(api.integrations)
                            : rows(api.resources).flatMap((resource) =>
                                Object.entries(
                                  resource.resourceMethods || {},
                                ).flatMap(([verb, method]: [string, any]) =>
                                  method.methodIntegration
                                    ? [
                                        {
                                          ...method.methodIntegration,
                                          resource: `${verb} ${resource.path}`,
                                        },
                                      ]
                                    : [],
                                ),
                              )
                        }
                        columnDefinitions={[
                          {
                            id: "id",
                            header: http ? "Integration ID" : "Resource method",
                            cell: (item: any) =>
                              item.IntegrationId || item.resource,
                          },
                          {
                            id: "type",
                            header: "Type",
                            cell: (item: any) =>
                              item.IntegrationType ||
                              item.type ||
                              "Not reported",
                          },
                          {
                            id: "uri",
                            header: "URI",
                            cell: (item: any) =>
                              item.IntegrationUri || item.uri || "Not reported",
                          },
                        ]}
                        empty={empty("No integrations reported.")}
                      />
                    </SpaceBetween>
                  ),
                },
                {
                  id: "stages",
                  label: `Stages (${stages.length})`,
                  content: (
                    <SpaceBetween size="m">
                      {configured(api.stages, "Stages")}
                      <Table
                        items={stages}
                        columnDefinitions={[
                          {
                            id: "name",
                            header: "Stage name",
                            cell: (item: any) => (
                              <Button
                                variant="inline-link"
                                onClick={() =>
                                  setSelectedStageDetail(stageName(item))
                                }
                              >
                                {stageName(item)}
                              </Button>
                            ),
                          },
                          {
                            id: "deployment",
                            header: "Deployment ID",
                            cell: (item: any) =>
                              item.DeploymentId ||
                              item.deploymentId ||
                              "Not reported",
                          },
                          {
                            id: "auto",
                            header: "Automatic deployment",
                            cell: (item: any) =>
                              !http
                                ? "Manual deployment"
                                : item.AutoDeploy === undefined
                                  ? "Not reported"
                                  : item.AutoDeploy
                                    ? "Enabled"
                                    : "Disabled",
                          },
                        ]}
                        empty={empty("No stages deployed.")}
                      />
                      {stageDetail && (
                        <Container
                          header={
                            <Header variant="h2">
                              Stage: {stageName(stageDetail)}
                            </Header>
                          }
                        >
                          <SpaceBetween size="m">
                            <KeyValuePairs
                              columns={2}
                              items={[
                                {
                                  label: "Deployment ID",
                                  value:
                                    stageDetail.DeploymentId ||
                                    stageDetail.deploymentId ||
                                    "Not reported",
                                },
                                {
                                  label: "Deployment mode",
                                  value: http
                                    ? stageDetail.AutoDeploy === true
                                      ? "Automatic"
                                      : stageDetail.AutoDeploy === false
                                        ? "Manual"
                                        : "Not reported"
                                    : "Manual",
                                },
                                {
                                  label: "Description",
                                  value:
                                    stageDetail.Description ||
                                    stageDetail.description ||
                                    "—",
                                },
                                {
                                  label: "Last updated",
                                  value: gatewayDate(
                                    stageDetail.LastUpdatedDate ||
                                      stageDetail.lastUpdatedDate,
                                  ),
                                },
                                {
                                  label: "Invoke URL",
                                  value:
                                    gatewayInvokeUrl(
                                      api,
                                      stageName(stageDetail),
                                    ) || "Not reported",
                                },
                              ]}
                            />
                            <Button
                              onClick={() => {
                                setStage(stageName(stageDetail));
                                showTab("test");
                              }}
                            >
                              Test this stage
                            </Button>
                          </SpaceBetween>
                        </Container>
                      )}
                      <Header variant="h2">Deployments</Header>
                      {configured(api.deployments, "Deployments")}
                      <Table
                        items={rows(api.deployments)}
                        columnDefinitions={[
                          {
                            id: "id",
                            header: "Deployment ID",
                            cell: (item) => item.DeploymentId || item.id,
                          },
                          {
                            id: "created",
                            header: "Created",
                            cell: (item) =>
                              gatewayDate(item.CreatedDate || item.createdDate),
                          },
                          {
                            id: "description",
                            header: "Description",
                            cell: (item) =>
                              item.Description || item.description || "—",
                          },
                          {
                            id: "status",
                            header: "Status",
                            cell: (item) =>
                              item.DeploymentStatus ||
                              (http ? "Not reported" : "—"),
                          },
                          {
                            id: "stages",
                            header: "Referenced by stages",
                            cell: (item) =>
                              stages
                                .filter(
                                  (stage) =>
                                    (item.DeploymentId || item.id) &&
                                    (stage.DeploymentId ||
                                      stage.deploymentId) ===
                                      (item.DeploymentId || item.id),
                                )
                                .map(stageName)
                                .join(", ") || "None reported",
                          },
                        ]}
                        empty="No deployments reported."
                      />
                    </SpaceBetween>
                  ),
                },
                {
                  id: "authorizers",
                  label: "Authorizers",
                  content: (
                    <SpaceBetween size="m">
                      {configured(api.authorizers, "Authorizers")}
                      <Table
                        items={rows(api.authorizers)}
                        columnDefinitions={[
                          {
                            id: "name",
                            header: "Name",
                            cell: (item: any) => item.Name || item.name,
                          },
                          {
                            id: "id",
                            header: "Authorizer ID",
                            cell: (item: any) => item.AuthorizerId || item.id,
                          },
                          {
                            id: "type",
                            header: "Type",
                            cell: (item: any) =>
                              item.AuthorizerType || item.type,
                          },
                        ]}
                        empty={empty("No authorizers configured.")}
                      />
                    </SpaceBetween>
                  ),
                },
                {
                  id: "test",
                  label: "Test request",
                  content: (
                    <SpaceBetween size="m">
                      <Alert type="info">
                        This sends a real request through the selected stage in
                        the local environment. Requests can invoke integrations
                        and change application data.
                      </Alert>
                      {requestError && (
                        <Alert type="error">{requestError}</Alert>
                      )}
                      {selectedStage && (
                        <KeyValuePairs
                          items={[
                            {
                              label: "Stage invoke URL",
                              value:
                                gatewayInvokeUrl(api, selectedStage) ||
                                "Not reported",
                            },
                          ]}
                        />
                      )}
                      <FormField label="Stage">
                        <Select
                          ariaLabel="Request stage"
                          selectedOption={
                            selectedStage
                              ? { value: selectedStage, label: selectedStage }
                              : null
                          }
                          placeholder="Select a deployed stage"
                          options={stages.map((item) => ({
                            value: stageName(item),
                            label: stageName(item),
                          }))}
                          onChange={({ detail }) =>
                            setStage(detail.selectedOption.value!)
                          }
                        />
                      </FormField>
                      <FormField label="Method">
                        <Select
                          ariaLabel="Request method"
                          selectedOption={{ value: method, label: method }}
                          options={[
                            "GET",
                            "POST",
                            "PUT",
                            "PATCH",
                            "DELETE",
                            "HEAD",
                            "OPTIONS",
                          ].map((value) => ({ value, label: value }))}
                          onChange={({ detail }) =>
                            setMethod(detail.selectedOption.value!)
                          }
                        />
                      </FormField>
                      <FormField label="Request path">
                        <Input
                          value={path}
                          onChange={({ detail }) => setPath(detail.value)}
                        />
                      </FormField>
                      <FormField label="Request headers (JSON)">
                        <Textarea
                          value={headers}
                          onChange={({ detail }) => setHeaders(detail.value)}
                          rows={3}
                        />
                      </FormField>
                      <FormField label="Request body">
                        <Textarea
                          value={body}
                          onChange={({ detail }) => setBody(detail.value)}
                          rows={4}
                        />
                      </FormField>
                      {!selectedStage && (
                        <Alert type="info">
                          Deploy a stage before sending a test request.
                        </Alert>
                      )}
                      <Button
                        variant="primary"
                        disabled={!selectedStage || !path.trim()}
                        loading={busy}
                        onClick={sendRequest}
                      >
                        Send request
                      </Button>
                      {result && (
                        <ApiTestResponse
                          key={JSON.stringify(result)}
                          result={result}
                        />
                      )}
                    </SpaceBetween>
                  ),
                },
              ]}
            />
          </SpaceBetween>
        </Container>
      )}
      <Modal
        visible={createOpen}
        onDismiss={() => {
          if (!busy) setCreateOpen(false);
        }}
        header="Create API"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                disabled={!name.trim()}
                loading={busy}
                onClick={() =>
                  run(async () => {
                    await createApiGatewayApi(
                      name.trim(),
                      apiType,
                      description,
                    );
                    if (!mounted.current) return;
                    setCreateOpen(false);
                    setName("");
                    setDescription("");
                    await load();
                    setNotice({
                      type: "success",
                      text: `API "${name.trim()}" created.`,
                    });
                  })
                }
              >
                Create API
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {notice?.type === "error" && (
            <Alert type="error">{notice.text}</Alert>
          )}
          <FormField label="API name">
            <Input
              value={name}
              onChange={({ detail }) => setName(detail.value)}
            />
          </FormField>
          <FormField label="API type">
            <Select
              ariaLabel="API type"
              selectedOption={{ value: apiType, label: `${apiType} API` }}
              options={["HTTP", "REST"].map((value) => ({
                value,
                label: `${value} API`,
              }))}
              onChange={({ detail }) =>
                setApiType(detail.selectedOption.value!)
              }
            />
          </FormField>
          <FormField label="Description">
            <Input
              value={description}
              onChange={({ detail }) => setDescription(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
      <ConfirmActionModal
        visible={deleteOpen}
        header={`Delete API ${deleteTarget?.name || ""}`}
        confirmLabel="Delete"
        confirmationText="confirm"
        loading={busy}
        onDismiss={() => {
          if (!busy) setDeleteOpen(false);
        }}
        onConfirm={() =>
          run(async () => {
            if (!deleteTarget) return;
            await deleteApiGatewayApi(deleteTarget.id, deleteTarget.api_type);
            if (!mounted.current) return;
            setDeleteOpen(false);
            backToList();
            await load();
            setNotice({ type: "success", text: `API "${deleteTarget.name}" deleted.` });
          })
        }
      >
        {notice?.type === "error" ? `${notice.text} ` : ""}Deleting {deleteTarget?.name} ({deleteTarget?.id}) also deletes its routes or resources, integrations, stages and deployments. This can't be undone.
      </ConfirmActionModal>
    </SpaceBetween>
  );
};
