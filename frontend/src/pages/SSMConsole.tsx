import React, { useEffect, useMemo, useRef, useState } from "react";
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
import Checkbox from "@cloudscape-design/components/checkbox";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import Box from "@cloudscape-design/components/box";
import Badge from "@cloudscape-design/components/badge";
import Alert from "@cloudscape-design/components/alert";
import { useSession } from "../components/SessionContext";
import { SsmParameterDetails } from "../components/SsmParameterDetails";
import {
  ssmParameters,
  ssmDate,
  ssmRequest,
  parameterUrl,
} from "./ssmResources";

interface SSMConsoleProps {
  activeTab?: string;
  onTabChange?: (tab: string) => void;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
}
const copy = (value?: string) =>
  value ? (
    <CopyToClipboard
      variant="inline"
      textToCopy={value}
      copyButtonAriaLabel={`Copy ${value}`}
      copySuccessText="Copied"
      copyErrorText="Unable to copy"
    />
  ) : (
    "Unavailable"
  );
export const SSMConsole: React.FC<SSMConsoleProps> = ({
  activeTab,
  onTabChange,
  resourcePath,
  onNavigate,
}) => {
  const { identity } = useSession();
  const query = new URLSearchParams(
    (resourcePath || window.location.search).split("?").pop(),
  );
  const name = query.get("parameter") || "";
  const rawPath = query.get("path") || "";
  const path = rawPath ? `${rawPath.replace(/\/+$/, "")}/` : "";
  const requestedTab = (activeTab || "parameters").split("?")[0];
  const tab = name && requestedTab === "parameters" ? "details" : requestedTab;
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [revision, setRevision] = useState(0);
  const [filter, setFilter] = useState("");
  const [nested, setNested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [form, setForm] = useState<"create" | "edit" | "document" | null>(null);
  const [formName, setFormName] = useState("");
  const [formType, setFormType] = useState("String");
  const [formValue, setFormValue] = useState("");
  const [description, setDescription] = useState("");
  const [docType, setDocType] = useState("Command");
  const [confirmation, setConfirmation] = useState<{
    type: "parameter" | "document";
    name: string;
  } | null>(null);
  const requestId = useRef(0);
  const parameters = useMemo(() => ssmParameters(data), [data]);
  const parameter = parameters.find((item) => item.name === name);
  const documents: any[] = data.documents || [];
  const navigate = (nextTab: string, param = "", nextPath = path) => {
    const qs = new URLSearchParams();
    if (param) qs.set("parameter", param);
    if (nextPath) qs.set("path", nextPath);
    const target = `${nextTab}${qs.size ? `?${qs}` : ""}`;
    if (onNavigate) onNavigate(`ssm/${target}`);
    else onTabChange?.(target);
  };
  const loadData = async () => {
    const version = ++requestId.current;
    setLoading(true);
    setLoadError("");
    try {
      const result = await ssmRequest("/api/ssm/");
      if (version === requestId.current) {
        setData(result);
        setRevision((value) => value + 1);
      }
    } catch (err) {
      if (version === requestId.current)
        setLoadError(
          err instanceof Error ? err.message : "Unable to load SSM resources.",
        );
    } finally {
      if (version === requestId.current) setLoading(false);
    }
  };
  useEffect(() => {
    setData({});
    void loadData();
    return () => {
      requestId.current++;
    };
  }, [
    identity?.account_id,
    identity?.region,
    identity?.arn,
    identity?.endpoint,
  ]);
  useEffect(() => {
    setForm(null);
    setFormValue("");
    setConfirmation(null);
    setMessage(null);
  }, [name, path]);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (err) {
      setMessage({
        type: "error",
        text: err instanceof Error ? err.message : "Operation failed.",
      });
    } finally {
      setBusy(false);
    }
  };
  const openForm = (next: "create" | "edit" | "document") => {
    setForm(next);
    setMessage(null);
    setFormName(next === "edit" ? name : next === "create" ? path : "");
    setFormValue(
      next === "document"
        ? '{\n  "schemaVersion": "2.2",\n  "description": "Run a command",\n  "mainSteps": []\n}'
        : "",
    );
    setDescription(next === "edit" ? parameter?.description || "" : "");
    setFormType(next === "edit" ? parameter?.type || "String" : "String");
  };
  const saveForm = () =>
    run(async () => {
      if (form === "document") {
        try {
          JSON.parse(formValue);
        } catch {
          throw new Error("Enter valid JSON for the document.");
        }
        await ssmRequest("/api/ssm/documents/", "POST", {
          name: formName.trim(),
          content: formValue,
          document_type: docType,
        });
        setForm(null);
        setFormValue("");
        await loadData();
        setMessage({ type: "success", text: "Document created." });
      } else {
        const editing = form === "edit";
        await ssmRequest(
          editing ? parameterUrl(name) : "/api/ssm/parameters/",
          editing ? "PUT" : "POST",
          {
            name: formName.trim(),
            value: formValue,
            type: formType,
            description,
            overwrite: editing,
            ...(editing
              ? {
                  key_id: parameter?.keyId,
                  tier: parameter?.tier,
                  data_type: parameter?.dataType,
                }
              : {}),
          },
        );
        const savedName = editing ? name : formName.trim();
        setForm(null);
        setFormValue("");
        await loadData();
        navigate("details", savedName);
      }
    });
  const validName =
    /^[a-zA-Z0-9_.\-/]+$/.test(formName) &&
    !formName.endsWith("/") &&
    !formName.includes("//");
  const underPath = parameters.filter((item) => item.name.startsWith(path));
  const folders = [
    ...new Set(
      underPath
        .map((item) => {
          const remainder = item.name.slice(path.length).replace(/^\//, "");
          const slash = remainder.indexOf("/");
          return slash >= 0
            ? `${path || "/"}${remainder.slice(0, slash)}/`
            : "";
        })
        .filter(Boolean),
    ),
  ].sort();
  const visibleParameters = underPath.filter(
    (item) =>
      (nested ||
        !!filter ||
        !item.name.slice(path.length).replace(/^\//, "").includes("/")) &&
      `${item.name} ${item.description || ""} ${item.type || ""}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );
  const disabled = loading || !!loadError || busy;
  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={
          parameter
            ? "Inspect configuration, retrieve values, and manage parameter versions and tags."
            : tab === "documents"
              ? "Manage Systems Manager command documents and automation runbooks."
              : "Store configuration and encrypted values using hierarchical parameter paths."
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              iconName="refresh"
              loading={loading}
              disabled={busy}
              onClick={loadData}
            >
              Refresh
            </Button>
            {parameter ? (
              <>
                <Button
                  disabled={disabled || !parameter.type}
                  onClick={() => openForm("edit")}
                >
                  Edit parameter
                </Button>
                <Button
                  disabled={disabled}
                  onClick={() => setConfirmation({ type: "parameter", name })}
                >
                  Delete parameter
                </Button>
              </>
            ) : (
              <Button
                variant="primary"
                onClick={() =>
                  openForm(tab === "documents" ? "document" : "create")
                }
              >
                {tab === "documents" ? "Create document" : "Create parameter"}
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {parameter?.name ||
          (tab === "documents"
            ? "Systems Manager documents"
            : "Parameter Store")}
      </Header>
      {loadError && (
        <Alert type="error" header="Unable to load SSM resources">
          {loadError} Refresh to retry. Previously loaded resources may be out
          of date.
        </Alert>
      )}
      {message && !form && !confirmation && (
        <Alert
          type={message.type}
          dismissible
          onDismiss={() => setMessage(null)}
        >
          {message.text}
        </Alert>
      )}
      {name && !parameter ? (
        <Container>
          <Box textAlign="center" padding="l">
            <SpaceBetween size="m">
              <Box>
                {loading
                  ? "Loading parameter…"
                  : `Parameter "${name}" was not found in the current account and Region.`}
              </Box>
              <Button onClick={() => navigate("parameters")}>
                View parameters
              </Button>
            </SpaceBetween>
          </Box>
        </Container>
      ) : parameter ? (
        <>
          <Container header={<Header variant="h2">Parameter overview</Header>}>
            <KeyValuePairs
              columns={3}
              minColumnWidth={220}
              items={[
                { label: "Name", value: copy(parameter.name) },
                { label: "Type", value: parameter.type || "Unavailable" },
                { label: "Version", value: parameter.version ?? "Unavailable" },
                {
                  label: "Description",
                  value: parameter.description || "No description",
                },
                { label: "Last modified", value: ssmDate(parameter.modified) },
                {
                  label: "Modified by",
                  value: parameter.modifiedBy || "Unavailable",
                },
                { label: "Tier", value: parameter.tier || "Unavailable" },
                {
                  label: "Data type",
                  value: parameter.dataType || "Unavailable",
                },
                {
                  label: "KMS key",
                  value:
                    parameter.type === "SecureString"
                      ? copy(parameter.keyId)
                      : "Not applicable",
                },
              ]}
            />
          </Container>
          <Tabs
            activeTabId={tab}
            onChange={({ detail }) => navigate(detail.activeTabId, name)}
            tabs={[
              { id: "details", label: "Value" },
              { id: "history", label: "Version history" },
              { id: "tags", label: "Tags" },
            ]}
          />
          <SsmParameterDetails
            key={`${name}:${tab}:${revision}:${identity?.arn}:${identity?.endpoint}`}
            parameter={parameter}
            tab={tab}
            disabled={disabled}
          />
        </>
      ) : tab === "documents" ? (
        <Table
          loading={loading}
          loadingText="Loading documents"
          trackBy={(item) => item.name || item.Name}
          header={
            <Header variant="h2" counter={`(${documents.length})`}>
              Documents
            </Header>
          }
          items={documents}
          columnDefinitions={[
            {
              id: "name",
              header: "Name",
              cell: (item) => item.name || item.Name,
            },
            {
              id: "document_type",
              header: "Document type",
              cell: (item) =>
                item.document_type || item.DocumentType || "Unavailable",
            },
            {
              id: "status",
              header: "Status",
              cell: (item) => item.status || item.Status || "Unavailable",
            },
            {
              id: "document_version",
              header: "Version",
              cell: (item) =>
                item.document_version || item.DocumentVersion || "Unavailable",
            },
            {
              id: "owner",
              header: "Owner",
              cell: (item) => item.owner || item.Owner || "Unavailable",
            },
            {
              id: "actions",
              header: "Actions",
              cell: (item) => (
                <Button
                  disabled={disabled}
                  onClick={() =>
                    setConfirmation({
                      type: "document",
                      name: item.name || item.Name,
                    })
                  }
                >
                  Delete document
                </Button>
              ),
            },
          ]}
          empty={
            <Box textAlign="center" padding="l">
              No documents returned.
            </Box>
          }
        />
      ) : (
        <>
          <Container header={<Header variant="h2">Parameter paths</Header>}>
            <SpaceBetween size="m">
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="inline-link"
                  onClick={() => navigate("parameters", "", "")}
                >
                  All parameters
                </Button>
                {path && (
                  <>
                    <Box>{path}</Box>
                    <Button
                      onClick={() =>
                        navigate(
                          "parameters",
                          "",
                          path
                            .slice(0, -1)
                            .substring(
                              0,
                              path.slice(0, -1).lastIndexOf("/") + 1,
                            ),
                        )
                      }
                    >
                      Up one level
                    </Button>
                  </>
                )}
              </SpaceBetween>
              {folders.length ? (
                <SpaceBetween direction="horizontal" size="xs">
                  {folders.map((folder) => (
                    <Button
                      key={folder}
                      iconName="folder"
                      onClick={() => navigate("parameters", "", folder)}
                    >
                      {folder}
                    </Button>
                  ))}
                </SpaceBetween>
              ) : (
                <Box>No child paths.</Box>
              )}
            </SpaceBetween>
          </Container>
          <Table
            loading={loading}
            loadingText="Loading parameters"
            trackBy="name"
            header={
              <Header variant="h2" counter={`(${visibleParameters.length})`}>
                Parameters
              </Header>
            }
            filter={
              <SpaceBetween size="s">
                <TextFilter
                  filteringText={filter}
                  filteringAriaLabel="Find parameters"
                  filteringPlaceholder="Find by name, type, or description"
                  onChange={({ detail }) => setFilter(detail.filteringText)}
                />
                <Checkbox
                  checked={nested}
                  onChange={({ detail }) => setNested(detail.checked)}
                >
                  Include nested paths
                </Checkbox>
              </SpaceBetween>
            }
            items={visibleParameters}
            columnDefinitions={[
              {
                id: "name",
                header: "Name",
                sortingField: "name",
                cell: (item) => (
                  <Button
                    variant="inline-link"
                    onClick={() => navigate("details", item.name)}
                  >
                    {item.name}
                  </Button>
                ),
              },
              {
                id: "type",
                header: "Type",
                cell: (item) =>
                  item.type ? (
                    <Badge
                      color={item.type === "SecureString" ? "blue" : "grey"}
                    >
                      {item.type}
                    </Badge>
                  ) : (
                    "Unavailable"
                  ),
              },
              {
                id: "version",
                header: "Version",
                cell: (item) => item.version ?? "Unavailable",
              },
              {
                id: "tier",
                header: "Tier",
                cell: (item) => item.tier || "Unavailable",
              },
              {
                id: "modified",
                header: "Last modified",
                cell: (item) => ssmDate(item.modified),
              },
            ]}
            empty={
              <Box textAlign="center" padding="l">
                <SpaceBetween size="m">
                  <Box variant="strong">
                    {filter
                      ? "No matching parameters"
                      : "No parameters at this level"}
                  </Box>
                  <Box>
                    {folders.length
                      ? "Open a child path or include nested paths to view its parameters."
                      : "Create a parameter or adjust your filters."}
                  </Box>
                </SpaceBetween>
              </Box>
            }
          />
        </>
      )}
      <Modal
        visible={!!form}
        onDismiss={() => !busy && setForm(null)}
        size="large"
        header={
          form === "document"
            ? "Create document"
            : form === "edit"
              ? "Edit parameter"
              : "Create parameter"
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => {
                  setForm(null);
                  setFormValue("");
                }}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={
                  !formName.trim() ||
                  !formValue.length ||
                  (form !== "document" && !validName)
                }
                onClick={saveForm}
              >
                {form === "edit"
                  ? "Save new version"
                  : form === "document"
                    ? "Create document"
                    : "Create parameter"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          <FormField
            label={form === "document" ? "Document name" : "Parameter name"}
            description={
              form === "document"
                ? undefined
                : "Use a name or hierarchical path, such as /app/production/database."
            }
            errorText={
              form !== "document" && formName && !validName
                ? "Use letters, numbers, periods, hyphens, underscores, and path separators; do not end with /."
                : undefined
            }
          >
            <Input
              disabled={form === "edit"}
              value={formName}
              onChange={({ detail }) => setFormName(detail.value)}
            />
          </FormField>
          {form === "document" ? (
            <FormField label="Document type">
              <Select
                selectedOption={{ label: docType, value: docType }}
                options={["Command", "Automation", "Package"].map((value) => ({
                  label: value,
                  value,
                }))}
                onChange={({ detail }) =>
                  setDocType(detail.selectedOption.value!)
                }
              />
            </FormField>
          ) : (
            <>
              <FormField label="Parameter type">
                <Select
                  disabled={form === "edit"}
                  selectedOption={{ label: formType, value: formType }}
                  options={["String", "StringList", "SecureString"].map(
                    (value) => ({ label: value, value }),
                  )}
                  onChange={({ detail }) =>
                    setFormType(detail.selectedOption.value!)
                  }
                />
              </FormField>
              <FormField label="Description (optional)">
                <Input
                  value={description}
                  onChange={({ detail }) => setDescription(detail.value)}
                />
              </FormField>
            </>
          )}
          {form === "edit" && (
            <Alert type="info">
              Enter the replacement value. Saving creates a new version and
              preserves the parameter name and type.
            </Alert>
          )}
          <FormField
            label={
              form === "document"
                ? "Document content (JSON)"
                : form === "edit"
                  ? "New value"
                  : "Value"
            }
            description={
              form === "document"
                ? undefined
                : "The value is saved exactly as entered, including whitespace."
            }
          >
            <Textarea
              rows={form === "document" ? 10 : 4}
              value={formValue}
              onChange={({ detail }) => setFormValue(detail.value)}
            />
          </FormField>
          {form && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!confirmation}
        onDismiss={() => !busy && setConfirmation(null)}
        header={`Delete ${confirmation?.type || "resource"}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setConfirmation(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                onClick={() =>
                  run(async () => {
                    if (!confirmation) return;
                    await ssmRequest(
                      confirmation.type === "parameter"
                        ? parameterUrl(confirmation.name)
                        : "/api/ssm/documents/",
                      "DELETE",
                      confirmation.type === "document"
                        ? { name: confirmation.name }
                        : undefined,
                    );
                    const wasParameter = confirmation.type === "parameter";
                    setConfirmation(null);
                    await loadData();
                    if (wasParameter) navigate("parameters");
                    else
                      setMessage({
                        type: "success",
                        text: "Document deleted.",
                      });
                  })
                }
              >
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            Delete "{confirmation?.name}"?{" "}
            {confirmation?.type === "parameter"
              ? "All versions of this parameter will be deleted."
              : "Applications using this document may fail."}
          </Alert>
          {confirmation && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
