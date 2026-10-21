import React, { useEffect, useMemo, useRef, useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Multiselect from "@cloudscape-design/components/multiselect";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import { executeKmsAction } from "../api/client";
import { useSession } from "../components/SessionContext";
import { KmsCryptoTools } from "../components/KmsCryptoTools";
import { KmsKey, kmsKeys, kmsDate, canRotateKmsKey } from "./kmsResources";

interface KMSConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
}
const unavailable = "Unavailable";
const grantOperations = [
  "Encrypt",
  "Decrypt",
  "ReEncryptFrom",
  "ReEncryptTo",
  "GenerateDataKey",
  "GenerateDataKeyWithoutPlaintext",
  "DescribeKey",
  "CreateGrant",
  "RetireGrant",
  "Sign",
  "Verify",
  "GenerateMac",
  "VerifyMac",
];
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
    unavailable
  );
const keyStatus = (key: KmsKey) => (
  <StatusIndicator
    type={
      key.state === "Enabled"
        ? "success"
        : key.state === "Disabled"
          ? "stopped"
          : key.state?.startsWith("Pending")
            ? "warning"
            : "info"
    }
  >
    {key.state || unavailable}
  </StatusIndicator>
);

export const KMSConsole: React.FC<KMSConsoleProps> = ({
  activeTab,
  onTabChange,
  resourcePath,
  onNavigate,
}) => {
  const { identity } = useSession();
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [policyDoc, setPolicyDoc] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [alias, setAlias] = useState("");
  const [spec, setSpec] = useState("SYMMETRIC_DEFAULT");
  const [usage, setUsage] = useState("ENCRYPT_DECRYPT");
  const [grantOpen, setGrantOpen] = useState(false);
  const [principal, setPrincipal] = useState("");
  const [operations, setOperations] = useState<string[]>([
    "Decrypt",
    "GenerateDataKey",
  ]);
  const [confirmation, setConfirmation] = useState<{
    kind: "disable" | "revoke";
    grantId?: string;
  } | null>(null);
  const requestId = useRef(0);
  const keyId =
    new URLSearchParams(
      (resourcePath || window.location.search).split("?").pop(),
    ).get("key") || "";
  const context = useRef(keyId);
  context.current = keyId;
  const keys = useMemo(() => kmsKeys(data), [data]);
  const key = keys.find((item) => item.id === keyId);
  const requestedTab = (activeTab || "keys").split("?")[0];
  const tab = ["workbench", "crypto", "datakey"].includes(requestedTab)
    ? "tools"
    : requestedTab === "keys" || requestedTab === "lifecycle"
      ? "details"
      : requestedTab;
  const navigate = (nextTab: string, id = "") => {
    const target = `${nextTab}${id ? `?key=${encodeURIComponent(id)}` : ""}`;
    if (onNavigate) onNavigate(`kms/${target}`);
    else onTabChange?.(target);
  };
  const loadData = async () => {
    const version = ++requestId.current;
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/kms/");
      const result = await response.json();
      if (!response.ok || result.error)
        throw new Error(result.error || `HTTP ${response.status}`);
      if (version === requestId.current) setData(result);
    } catch (error) {
      if (version === requestId.current)
        setLoadError(
          error instanceof Error
            ? error.message
            : "Unable to load KMS resources.",
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
    setPolicyDoc(key?.policy || "");
  }, [key?.id, key?.policy]);
  useEffect(() => {
    setMessage(null);
    setGrantOpen(false);
    setPrincipal("");
    setConfirmation(null);
  }, [keyId]);
  const run = async (action: () => Promise<void>) => {
    const originalKey = keyId;
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (error) {
      if (context.current === originalKey)
        setMessage({
          type: "error",
          text: error instanceof Error ? error.message : "Operation failed.",
        });
    } finally {
      setBusy(false);
    }
  };
  const mutate = (
    action: string,
    payload: Record<string, unknown>,
    success: string,
  ) =>
    run(async () => {
      await executeKmsAction(action, { key_id: keyId, ...payload });
      setConfirmation(null);
      await loadData();
      if (context.current === keyId)
        setMessage({ type: "success", text: success });
    });
  const managed = key?.manager === "CUSTOMER";
  const allowedUsages = spec.startsWith("ECC")
    ? ["SIGN_VERIFY"]
    : spec.startsWith("HMAC")
      ? ["GENERATE_VERIFY_MAC"]
      : spec.startsWith("RSA")
        ? ["ENCRYPT_DECRYPT", "SIGN_VERIFY"]
        : ["ENCRYPT_DECRYPT"];
  const fullAlias = alias.trim().startsWith("alias/")
    ? alias.trim()
    : `alias/${alias.trim()}`;
  const aliasValid =
    !alias.trim() ||
    (/^alias\/[a-zA-Z0-9/_-]+$/.test(fullAlias) &&
      !fullAlias.startsWith("alias/aws/") &&
      fullAlias.length <= 256);
  const createKey = () =>
    run(async () => {
      const result = await executeKmsAction("create_key", {
        description,
        key_spec: spec,
        key_usage: usage,
      });
      const createdId =
        result.key_id || result.metadata?.KeyId || result.KeyMetadata?.KeyId;
      if (!createdId)
        throw new Error(
          "The key was created, but no key ID was returned. Refresh the key list before retrying.",
        );
      let aliasError = "";
      if (alias.trim()) {
        try {
          await executeKmsAction("create_alias", {
            alias_name: fullAlias,
            target_key_id: createdId,
          });
        } catch (error) {
          aliasError =
            error instanceof Error ? error.message : "Alias creation failed.";
        }
      }
      setCreateOpen(false);
      setDescription("");
      setAlias("");
      await loadData();
      navigate("details", createdId);
      // Keep partial creation visible on the destination instead of inviting another CreateKey request.
      setCreationNotice({
        keyId: createdId,
        text: aliasError
          ? `Key created, but its alias could not be created: ${aliasError}`
          : "Key created.",
        error: !!aliasError,
      });
    });
  const [creationNotice, setCreationNotice] = useState<{
    keyId: string;
    text: string;
    error: boolean;
  } | null>(null);
  const visibleKeys = keys.filter(
    (item) =>
      `${item.id} ${item.aliases.join(" ")} ${item.description || ""}`
        .toLowerCase()
        .includes(filter.toLowerCase()) &&
      (stateFilter === "all" || item.state === stateFilter),
  );

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={
          key
            ? "Manage key configuration, access, and cryptographic operations."
            : "Manage your KMS keys, aliases, and access policies."
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
            {key ? (
              managed &&
              ["Enabled", "Disabled"].includes(key.state || "") && (
                <Button
                  loading={busy}
                  disabled={loading || !!loadError}
                  onClick={() =>
                    key.state === "Enabled"
                      ? setConfirmation({ kind: "disable" })
                      : mutate(
                          "set_key_enabled",
                          { enabled: true },
                          "Key enabled.",
                        )
                  }
                >
                  {key.state === "Enabled" ? "Disable key" : "Enable key"}
                </Button>
              )
            ) : (
              <Button variant="primary" onClick={() => setCreateOpen(true)}>
                Create key
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {key ? key.aliases[0] || key.id : "KMS keys"}
      </Header>
      {loadError && (
        <Alert type="error" header="Unable to load KMS resources">
          {loadError} Refresh to retry. Previously loaded key details may be out
          of date.
        </Alert>
      )}
      {key?.issues.length ? (
        <Alert type="warning" header="Some key details are unavailable">
          {key.issues.join(" ")} Refresh to retry.
        </Alert>
      ) : null}
      {message && !createOpen && !grantOpen && !confirmation && (
        <Alert
          type={message.type}
          dismissible
          onDismiss={() => setMessage(null)}
        >
          {message.text}
        </Alert>
      )}
      {creationNotice?.keyId === keyId && (
        <Alert
          type={creationNotice.error ? "warning" : "success"}
          dismissible
          onDismiss={() => setCreationNotice(null)}
        >
          {creationNotice.text}
        </Alert>
      )}
      {keyId && !key ? (
        <Container>
          <Box textAlign="center" padding="l">
            <SpaceBetween size="m">
              <Box>
                {loading
                  ? "Loading key…"
                  : `Key "${keyId}" was not found in the current account and Region.`}
              </Box>
              <Button onClick={() => navigate("keys")}>View keys</Button>
            </SpaceBetween>
          </Box>
        </Container>
      ) : !key ? (
        <>
          <Container header={<Header variant="h2">Key overview</Header>}>
            <KeyValuePairs
              minColumnWidth={220}
              columns={3}
              items={[
                { label: "Keys", value: loading ? "Loading…" : keys.length },
                {
                  label: "Enabled keys",
                  value: loading
                    ? "Loading…"
                    : keys.filter((item) => item.state === "Enabled").length,
                },
                {
                  label: "Pending deletion",
                  value: loading
                    ? "Loading…"
                    : keys.filter(
                        (item) =>
                          item.state?.startsWith("Pending") &&
                          item.state.includes("Deletion"),
                      ).length,
                },
              ]}
            />
          </Container>
          <Table
            loading={loading}
            loadingText="Loading keys"
            trackBy="id"
            header={
              <Header variant="h2" counter={`(${keys.length})`}>
                Keys
              </Header>
            }
            filter={
              <SpaceBetween direction="horizontal" size="s">
                <TextFilter
                  filteringText={filter}
                  filteringAriaLabel="Find keys"
                  filteringPlaceholder="Find by alias, key ID, or description"
                  onChange={({ detail }) => setFilter(detail.filteringText)}
                />
                <Select
                  ariaLabel="Key state"
                  selectedOption={{
                    label:
                      stateFilter === "all" ? "All key states" : stateFilter,
                    value: stateFilter,
                  }}
                  options={[
                    "all",
                    ...new Set(
                      keys
                        .map((item) => item.state)
                        .filter((state): state is string => !!state),
                    ),
                  ].map((value) => ({
                    value,
                    label: value === "all" ? "All key states" : value,
                  }))}
                  onChange={({ detail }) =>
                    setStateFilter(detail.selectedOption.value!)
                  }
                />
              </SpaceBetween>
            }
            items={visibleKeys}
            columnDefinitions={[
              {
                id: "aliases",
                header: "Alias",
                cell: (item) =>
                  item.aliases.length ? (
                    <Button
                      variant="inline-link"
                      onClick={() => navigate("details", item.id)}
                    >
                      {item.aliases.join(", ")}
                    </Button>
                  ) : (
                    "No alias"
                  ),
              },
              {
                id: "id",
                header: "Key ID",
                sortingField: "id",
                cell: (item) => (
                  <Button
                    variant="inline-link"
                    onClick={() => navigate("details", item.id)}
                  >
                    {item.id}
                  </Button>
                ),
              },
              {
                id: "state",
                header: "Status",
                sortingField: "state",
                cell: keyStatus,
              },
              {
                id: "spec",
                header: "Key spec",
                cell: (item) => item.spec || unavailable,
              },
              {
                id: "usage",
                header: "Key usage",
                cell: (item) => item.usage || unavailable,
              },
              {
                id: "manager",
                header: "Key manager",
                cell: (item) => item.manager || unavailable,
              },
            ]}
            empty={
              <Box textAlign="center" padding="l">
                <SpaceBetween size="m">
                  <Box variant="strong">
                    {filter || stateFilter !== "all"
                      ? "No matching keys"
                      : "No keys"}
                  </Box>
                  <Button
                    onClick={() => {
                      if (filter || stateFilter !== "all") {
                        setFilter("");
                        setStateFilter("all");
                      } else setCreateOpen(true);
                    }}
                  >
                    {filter || stateFilter !== "all"
                      ? "Clear filters"
                      : "Create key"}
                  </Button>
                </SpaceBetween>
              </Box>
            }
          />
        </>
      ) : (
        <>
          <Container header={<Header variant="h2">Key overview</Header>}>
            <KeyValuePairs
              minColumnWidth={220}
              columns={3}
              items={[
                { label: "Key ID", value: copy(key.id) },
                { label: "ARN", value: copy(key.arn) },
                { label: "Status", value: keyStatus(key) },
                {
                  label: "Aliases",
                  value: key.aliases.join(", ") || "No alias",
                },
                {
                  label: "Description",
                  value: key.description || "No description",
                },
                { label: "Created at", value: kmsDate(key.created) },
              ]}
            />
          </Container>
          {key.state?.includes("Deletion") && (
            <Alert type="warning" header="Key pending deletion">
              This key cannot be used for cryptographic operations.{" "}
              {key.deletionDate
                ? `Scheduled deletion: ${kmsDate(key.deletionDate)}.`
                : ""}
            </Alert>
          )}
          <Tabs
            activeTabId={tab}
            onChange={({ detail }) => navigate(detail.activeTabId, key.id)}
            tabs={[
              {
                id: "details",
                label: "Cryptographic configuration",
                content: (
                  <Container
                    header={
                      <Header variant="h2">Cryptographic configuration</Header>
                    }
                  >
                    <KeyValuePairs
                      minColumnWidth={220}
                      columns={3}
                      items={[
                        { label: "Key spec", value: key.spec || unavailable },
                        { label: "Key usage", value: key.usage || unavailable },
                        {
                          label: "Key material origin",
                          value: key.origin || unavailable,
                        },
                        {
                          label: "Key manager",
                          value: key.manager || unavailable,
                        },
                        {
                          label: "Regionality",
                          value:
                            key.multiRegion == null
                              ? unavailable
                              : key.multiRegion
                                ? key.replica
                                  ? "Multi-Region replica"
                                  : "Multi-Region primary"
                                : "Single-Region",
                        },
                      ]}
                    />
                  </Container>
                ),
              },
              {
                id: "policy",
                label: "Key policy",
                content: (
                  <Container header={<Header variant="h2">Key policy</Header>}>
                    <SpaceBetween size="m">
                      {!key.policy && (
                        <Alert type="info">
                          The key policy is unavailable. Refresh to retry.
                        </Alert>
                      )}
                      <FormField
                        label="Key policy JSON"
                        description="The key policy controls access to this KMS key."
                      >
                        <Textarea
                          rows={14}
                          value={policyDoc}
                          disabled={
                            !managed || !key.policy || loading || !!loadError
                          }
                          onChange={({ detail }) => setPolicyDoc(detail.value)}
                        />
                      </FormField>
                      <Button
                        variant="primary"
                        loading={busy}
                        disabled={
                          !managed ||
                          !key.policy ||
                          !policyDoc.trim() ||
                          loading ||
                          !!loadError
                        }
                        onClick={() =>
                          run(async () => {
                            let policy;
                            try {
                              policy = JSON.parse(policyDoc);
                            } catch {
                              throw new Error(
                                "Enter valid JSON for the key policy.",
                              );
                            }
                            if (
                              !policy ||
                              typeof policy !== "object" ||
                              Array.isArray(policy)
                            )
                              throw new Error(
                                "The key policy must be a JSON object.",
                              );
                            await executeKmsAction("put_key_policy", {
                              key_id: key.id,
                              policy: policyDoc,
                            });
                            await loadData();
                            if (context.current === key.id)
                              setMessage({
                                type: "success",
                                text: "Key policy saved.",
                              });
                          })
                        }
                      >
                        Save policy
                      </Button>
                    </SpaceBetween>
                  </Container>
                ),
              },
              {
                id: "rotation",
                label: "Key rotation",
                content: (
                  <Container
                    header={
                      <Header variant="h2">Automatic key rotation</Header>
                    }
                  >
                    <SpaceBetween size="m">
                      <KeyValuePairs
                        minColumnWidth={220}
                        columns={2}
                        items={[
                          {
                            label: "Rotation status",
                            value:
                              key.rotation == null
                                ? unavailable
                                : key.rotation
                                  ? "Enabled"
                                  : "Disabled",
                          },
                          {
                            label: "Key material origin",
                            value: key.origin || unavailable,
                          },
                        ]}
                      />
                      <Box>
                        Automatic rotation can be managed for enabled customer
                        managed symmetric encryption keys with AWS_KMS origin.
                        Manage multi-Region rotation on the primary key.
                      </Box>
                      <Button
                        loading={busy}
                        disabled={
                          !canRotateKmsKey(key) ||
                          key.rotation == null ||
                          loading ||
                          !!loadError
                        }
                        onClick={() =>
                          mutate(
                            "set_key_rotation",
                            { enabled: !key.rotation },
                            `Automatic rotation ${key.rotation ? "disabled" : "enabled"}.`,
                          )
                        }
                      >
                        {key.rotation
                          ? "Disable automatic rotation"
                          : "Enable automatic rotation"}
                      </Button>
                    </SpaceBetween>
                  </Container>
                ),
              },
              {
                id: "grants",
                label: `Grants (${key.grants.length})`,
                content: (
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${key.grants.length})`}
                        actions={
                          <Button
                            variant="primary"
                            disabled={
                              !managed ||
                              busy ||
                              loading ||
                              !!loadError ||
                              key.state !== "Enabled"
                            }
                            onClick={() => setGrantOpen(true)}
                          >
                            Create grant
                          </Button>
                        }
                      >
                        Grants
                      </Header>
                    }
                    trackBy="GrantId"
                    items={key.grants}
                    columnDefinitions={[
                      {
                        id: "GrantId",
                        header: "Grant ID",
                        cell: (grant) => copy(grant.GrantId || grant.grant_id),
                      },
                      {
                        id: "GranteePrincipal",
                        header: "Grantee principal",
                        cell: (grant) =>
                          grant.GranteePrincipal ||
                          grant.grantee_principal ||
                          unavailable,
                      },
                      {
                        id: "Operations",
                        header: "Operations",
                        cell: (grant) =>
                          (grant.Operations || grant.operations || []).join(
                            ", ",
                          ) || unavailable,
                      },
                      {
                        id: "actions",
                        header: "Actions",
                        cell: (grant) => (
                          <Button
                            disabled={
                              !managed || busy || loading || !!loadError
                            }
                            onClick={() =>
                              setConfirmation({
                                kind: "revoke",
                                grantId: grant.GrantId || grant.grant_id,
                              })
                            }
                          >
                            Revoke grant
                          </Button>
                        ),
                      },
                    ]}
                    empty={
                      <Box textAlign="center" padding="l">
                        {key.issues.length
                          ? "Grant data may be unavailable. Check the warning above."
                          : "No grants associated with this key."}
                      </Box>
                    }
                  />
                ),
              },
              {
                id: "tags",
                label: "Tags",
                content: (
                  <Table
                    header={
                      <Header variant="h2" counter={`(${key.tags.length})`}>
                        Tags
                      </Header>
                    }
                    trackBy="TagKey"
                    items={key.tags}
                    columnDefinitions={[
                      {
                        id: "TagKey",
                        header: "Key",
                        cell: (tag) => tag.TagKey,
                      },
                      {
                        id: "TagValue",
                        header: "Value",
                        cell: (tag) => tag.TagValue,
                      },
                    ]}
                    empty={
                      <Box textAlign="center" padding="l">
                        {key.issues.length
                          ? "Tag data may be unavailable. Check the warning above."
                          : "No tags associated with this key."}
                      </Box>
                    }
                  />
                ),
              },
              {
                id: "tools",
                label: "Cryptographic tools",
                content: (
                  <KmsCryptoTools
                    key={`${key.id}:${identity?.arn}:${identity?.endpoint}`}
                    resource={key}
                    disabled={loading || !!loadError || busy}
                  />
                ),
              },
            ]}
          />
        </>
      )}
      <Modal
        visible={createOpen}
        onDismiss={() => !busy && setCreateOpen(false)}
        header="Create KMS key"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setCreateOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!aliasValid}
                onClick={createKey}
              >
                Create key
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          <FormField label="Key spec">
            <Select
              selectedOption={{ label: spec, value: spec }}
              options={[
                "SYMMETRIC_DEFAULT",
                "RSA_2048",
                "RSA_4096",
                "ECC_NIST_P256",
                "HMAC_256",
              ].map((value) => ({ label: value, value }))}
              onChange={({ detail }) => {
                const next = detail.selectedOption.value!;
                setSpec(next);
                setUsage(
                  next.startsWith("ECC")
                    ? "SIGN_VERIFY"
                    : next.startsWith("HMAC")
                      ? "GENERATE_VERIFY_MAC"
                      : "ENCRYPT_DECRYPT",
                );
              }}
            />
          </FormField>
          <FormField label="Key usage">
            <Select
              selectedOption={{ label: usage, value: usage }}
              options={allowedUsages.map((value) => ({ label: value, value }))}
              onChange={({ detail }) => setUsage(detail.selectedOption.value!)}
            />
          </FormField>
          <FormField
            label="Alias (optional)"
            description="Use a friendly name such as alias/application. The alias/aws/ prefix is reserved."
            errorText={
              aliasValid
                ? undefined
                : "Use letters, numbers, /, _, and - with an alias/ prefix; alias/aws/ is reserved."
            }
          >
            <Input
              value={alias}
              onChange={({ detail }) => setAlias(detail.value)}
              placeholder="alias/application"
            />
          </FormField>
          <FormField label="Description (optional)">
            <Input
              value={description}
              onChange={({ detail }) => setDescription(detail.value)}
            />
          </FormField>
          {createOpen && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={grantOpen && !!key}
        onDismiss={() => !busy && setGrantOpen(false)}
        header="Create grant"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setGrantOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!principal.trim() || !operations.length}
                onClick={() =>
                  run(async () => {
                    await executeKmsAction("create_grant", {
                      key_id: keyId,
                      grantee_principal: principal.trim(),
                      operations,
                    });
                    setGrantOpen(false);
                    setPrincipal("");
                    await loadData();
                    if (context.current === keyId)
                      setMessage({ type: "success", text: "Grant created." });
                  })
                }
              >
                Create grant
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Grantee principal ARN">
            <Input
              value={principal}
              onChange={({ detail }) => setPrincipal(detail.value)}
              placeholder="arn:aws:iam::123456789012:role/application"
            />
          </FormField>
          <FormField label="Operations">
            <Multiselect
              selectedOptions={operations.map((value) => ({
                label: value,
                value,
              }))}
              options={grantOperations.map((value) => ({
                label: value,
                value,
              }))}
              onChange={({ detail }) =>
                setOperations(
                  detail.selectedOptions.map((option) => option.value!),
                )
              }
              placeholder="Choose grant operations"
            />
          </FormField>
          {grantOpen && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!confirmation}
        onDismiss={() => !busy && setConfirmation(null)}
        header={
          confirmation?.kind === "disable" ? "Disable key" : "Revoke grant"
        }
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
                  confirmation?.kind === "disable"
                    ? mutate(
                        "set_key_enabled",
                        { enabled: false },
                        "Key disabled.",
                      )
                    : run(async () => {
                        await executeKmsAction("revoke_grant", {
                          key_id: keyId,
                          grant_id: confirmation!.grantId!,
                        });
                        setConfirmation(null);
                        await loadData();
                        if (context.current === keyId)
                          setMessage({
                            type: "success",
                            text: "Grant revoked.",
                          });
                      })
                }
              >
                {confirmation?.kind === "disable"
                  ? "Disable key"
                  : "Revoke grant"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            {confirmation?.kind === "disable"
              ? `Disable ${key?.aliases[0] || keyId}? Applications using this key for cryptographic operations will fail until it is enabled again.`
              : `Revoke grant ${confirmation?.grantId} on ${keyId}? The grantee will lose the permissions provided by this grant.`}
          </Alert>
          {confirmation && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
