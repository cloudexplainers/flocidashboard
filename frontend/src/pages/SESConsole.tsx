import React, { useEffect, useMemo, useRef, useState } from "react";
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
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import { SesEmailComposer } from "../components/SesEmailComposer";
import { SesLocalMailbox } from "../components/SesLocalMailbox";
import { useSession } from "../components/SessionContext";
import {
  SesIdentity,
  sesIdentities,
  sesStatus,
  sesDate,
  sesRequest,
  validSesEmail,
} from "./sesResources";
import {
  verifySesEmailIdentity,
  verifySesDomainIdentity,
  deleteSesIdentity,
  createSesTemplate,
  renderSesTemplate,
  deleteSesTemplate,
  createSesConfigurationSet,
  updateSesSendingEnabled,
} from "../api/client";

interface SESConsoleProps {
  activeTab?: string;
  onTabChange?: (tab: string) => void;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
}
const copy = (value?: string) =>
  value ? (
    <CopyToClipboard
      textToCopy={value}
      variant="inline"
      copyButtonAriaLabel={`Copy ${value}`}
      copySuccessText="Copied"
      copyErrorText="Unable to copy"
    />
  ) : (
    "Unavailable"
  );
const identityStatus = (identity: SesIdentity) => (
  <StatusIndicator
    type={
      identity.verified
        ? "success"
        : /fail/i.test(identity.status || "")
          ? "error"
          : /pending|not.?started/i.test(identity.status || "")
            ? "pending"
            : "info"
    }
  >
    {sesStatus(identity)}
  </StatusIndicator>
);
const enabledLabel = (value: unknown) =>
  value === true ? "Enabled" : value === false ? "Paused" : "Unavailable";
