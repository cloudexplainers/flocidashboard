import React, { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Tabs from "@cloudscape-design/components/tabs";
import Textarea from "@cloudscape-design/components/textarea";
import TextFilter from "@cloudscape-design/components/text-filter";
import Table from "../components/ResourceTable";
import { secretsRequest } from "../api/client";
import { useSession } from "../components/SessionContext";
import {
  SecretResource,
  SecretVersion,
  secretDate,
  secretPath,
  secretResources,
} from "./secretResources";

type Props = { resourcePath?: string; onNavigate?: (path: string) => void };
const Field = ({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) => (
  <div>
    <Box variant="awsui-key-label">{label}</Box>
    <div style={{ overflowWrap: "anywhere" }}>{children || "—"}</div>
  </div>
);
const Rotation = ({ secret }: { secret: SecretResource }) => (
  <StatusIndicator
    type={
      secret.rotation === true
        ? "success"
        : secret.rotation === false
          ? "stopped"
          : "not-started"
    }
  >
    {secret.rotation === true
      ? "Enabled"
      : secret.rotation === false
        ? "Disabled"
        : "Not reported"}
  </StatusIndicator>
);
const Stages = ({ stages }: { stages: string[] }) => (
  <SpaceBetween direction="horizontal" size="xxs">
    {stages.length
      ? stages.map((stage) => (
          <Badge key={stage} color={stage === "AWSCURRENT" ? "green" : "blue"}>
            {stage}
          </Badge>
        ))
      : "—"}
  </SpaceBetween>
);

export const SecretsManagerConsole: React.FC<Props> = (props) => {
  const { identity, details } = useSession();
  const scope = JSON.stringify([
    identity?.arn,
    identity?.account_id,
    identity?.region,
    identity?.endpoint,
    identity?.expires_at,
    details?.session_identity?.access_key_id,
  ]);
  return <SecretWorkspace key={scope} {...props} />;
};

const SecretWorkspace: React.FC<Props> = ({
  resourcePath = "secretsmanager",
  onNavigate,
}) => {
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<SecretResource[]>([]);
  const [localPath, setLocalPath] = useState(resourcePath);
  const [tab, setTab] = useState("overview");
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  }>();
  const [modal, setModal] = useState<
    "" | "create" | "metadata" | "value" | "delete"
  >("");
  const [target, setTarget] = useState<SecretResource>();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [kmsKey, setKmsKey] = useState("");
  const [draft, setDraft] = useState("");
  const [value, setValue] = useState<any>();
  const [valueError, setValueError] = useState("");
  const [retrieving, setRetrieving] = useState(false);
  const [editingValue, setEditingValue] = useState(false);
  const [retrievedVersion, setRetrievedVersion] = useState("");
  const [formatJson, setFormatJson] = useState(false);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [modalError, setModalError] = useState("");
  const [forceDelete, setForceDelete] = useState(false);
  const [recovery, setRecovery] = useState("7");
  const mounted = useRef(true);
  const loadSequence = useRef(0);
  const valueSequence = useRef(0);
  const mutation = useRef(false);
  const navigate = (path: string) => {
    setLocalPath(path);
    onNavigate?.(path);
  };
  const path = onNavigate ? resourcePath : localPath;
  const secretName = new URLSearchParams(path.split("?")[1] || "").get(
    "secret",
  );
  const secrets = secretResources(data);
  const secret = secrets.find(
    (s) => s.name === secretName || s.id === secretName,
  );
  const endpoint = (s: SecretResource, suffix: string) =>
    `secrets/${encodeURIComponent(s.id)}/${suffix}/`;
  const load = async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    setLoadError("");
    try {
      const result = await secretsRequest();
      if (mounted.current && sequence === loadSequence.current) {
        setData(result);
        setSelected((previous) =>
          secretResources(result).filter((s) =>
            previous.some((p) => p.id === s.id),
          ),
        );
      }
    } catch (error: any) {
      if (mounted.current && sequence === loadSequence.current)
        setLoadError(error.message);
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
      valueSequence.current++;
    };
  }, []);
  const close = () => {
    if (mutation.current) return;
    valueSequence.current++;
    setModal("");
    setValue(undefined);
    setDraft("");
    setValueError("");
    setRetrieving(false);
    setTarget(undefined);
  };
  useEffect(() => {
    close();
    setTab("overview");
  }, [secretName]);
  const open = (
    kind: "create" | "metadata" | "delete",
    item?: SecretResource,
  ) => {
    setTarget(item);
    setName("");
    setDescription(item?.description || "");
    setKmsKey(item?.kmsKey || "");
    setDraft("");
    setModalError("");
    setForceDelete(false);
    setRecovery("7");
    setModal(kind);
  };
  const run = async (
    action: () => Promise<any>,
    success: string,
    after?: () => void,
  ) => {
    if (mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setModalError("");
    setMessage(undefined);
    try {
      await action();
      if (!mounted.current) return;
      valueSequence.current++;
      setModal("");
      setValue(undefined);
      setDraft("");
      setTarget(undefined);
      setMessage({ type: "success", text: success });
      after?.();
      await load();
    } catch (error: any) {
      if (mounted.current) {
        setModalError(error.message);
        setMessage({ type: "error", text: error.message });
      }
    } finally {
      mutation.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const reveal = async (item: SecretResource, version?: SecretVersion) => {
    const sequence = ++valueSequence.current;
    setTarget(item);
    setValue(undefined);
    setValueError("");
    setModalError("");
    setDraft("");
    setEditingValue(false);
    setFormatJson(false);
    setCopied(false);
    setRetrievedVersion(version?.id || "");
    setRetrieving(true);
    setModal("value");
    try {
      const result = await secretsRequest(
        endpoint(item, "value") +
          (version ? `?version_id=${encodeURIComponent(version.id)}` : ""),
      );
      if (mounted.current && sequence === valueSequence.current)
        setValue(result);
    } catch (error: any) {
      if (mounted.current && sequence === valueSequence.current)
        setValueError(error.message);
    } finally {
      if (mounted.current && sequence === valueSequence.current)
        setRetrieving(false);
    }
  };
  const filtered = secrets.filter((s) =>
    `${s.name} ${s.description} ${s.arn || ""} ${s.kmsKey || ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  let jsonValue = "";
  let validJson = false;
  if (value?.type === "string") {
    try {
      jsonValue = JSON.stringify(JSON.parse(value.value), null, 2);
      validJson = true;
    } catch {
      /* Plain text values are valid secrets. */
    }
  }
  const active = secrets.filter((s) => !s.deleted).length;
  const currentTarget = secret || selected[0];
  const isCurrentValue =
    !retrievedVersion ||
    target?.versions.some(
      (v) => v.id === retrievedVersion && v.stages.includes("AWSCURRENT"),
    );
  const actions = (item?: SecretResource) => (
    <SpaceBetween direction="horizontal" size="xs">
      {item?.deleted ? (
        <Button
          loading={busy}
          onClick={() =>
            run(
              () => secretsRequest(endpoint(item, "restore"), "POST", {}),
              `Restored ${item.name}.`,
            )
          }
        >
          Cancel deletion
        </Button>
      ) : (
        <>
          <Button
            disabled={!item}
            onClick={() => item && open("metadata", item)}
          >
            Edit details
          </Button>
          <Button disabled={!item} onClick={() => item && open("delete", item)}>
            Delete
          </Button>
        </>
      )}
    </SpaceBetween>
  );

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={
          secretName
            ? "Manage secret metadata, values, versions, and rotation settings."
            : "Store and manage application credentials, API keys, and other secrets."
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={load}>
              Refresh
            </Button>
            {!secretName && (
              <Button variant="primary" onClick={() => open("create")}>
                Store a new secret
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {secretName ? `Secret: ${secretName}` : "AWS Secrets Manager"}
      </Header>
      {message && (
        <Alert
          type={message.type}
          dismissible
          onDismiss={() => setMessage(undefined)}
        >
          {message.text}
        </Alert>
      )}
      {loadError && (
        <Alert type="error" header="Unable to load secrets">
          {loadError}
        </Alert>
      )}
      {secretName ? (
        <>
          <Button
            variant="link"
            iconName="arrow-left"
            onClick={() => navigate("secretsmanager")}
          >
            Secrets
          </Button>
          {loading && !secret ? (
            <StatusIndicator type="loading">
              Loading secret metadata
            </StatusIndicator>
          ) : !secret ? (
            <Alert type="warning" header="Secret not found">
              Refresh or return to the secrets list to select an available
              secret.
            </Alert>
          ) : (
            <>
              {secret.deleted && (
                <Alert type="warning" header="Scheduled for deletion">
                  This secret is scheduled for deletion on{" "}
                  {secretDate(secret.deleted)}. Cancel deletion to retrieve or
                  update its value.
                </Alert>
              )}
              <Container
                header={
                  <Header variant="h2" actions={actions(secret)}>
                    Secret details
                  </Header>
                }
              >
                <ColumnLayout columns={2} variant="text-grid">
                  <Field label="Secret name">{secret.name}</Field>
                  <Field label="Secret ARN">{secret.arn}</Field>
                  <Field label="Description">{secret.description}</Field>
                  <Field label="Encryption key">
                    {secret.kmsKey || "Default key (aws/secretsmanager)"}
                  </Field>
                  <Field label="Created">{secretDate(secret.created)}</Field>
                  <Field label="Last changed">
                    {secretDate(secret.changed)}
                  </Field>
                </ColumnLayout>
              </Container>
              <Tabs
                activeTabId={tab}
                onChange={({ detail }) => setTab(detail.activeTabId)}
                tabs={[
                  {
                    id: "overview",
                    label: "Overview",
                    content: (
                      <SpaceBetween size="l">
                        <Container
                          header={
                            <Header
                              variant="h2"
                              description="Retrieve a value explicitly. Opening metadata or refreshing this page does not retrieve secret values."
                              actions={
                                <Button
                                  variant="primary"
                                  disabled={!!secret.deleted}
                                  onClick={() => reveal(secret)}
                                >
                                  Retrieve secret value
                                </Button>
                              }
                            >
                              Secret value
                            </Header>
                          }
                        >
                          <Box color="text-body-secondary">
                            The secret value is hidden. Retrieved values are
                            cleared when you close the value dialog or leave
                            this secret.
                          </Box>
                        </Container>
                        <Container
                          header={<Header variant="h2">Lifecycle</Header>}
                        >
                          <ColumnLayout columns={3} variant="text-grid">
                            <Field label="Status">
                              <StatusIndicator
                                type={secret.deleted ? "warning" : "success"}
                              >
                                {secret.deleted
                                  ? "Scheduled for deletion"
                                  : "Active"}
                              </StatusIndicator>
                            </Field>
                            <Field label="Last accessed">
                              {secretDate(secret.accessed)}
                            </Field>
                            <Field label="Automatic rotation">
                              <Rotation secret={secret} />
                            </Field>
                          </ColumnLayout>
                        </Container>
                      </SpaceBetween>
                    ),
                  },
                  {
                    id: "versions",
                    label: `Versions (${secret.versions.length})`,
                    content: (
                      <Container
                        header={
                          <Header
                            variant="h2"
                            description="Staging labels identify the current, previous, and pending versions. Unreported labels are left blank."
                          >
                            Secret versions
                          </Header>
                        }
                      >
                        {secret.versionsError && (
                          <Alert type="warning">{secret.versionsError}</Alert>
                        )}
                        <Table
                          items={secret.versions}
                          columnDefinitions={[
                            {
                              id: "id",
                              header: "Version ID",
                              cell: (v) => (
                                <span style={{ overflowWrap: "anywhere" }}>
                                  {v.id}
                                </span>
                              ),
                              minWidth: 220,
                            },
                            {
                              id: "stages",
                              header: "Staging labels",
                              cell: (v) => <Stages stages={v.stages} />,
                              minWidth: 180,
                            },
                            {
                              id: "created",
                              header: "Created",
                              cell: (v) => secretDate(v.created),
                              minWidth: 170,
                            },
                            {
                              id: "actions",
                              header: "Value",
                              cell: (v) => (
                                <Button
                                  disabled={!!secret.deleted}
                                  onClick={() => reveal(secret, v)}
                                >
                                  Retrieve version
                                </Button>
                              ),
                              minWidth: 160,
                            },
                          ]}
                          empty={
                            <Box textAlign="center">No versions reported.</Box>
                          }
                        />
                      </Container>
                    ),
                  },
                  {
                    id: "rotation",
                    label: "Rotation",
                    content: (
                      <Container
                        header={
                          <Header
                            variant="h2"
                            description="Rotation settings reported by Secrets Manager."
                          >
                            Rotation configuration
                          </Header>
                        }
                      >
                        <ColumnLayout columns={2} variant="text-grid">
                          <Field label="Automatic rotation">
                            <Rotation secret={secret} />
                          </Field>
                          <Field label="Rotation Lambda ARN">
                            {secret.rotationLambda}
                          </Field>
                          <Field label="Rotation interval">
                            {secret.rotationRules.AutomaticallyAfterDays != null
                              ? `${secret.rotationRules.AutomaticallyAfterDays} days`
                              : "—"}
                          </Field>
                          <Field label="Schedule expression">
                            {secret.rotationRules.ScheduleExpression}
                          </Field>
                          <Field label="Last rotated">
                            {secretDate(secret.rotated)}
                          </Field>
                          <Field label="Next rotation">
                            {secretDate(secret.nextRotation)}
                          </Field>
                          <Field label="Rotation window">
                            {secret.rotationRules.Duration}
                          </Field>
                        </ColumnLayout>
                      </Container>
                    ),
                  },
                  {
                    id: "tags",
                    label: `Tags (${secret.tags.length})`,
                    content: (
                      <Container header={<Header variant="h2">Tags</Header>}>
                        <Table
                          items={secret.tags}
                          columnDefinitions={[
                            { id: "Key", header: "Key", cell: (t) => t.Key },
                            {
                              id: "Value",
                              header: "Value",
                              cell: (t) => t.Value || "—",
                            },
                          ]}
                          empty={
                            <Box textAlign="center">
                              No tags associated with this secret.
                            </Box>
                          }
                        />
                      </Container>
                    ),
                  },
                ]}
              />
            </>
          )}
        </>
      ) : (
        <>
          <Container header={<Header variant="h2">Secret overview</Header>}>
            <ColumnLayout columns={3} variant="text-grid">
              <Field label="Active secrets">
                <Box variant="h1">{loading ? "—" : active}</Box>
              </Field>
              <Field label="Automatic rotation enabled">
                <Box variant="h1">
                  {loading
                    ? "—"
                    : secrets.filter((s) => s.rotation === true && !s.deleted)
                        .length}
                </Box>
              </Field>
              <Field label="Scheduled for deletion">
                <Box variant="h1">
                  {loading ? "—" : secrets.filter((s) => s.deleted).length}
                </Box>
              </Field>
            </ColumnLayout>
          </Container>
          <Table
            loading={loading}
            loadingText="Loading secrets"
            items={filtered}
            selectionType="single"
            selectedItems={selected}
            onSelectionChange={({ detail }) =>
              setSelected(detail.selectedItems)
            }
            header={
              <Header
                variant="h2"
                counter={`(${filtered.length}${filter ? ` of ${secrets.length}` : ""})`}
                actions={actions(currentTarget)}
              >
                Secrets
              </Header>
            }
            filter={
              <TextFilter
                filteringText={filter}
                filteringPlaceholder="Find secrets by name, description, ARN, or encryption key"
                onChange={({ detail }) => setFilter(detail.filteringText)}
              />
            }
            columnDefinitions={[
              {
                id: "name",
                header: "Secret name",
                minWidth: 210,
                cell: (s) => (
                  <Button
                    variant="inline-link"
                    onClick={() => navigate(secretPath(s.name))}
                  >
                    {s.name}
                  </Button>
                ),
              },
              {
                id: "description",
                header: "Description",
                minWidth: 200,
                cell: (s) => s.description || "—",
              },
              {
                id: "status",
                header: "Status",
                minWidth: 180,
                cell: (s) => (
                  <StatusIndicator type={s.deleted ? "warning" : "success"}>
                    {s.deleted ? "Scheduled for deletion" : "Active"}
                  </StatusIndicator>
                ),
              },
              {
                id: "rotation",
                header: "Automatic rotation",
                minWidth: 150,
                cell: (s) => <Rotation secret={s} />,
              },
              {
                id: "changed",
                header: "Last changed",
                minWidth: 180,
                cell: (s) => secretDate(s.changed),
              },
            ]}
            empty={
              <Box textAlign="center">
                <b>{filter ? "No matching secrets" : "No secrets"}</b>
                <p>
                  {filter
                    ? "Adjust your search to find a secret."
                    : "Store a secret to get started."}
                </p>
              </Box>
            }
          />
        </>
      )}

      <Modal
        visible={modal === "create" || modal === "metadata"}
        onDismiss={close}
        size="large"
        header={
          modal === "create"
            ? "Store a new secret"
            : `Edit details: ${target?.name}`
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={close}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={
                  modal === "create" &&
                  (!name.trim() ||
                    !draft.length ||
                    !/^[a-zA-Z0-9/_+=.@-]+$/.test(name.trim()))
                }
                onClick={() =>
                  modal === "create"
                    ? run(
                        () =>
                          secretsRequest("secrets/", "POST", {
                            name: name.trim(),
                            description,
                            kms_key_id: kmsKey.trim(),
                            value: draft,
                          }),
                        `Stored ${name.trim()}.`,
                        () => navigate(secretPath(name.trim())),
                      )
                    : target &&
                      run(
                        () =>
                          secretsRequest(
                            endpoint(target, "metadata"),
                            "PATCH",
                            { description, kms_key_id: kmsKey.trim() },
                          ),
                        `Updated ${target.name}.`,
                      )
                }
              >
                {modal === "create" ? "Store secret" : "Save changes"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          {modal === "create" && (
            <FormField
              label="Secret name"
              description="Use letters, numbers, and /_+=.@- to organize secrets by application or environment."
            >
              <Input
                value={name}
                onChange={({ detail }) => setName(detail.value)}
                placeholder="production/database/credentials"
              />
            </FormField>
          )}
          <FormField label="Description">
            <Textarea
              value={description}
              onChange={({ detail }) => setDescription(detail.value)}
              rows={2}
            />
          </FormField>
          <FormField
            label="Encryption key"
            description="Optional KMS key ID, ARN, or alias. Leave blank to use the default key when creating a secret; blank preserves the existing key when editing."
          >
            <Input
              value={kmsKey}
              onChange={({ detail }) => setKmsKey(detail.value)}
              placeholder="alias/aws/secretsmanager"
            />
          </FormField>
          {modal === "create" && (
            <FormField
              label="Secret value"
              description="Enter JSON or plain text. The exact text will be stored as a secret string."
            >
              <Textarea
                value={draft}
                onChange={({ detail }) => setDraft(detail.value)}
                rows={8}
              />
            </FormField>
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={modal === "value"}
        onDismiss={close}
        size="large"
        header={`Secret value: ${target?.name || ""}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button disabled={busy} onClick={close}>
                Close
              </Button>
              {editingValue && (
                <Button
                  variant="primary"
                  loading={busy}
                  disabled={!draft.length}
                  onClick={() =>
                    target &&
                    run(
                      () =>
                        secretsRequest(endpoint(target, "value"), "PUT", {
                          value: draft,
                        }),
                      `Stored a new version of ${target.name}.`,
                    )
                  }
                >
                  Save new version
                </Button>
              )}
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {retrieving ? (
            <StatusIndicator type="loading">
              Retrieving secret value
            </StatusIndicator>
          ) : valueError ? (
            <Alert type="error">{valueError}</Alert>
          ) : (
            value && (
              <>
                {modalError && <Alert type="error">{modalError}</Alert>}
                <ColumnLayout columns={2} variant="text-grid">
                  <Field label="Version ID">{value.version_id}</Field>
                  <Field label="Staging labels">
                    <Stages stages={value.version_stages || []} />
                  </Field>
                  <Field label="Value type">
                    {value.type === "binary"
                      ? "Binary (base64)"
                      : "Secret string"}
                  </Field>
                  <Field label="Size">
                    {value.size_bytes == null
                      ? "—"
                      : `${value.size_bytes} bytes`}
                  </Field>
                </ColumnLayout>
                {editingValue ? (
                  <>
                    <Alert type="info">
                      Saving creates a new version with the AWSCURRENT staging
                      label. The previous current version becomes AWSPREVIOUS.
                    </Alert>
                    <FormField label="New secret value">
                      <Textarea
                        rows={8}
                        value={draft}
                        onChange={({ detail }) => setDraft(detail.value)}
                      />
                    </FormField>
                  </>
                ) : (
                  <>
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        iconName="copy"
                        onClick={async () => {
                          try {
                            await navigator.clipboard.writeText(
                              value.value ?? "",
                            );
                            setCopied(true);
                          } catch {
                            setModalError(
                              "Unable to copy. Select and copy the value manually.",
                            );
                          }
                        }}
                      >
                        {copied ? "Copied" : "Copy value"}
                      </Button>
                      {value.type === "string" && isCurrentValue && (
                        <Button
                          onClick={() => {
                            setDraft(value.value ?? "");
                            setEditingValue(true);
                          }}
                        >
                          Edit value
                        </Button>
                      )}
                      {validJson && (
                        <Checkbox
                          checked={formatJson}
                          onChange={({ detail }) =>
                            setFormatJson(detail.checked)
                          }
                        >
                          Format JSON
                        </Checkbox>
                      )}
                    </SpaceBetween>
                    <pre className="secret-value-output">
                      {formatJson ? jsonValue : (value.value ?? "")}
                    </pre>
                  </>
                )}
              </>
            )
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={modal === "delete"}
        onDismiss={close}
        header={`Delete secret: ${target?.name || ""}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={close}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                onClick={() =>
                  target &&
                  run(
                    () =>
                      secretsRequest(endpoint(target, "value"), "DELETE", {
                        recovery_window_days: Number(recovery),
                        force_delete_without_recovery: forceDelete,
                      }),
                    forceDelete
                      ? `Permanently deleted ${target.name}.`
                      : `Scheduled ${target.name} for deletion.`,
                    () => {
                      setSelected([]);
                      navigate("secretsmanager");
                    },
                  )
                }
              >
                {forceDelete ? "Permanently delete" : "Schedule deletion"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <Alert type="warning">
            {forceDelete
              ? "Permanent deletion cannot be undone."
              : "The secret cannot be retrieved while scheduled for deletion. You can cancel deletion during the recovery window."}
          </Alert>
          <Checkbox
            checked={forceDelete}
            onChange={({ detail }) => setForceDelete(detail.checked)}
          >
            Permanently delete without recovery
          </Checkbox>
          {!forceDelete && (
            <FormField label="Recovery window">
              <Select
                selectedOption={{ label: `${recovery} days`, value: recovery }}
                options={["7", "14", "30"].map((days) => ({
                  label: `${days} days`,
                  value: days,
                }))}
                onChange={({ detail }) =>
                  setRecovery(detail.selectedOption.value!)
                }
              />
            </FormField>
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