export const SESConsole: React.FC<SESConsoleProps> = ({
  activeTab,
  onTabChange,
  resourcePath,
  onNavigate,
}) => {
  const { identity: session } = useSession();
  const query = new URLSearchParams(
    (resourcePath || window.location.search).split("?").pop(),
  );
  const identityName = query.get("identity") || "";
  const tab = (activeTab || "overview").split("?")[0];
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [message, setMessage] = useState<{
    type: "success" | "error" | "info";
    text: string;
  } | null>(null);
  const [form, setForm] = useState<
    "identity" | "template" | "configset" | "render" | null
  >(null);
  const [formName, setFormName] = useState("");
  const [identityType, setIdentityType] = useState("Email address");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [html, setHtml] = useState("");
  const [templateData, setTemplateData] = useState("{}");
  const [rendered, setRendered] = useState<string | null>(null);
  const [token, setToken] = useState<{ name: string; value: string } | null>(
    null,
  );
  const [confirmation, setConfirmation] = useState<{
    type: "identity" | "template" | "sending";
    name: string;
  } | null>(null);
  const requestId = useRef(0);
  const identities = useMemo(() => sesIdentities(data), [data]);
  const identity = identities.find((item) => item.name === identityName);
  const templates: any[] = Array.isArray(data.templates) ? data.templates : [];
  const sets: any[] = Array.isArray(data.v2_configuration_sets)
    ? data.v2_configuration_sets
    : [];
  const sendingEnabled: boolean | undefined =
    typeof data.account_sending_enabled?.Enabled === "boolean"
      ? data.account_sending_enabled.Enabled
      : typeof data.account?.SendingEnabled === "boolean"
        ? data.account.SendingEnabled
        : undefined;
  const productionAccess = data.account?.ProductionAccessEnabled;
  const quota = data.send_quota || {};
  const contextKey = `${session?.account_id}:${session?.arn}:${session?.region}:${session?.endpoint}`;
  const disabled = loading || !!loadError || busy;
  const navigate = (next: string, name = "", source = "") => {
    const qs = new URLSearchParams();
    if (name) qs.set("identity", name);
    if (source) qs.set("source", source);
    const target = `${next}${qs.size ? `?${qs}` : ""}`;
    if (onNavigate) onNavigate(`ses/${target}`);
    else onTabChange?.(target);
  };
  const loadData = async () => {
    const version = ++requestId.current;
    setLoading(true);
    setLoadError("");
    try {
      const result = await sesRequest("/api/ses/");
      if (version === requestId.current) setData(result);
    } catch (err) {
      if (version === requestId.current)
        setLoadError(
          err instanceof Error ? err.message : "Unable to load SES resources.",
        );
    } finally {
      if (version === requestId.current) setLoading(false);
    }
  };
  useEffect(() => {
    setData({});
    setToken(null);
    setForm(null);
    void loadData();
    return () => {
      requestId.current++;
    };
  }, [contextKey]);
  useEffect(() => {
    setMessage(null);
    setConfirmation(null);
  }, [identityName, tab]);
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
  const openForm = (
    next: "identity" | "template" | "configset" | "render",
    name = "",
  ) => {
    setForm(next);
    setFormName(name);
    setMessage(null);
    setRendered(null);
    setSubject("");
    setText("");
    setHtml("");
    setTemplateData("{}");
  };
  const validIdentity =
    identityType === "Email address"
      ? validSesEmail(formName.trim())
      : /^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$/.test(
          formName.trim(),
        );
  const saveForm = () =>
    run(async () => {
      if (form === "identity") {
        const name = formName.trim();
        const result =
          identityType === "Email address"
            ? await verifySesEmailIdentity(name)
            : await verifySesDomainIdentity(name);
        if (result.verification_token)
          setToken({ name, value: result.verification_token });
        setForm(null);
        await loadData();
        navigate("details", name);
        // Identity state comes from inventory, never from a successful verification request.
        setMessage({
          type: "info",
          text:
            identityType === "Email address"
              ? `Verification requested for ${name}. Floci marks identities verified immediately; in AWS the status stays Pending until someone opens the link sent to the address.`
              : `Verification requested for ${name}. Floci marks identities verified immediately; in AWS the status stays Pending until the TXT (or DKIM CNAME) records are published in DNS.`,
        });
      } else if (form === "template") {
        await createSesTemplate(formName.trim(), subject, html, text);
        setForm(null);
        await loadData();
        setMessage({ type: "success", text: "Template created." });
      } else if (form === "configset") {
        await createSesConfigurationSet(formName.trim());
        setForm(null);
        await loadData();
        setMessage({ type: "success", text: "Configuration set created." });
      } else {
        let parsed;
        try {
          parsed = JSON.parse(templateData);
        } catch {
          throw new Error("Enter valid JSON for the template data.");
        }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
          throw new Error("Template data must be a JSON object.");
        setRendered(null);
        const result = await renderSesTemplate(formName, templateData);
        if (typeof result.rendered_template !== "string")
          throw new Error("No rendered template was returned.");
        setRendered(result.rendered_template);
      }
    });
  const confirmAction = () =>
    run(async () => {
      if (!confirmation) return;
      if (confirmation.type === "identity") {
        await deleteSesIdentity(confirmation.name);
        setConfirmation(null);
        await loadData();
        navigate("identities");
      } else if (confirmation.type === "template") {
        await deleteSesTemplate(confirmation.name);
        setConfirmation(null);
        await loadData();
        setMessage({ type: "success", text: "Template deleted." });
      } else {
        await updateSesSendingEnabled(sendingEnabled !== true);
        setConfirmation(null);
        await loadData();
        setMessage({
          type: "success",
          text: "Account sending settings updated.",
        });
      }
    });
  const verificationToken =
    identity?.token || (token?.name === identityName ? token.value : undefined);
  const visibleIdentities = identities.filter(
    (item) =>
      item.name.toLowerCase().includes(filter.toLowerCase()) &&
      (statusFilter === "all" ||
        (statusFilter === "verified"
          ? item.verified === true
          : item.verified !== true)),
  );
  const detailTab = ["authentication", "notifications"].includes(tab)
    ? tab
    : "details";
  const overviewErrors = [data.account, data.account_sending_enabled, quota]
    .filter((value) => value?.error)
    .map((value) => String(value.error));
  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={
          identity
            ? "Inspect identity verification and authentication in the current Region."
            : "Manage sending identities, templates, and local email testing."
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
            {identity ? (
              <Button
                disabled={disabled}
                onClick={() =>
                  setConfirmation({ type: "identity", name: identity.name })
                }
              >
                Delete identity
              </Button>
            ) : (
              <Button variant="primary" onClick={() => openForm("identity")}>
                Create identity
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {identity?.name || "Amazon Simple Email Service"}
      </Header>
      {loadError && (
        <Alert type="error" header="Unable to load SES resources">
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
      {identityName && !identity ? (
        <Container>
          <Box textAlign="center" padding="l">
            <SpaceBetween size="m">
              <Box>
                {loading
                  ? "Loading identity…"
                  : `Identity "${identityName}" was not found in the current account and Region.`}
              </Box>
              <Button onClick={() => navigate("identities")}>
                View identities
              </Button>
            </SpaceBetween>
          </Box>
        </Container>
      ) : identity ? (
        <>
          <Container header={<Header variant="h2">Identity overview</Header>}>
            <KeyValuePairs
              columns={3}
              minColumnWidth={220}
              items={[
                { label: "Identity", value: copy(identity.name) },
                { label: "Identity type", value: identity.type },
                {
                  label: "Verification status",
                  value: identityStatus(identity),
                },
                { label: "Region", value: session?.region || "Unavailable" },
                {
                  label: "Account sending",
                  value: enabledLabel(sendingEnabled),
                },
                {
                  label: "DKIM status",
                  value: identity.dkim?.DkimVerificationStatus || "Unavailable",
                },
              ]}
            />
          </Container>
          {identity.verified !== true && (
            <Alert type="info" header="Verification is not confirmed">
              A verification request does not establish that this identity is
              verified. Refresh to retrieve the current status reported by
              Floci.
            </Alert>
          )}
          <Tabs
            activeTabId={detailTab}
            onChange={({ detail }) =>
              navigate(detail.activeTabId, identity.name)
            }
            tabs={[
              {
                id: "details",
                label: "Verification",
                content: (
                  <Container
                    header={<Header variant="h2">Verification details</Header>}
                  >
                    <SpaceBetween size="m">
                      {identity.type === "Domain" ? (
                        <>
                          <Box>
                            This workflow uses the SES domain verification TXT
                            record returned by the verification API.
                          </Box>
                          <KeyValuePairs
                            columns={2}
                            minColumnWidth={220}
                            items={[
                              { label: "Record type", value: "TXT" },
                              {
                                label: "Record name",
                                value: copy(`_amazonses.${identity.name}`),
                              },
                              {
                                label: "Record value",
                                value: copy(verificationToken),
                              },
                            ]}
                          />
                          {!verificationToken && (
                            <Box>
                              The verification token was not reported. Refresh
                              or initiate verification again to retrieve it.
                            </Box>
                          )}
                        </>
                      ) : (
                        <Box>
                          Email identity verification is determined by the
                          status reported by the emulator. AWS SES verifies
                          email ownership through a verification email.
                        </Box>
                      )}
                      <Button
                        disabled={disabled}
                        onClick={() => {
                          setIdentityType(identity.type);
                          openForm("identity", identity.name);
                        }}
                      >
                        Request verification again
                      </Button>
                      {identity.type === "Email address" && (
                        <Button
                          variant="primary"
                          disabled={
                            disabled ||
                            identity.verified !== true ||
                            sendingEnabled !== true
                          }
                          onClick={() => navigate("send", "", identity.name)}
                        >
                          Send test email
                        </Button>
                      )}
                    </SpaceBetween>
                  </Container>
                ),
              },
              {
                id: "authentication",
                label: "DKIM authentication",
                content: (
                  <Container
                    header={<Header variant="h2">DKIM authentication</Header>}
                  >
                    <SpaceBetween size="m">
                      <KeyValuePairs
                        columns={2}
                        minColumnWidth={220}
                        items={[
                          {
                            label: "DKIM signing",
                            value:
                              identity.dkim?.DkimEnabled === true
                                ? "Enabled"
                                : identity.dkim?.DkimEnabled === false
                                  ? "Disabled"
                                  : "Unavailable",
                          },
                          {
                            label: "Verification status",
                            value:
                              identity.dkim?.DkimVerificationStatus ||
                              "Unavailable",
                          },
                        ]}
                      />
                      {identity.dkim?.error && (
                        <Alert type="warning">{identity.dkim.error}</Alert>
                      )}
                      <Box>Reported DKIM tokens</Box>
                      {identity.dkim?.DkimTokens?.length ? (
                        identity.dkim.DkimTokens.map((value) => (
                          <Box key={value}>{copy(value)}</Box>
                        ))
                      ) : (
                        <Box>No DKIM tokens reported.</Box>
                      )}
                    </SpaceBetween>
                  </Container>
                ),
              },
              {
                id: "notifications",
                label: "Notifications",
                content: (
                  <Container
                    header={
                      <Header variant="h2">Feedback notifications</Header>
                    }
                  >
                    <KeyValuePairs
                      columns={2}
                      minColumnWidth={220}
                      items={[
                        {
                          label: "Bounce topic ARN",
                          value: copy(identity.notifications?.BounceTopic),
                        },
                        {
                          label: "Complaint topic ARN",
                          value: copy(identity.notifications?.ComplaintTopic),
                        },
                        {
                          label: "Delivery topic ARN",
                          value: copy(identity.notifications?.DeliveryTopic),
                        },
                        {
                          label: "Feedback forwarding",
                          value:
                            typeof identity.notifications?.ForwardingEnabled ===
                            "boolean"
                              ? identity.notifications.ForwardingEnabled
                                ? "Enabled"
                                : "Disabled"
                              : "Unavailable",
                        },
                      ]}
                    />
                    {identity.notifications?.error && (
                      <Alert type="warning">
                        {identity.notifications.error}
                      </Alert>
                    )}
                  </Container>
                ),
              },
            ]}
          />
        </>
      ) : tab === "overview" ? (
        <SpaceBetween size="l">
          <Alert type="info" header="Local SES environment">
            Verification and sending statuses reflect Floci. Use the local test
            mailbox to inspect captured messages.
          </Alert>
          {overviewErrors.length > 0 && (
            <Alert type="warning" header="Some sending details are unavailable">
              {overviewErrors.join(" ")}
            </Alert>
          )}
          <Container
            header={
              <Header
                variant="h2"
                actions={
                  <Button
                    disabled={disabled || sendingEnabled == null}
                    onClick={() =>
                      setConfirmation({ type: "sending", name: "" })
                    }
                  >
                    {sendingEnabled === true
                      ? "Pause account sending"
                      : "Enable account sending"}
                  </Button>
                }
              >
                Sending readiness
              </Header>
            }
          >
            <KeyValuePairs
              columns={3}
              minColumnWidth={220}
              items={[
                {
                  label: "Account sending",
                  value: loading ? "Loading…" : enabledLabel(sendingEnabled),
                },
                {
                  label: "Access",
                  value:
                    productionAccess === true
                      ? "Production"
                      : productionAccess === false
                        ? "Sandbox"
                        : "Unavailable",
                },
                { label: "Region", value: session?.region || "Unavailable" },
                {
                  label: "Verified identities",
                  value: loading
                    ? "Loading…"
                    : identities.filter((item) => item.verified === true)
                        .length,
                },
                {
                  label: "Unconfirmed identities",
                  value: loading
                    ? "Loading…"
                    : identities.filter((item) => item.verified !== true)
                        .length,
                },
                {
                  label: "Maximum send rate",
                  value:
                    quota.MaxSendRate == null
                      ? "Unavailable"
                      : `${quota.MaxSendRate} messages/second`,
                },
                {
                  label: "24-hour sending quota",
                  value: quota.Max24HourSend ?? "Unavailable",
                },
                {
                  label: "Sent in last 24 hours",
                  value: quota.SentLast24Hours ?? "Unavailable",
                },
              ]}
            />
          </Container>
          <Container header={<Header variant="h2">Get started</Header>}>
            <SpaceBetween direction="horizontal" size="s">
              <Button onClick={() => navigate("identities")}>
                Manage identities
              </Button>
              <Button onClick={() => navigate("send")}>
                Compose test email
              </Button>
              <Button onClick={() => navigate("mailbox")}>
                Open local test mailbox
              </Button>
            </SpaceBetween>
          </Container>
        </SpaceBetween>
      ) : tab === "identities" || tab === "details" ? (
        <Table
          loading={loading}
          loadingText="Loading identities"
          trackBy="name"
          header={
            <Header
              variant="h2"
              counter={`(${identities.length})`}
              description="Includes identities awaiting verification."
            >
              Identities
            </Header>
          }
          filter={
            <SpaceBetween direction="horizontal" size="s">
              <TextFilter
                filteringText={filter}
                filteringAriaLabel="Find identities"
                filteringPlaceholder="Find email addresses or domains"
                onChange={({ detail }) => setFilter(detail.filteringText)}
              />
              <Select
                ariaLabel="Verification filter"
                selectedOption={{
                  value: statusFilter,
                  label:
                    statusFilter === "all"
                      ? "All statuses"
                      : statusFilter === "verified"
                        ? "Verified"
                        : "Unconfirmed",
                }}
                options={[
                  { label: "All statuses", value: "all" },
                  { label: "Verified", value: "verified" },
                  { label: "Unconfirmed", value: "unconfirmed" },
                ]}
                onChange={({ detail }) =>
                  setStatusFilter(detail.selectedOption.value!)
                }
              />
            </SpaceBetween>
          }
          items={visibleIdentities}
          columnDefinitions={[
            {
              id: "name",
              header: "Identity",
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
            { id: "type", header: "Identity type", cell: (item) => item.type },
            {
              id: "status",
              header: "Verification status",
              cell: identityStatus,
            },
            {
              id: "dkim",
              header: "DKIM status",
              cell: (item) =>
                item.dkim?.DkimVerificationStatus || "Unavailable",
            },
          ]}
          empty={
            <Box textAlign="center" padding="l">
              {filter || statusFilter !== "all"
                ? "No matching identities."
                : "No identities. Create an email or domain identity to get started."}
            </Box>
          }
        />
      ) : tab === "send" ? (
        <SesEmailComposer
          key={`${contextKey}:${query.get("source") || ""}`}
          identities={identities}
          sendingEnabled={sendingEnabled}
          sandbox={
            typeof productionAccess === "boolean"
              ? !productionAccess
              : undefined
          }
          configurationSets={sets}
          initialSource={query.get("source") || ""}
          disabled={disabled}
          onSent={() => {
            void loadData();
          }}
        />
      ) : tab === "templates" ? (
        <Table
          loading={loading}
          loadingText="Loading templates"
          trackBy={(item) => item.name}
          header={
            <Header
              variant="h2"
              counter={`(${templates.length})`}
              actions={
                <Button variant="primary" onClick={() => openForm("template")}>
                  Create template
                </Button>
              }
            >
              Email templates
            </Header>
          }
          items={templates}
          columnDefinitions={[
            {
              id: "name",
              header: "Name",
              cell: (item) => item.name || "Unavailable",
            },
            {
              id: "subject",
              header: "Subject",
              cell: (item) => item.details?.SubjectPart ?? "Unavailable",
            },
            {
              id: "created",
              header: "Created at",
              cell: (item) => sesDate(item.created),
            },
            {
              id: "actions",
              header: "Actions",
              cell: (item) => (
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    disabled={disabled}
                    onClick={() => openForm("render", item.name)}
                  >
                    Test render
                  </Button>
                  <Button
                    disabled={disabled}
                    onClick={() =>
                      setConfirmation({ type: "template", name: item.name })
                    }
                  >
                    Delete template
                  </Button>
                </SpaceBetween>
              ),
            },
          ]}
          empty={
            <Box textAlign="center" padding="l">
              No email templates returned.
            </Box>
          }
        />
      ) : tab === "configsets" ? (
        <SpaceBetween size="l">
          {data.v2_configuration_sets?.error && (
            <Alert type="warning">{data.v2_configuration_sets.error}</Alert>
          )}
          <Table
            loading={loading}
            loadingText="Loading configuration sets"
            trackBy="name"
            header={
              <Header
                variant="h2"
                counter={`(${sets.length})`}
                actions={
                  <Button
                    variant="primary"
                    onClick={() => openForm("configset")}
                  >
                    Create configuration set
                  </Button>
                }
              >
                Configuration sets
              </Header>
            }
            items={sets}
            columnDefinitions={[
              { id: "name", header: "Name", cell: (item) => item.name },
              {
                id: "sending_enabled",
                header: "Sending status",
                cell: (item) => enabledLabel(item.sending_enabled),
              },
              {
                id: "destinations",
                header: "Event destinations",
                cell: (item) =>
                  Array.isArray(item.event_destinations)
                    ? item.event_destinations.length
                    : "Unavailable",
              },
              {
                id: "tls",
                header: "TLS policy",
                cell: (item) =>
                  item.delivery_options?.TlsPolicy || "Unavailable",
              },
            ]}
            empty={
              <Box textAlign="center" padding="l">
                No configuration sets returned.
              </Box>
            }
          />
        </SpaceBetween>
      ) : tab === "mailbox" ? (
        <SesLocalMailbox key={contextKey} />
      ) : (
        <Alert type="info">
          Choose an SES page from the service navigation.
        </Alert>
      )}
      <Modal
        visible={!!form}
        onDismiss={() => !busy && setForm(null)}
        size={form === "template" || form === "render" ? "large" : "medium"}
        header={
          form === "identity"
            ? "Create or verify identity"
            : form === "template"
              ? "Create email template"
              : form === "render"
                ? `Test render: ${formName}`
                : "Create configuration set"
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setForm(null)}
              >
                Close
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={
                  form === "identity"
                    ? !validIdentity
                    : form === "template"
                      ? !formName.trim() || !subject.trim() || !(text || html)
                      : !formName.trim()
                }
                onClick={saveForm}
              >
                {form === "identity"
                  ? "Request verification"
                  : form === "render"
                    ? "Render template"
                    : "Create"}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          {form === "identity" && (
            <FormField label="Identity type">
              <Select
                selectedOption={{ label: identityType, value: identityType }}
                options={["Email address", "Domain"].map((value) => ({
                  label: value,
                  value,
                }))}
                onChange={({ detail }) =>
                  setIdentityType(detail.selectedOption.value!)
                }
              />
            </FormField>
          )}
          {form !== "render" && (
            <FormField
              label={form === "identity" ? identityType : "Name"}
              errorText={
                form === "identity" && formName && !validIdentity
                  ? `Enter a valid ${identityType.toLowerCase()}.`
                  : undefined
              }
            >
              <Input
                value={formName}
                onChange={({ detail }) => setFormName(detail.value)}
              />
            </FormField>
          )}
          {form === "identity" && (
            <Box>
              Verification requests are followed by a refresh of the reported
              identity status. AWS SES requires email ownership verification or
              domain DNS records.
            </Box>
          )}
          {form === "template" && (
            <>
              <FormField label="Subject">
                <Input
                  value={subject}
                  onChange={({ detail }) => setSubject(detail.value)}
                />
              </FormField>
              <FormField label="Text body">
                <Textarea
                  value={text}
                  rows={4}
                  onChange={({ detail }) => setText(detail.value)}
                />
              </FormField>
              <FormField label="HTML body">
                <Textarea
                  value={html}
                  rows={5}
                  onChange={({ detail }) => setHtml(detail.value)}
                />
              </FormField>
            </>
          )}
          {form === "render" && (
            <>
              <FormField label="Template data (JSON)">
                <Textarea
                  value={templateData}
                  rows={5}
                  onChange={({ detail }) => {
                    setTemplateData(detail.value);
                    setRendered(null);
                  }}
                />
              </FormField>
              {rendered !== null && (
                <FormField label="Rendered message source">
                  <Textarea rows={10} value={rendered} readOnly />
                </FormField>
              )}
            </>
          )}
          {form && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!confirmation}
        onDismiss={() => !busy && setConfirmation(null)}
        header={
          confirmation?.type === "sending"
            ? "Change account sending"
            : `Delete ${confirmation?.type || "resource"}`
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
              <Button variant="primary" loading={busy} onClick={confirmAction}>
                Confirm
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            {confirmation?.type === "sending"
              ? `${sendingEnabled ? "Pause" : "Enable"} sending for this SES account in the current Region? This affects every application using it.`
              : `Delete ${confirmation?.type} "${confirmation?.name}"? Applications using it may fail.`}
          </Alert>
          {confirmation && message?.type === "error" && (
            <Alert type="error">{message.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
