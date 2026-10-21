import React, { useEffect, useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Autosuggest from "@cloudscape-design/components/autosuggest";
import Tabs from "@cloudscape-design/components/tabs";
import Box from "@cloudscape-design/components/box";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Alert from "@cloudscape-design/components/alert";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Checkbox from "@cloudscape-design/components/checkbox";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Modal from "@cloudscape-design/components/modal";
import {
  useAdminIdentity,
  useUserIdentity,
  assumeRoleIdentity,
  clearSessionIdentity,
} from "../api/client";
import { CodeSnippet } from "../components/CodeSnippet";
import { useSession } from "../components/SessionContext";
import { sessionExpiration } from "../sessionIdentity";

type Target = { name: string; arn: string; path?: string };
interface EnvironmentConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
}
const copy = (value?: string | null) =>
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
const sourceLabels: Record<string, string> = {
  environment: "Environment credentials",
  env: "Environment credentials",
  profile: "AWS profile",
  local_default: "Local default credentials",
  session_identity: "Session override",
};
export const EnvironmentConsole: React.FC<EnvironmentConsoleProps> = ({
  resourcePath,
  onNavigate,
}) => {
  const { identity, details, loading, error, refresh } = useSession();
  const [busy, setBusy] = useState(false);
  const [notification, setNotification] = useState<{
    type: "success" | "error" | "warning" | "info";
    text: string;
  } | null>(null);
  const [userName, setUserName] = useState("");
  const [replaceKeys, setReplaceKeys] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [roleArn, setRoleArn] = useState("");
  const [roleSession, setRoleSession] = useState("floci-session");
  const [options, setOptions] = useState<{
    users: Target[];
    roles: Target[];
    errors: Record<string, string>;
  }>({ users: [], roles: [], errors: {} });
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [optionsRevision, setOptionsRevision] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [localTab, setLocalTab] = useState("role");
  const requestedTab = resourcePath?.split("?")[0].split("/")[1] || localTab;
  const activeTab = ["role", "user", "configured"].includes(requestedTab)
    ? requestedTab
    : "role";
  const session = details?.session_identity;
  const resolved = identity?.resolved === true;
  const expiration = sessionExpiration(identity?.expires_at, now);
  const roleMatch = roleArn
    .trim()
    .match(/^arn:([a-z0-9-]+):iam::(\d{12}):role\/(.+)$/);
  const validRole =
    !!roleMatch &&
    /^[\w+=,.@-]{1,64}$/.test(roleMatch[3].split("/").pop() || "");
  const validSession = /^[\w+=,.@-]{2,64}$/.test(roleSession);
  const validUser = /^[\w+=,.@-]{1,64}$/.test(userName.trim());
  const disabled = busy || loading;
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const context = `${identity?.arn}:${identity?.account_id}:${identity?.region}:${identity?.endpoint}`;
  useEffect(() => {
    let alive = true;
    setOptionsLoading(true);
    setOptions({ users: [], roles: [], errors: {} });
    fetch("/api/session-identity/options/")
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok || result.error)
          throw new Error(result.error || `HTTP ${response.status}`);
        if (alive)
          setOptions({
            users: result.users || [],
            roles: result.roles || [],
            errors: result.errors || {},
          });
      })
      .catch((err) => {
        if (alive)
          setOptions({
            users: [],
            roles: [],
            errors: { users: err.message, roles: err.message },
          });
      })
      .finally(() => {
        if (alive) setOptionsLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [context, optionsRevision]);
  const changeIdentity = async (action: () => Promise<any>, text: string) => {
    setBusy(true);
    setNotification(null);
    try {
      const result = await action();
      await refresh(result);
      setConfirmReplace(false);
      setReplaceKeys(false);
      setNotification(
        result.identity_resolved === false
          ? {
              type: "warning",
              text: "The session changed, but its caller identity could not be verified. Refresh or return to configured credentials.",
            }
          : { type: "success", text },
      );
    } catch (err) {
      setNotification({
        type: "error",
        text:
          err instanceof Error
            ? err.message
            : "Unable to change session identity.",
      });
    } finally {
      setBusy(false);
    }
  };
  const userChange = () =>
    changeIdentity(
      () => useUserIdentity(userName.trim(), replaceKeys),
      `Session switched to IAM user ${userName.trim()}.`,
    );
  const sessionType =
    session?.type === "assumed_role"
      ? "Assumed role"
      : session?.type === "user"
        ? "IAM user"
        : details
          ? "Configured credentials"
          : "Unavailable";
  const metadata = details
    ? {
        identity_resolved: details.identity_resolved,
        caller_identity: details.caller_identity,
        endpoint_url: details.endpoint_url,
        region: details.region,
        credential_source: details.credential_source,
        session_identity: session
          ? {
              type: session.type,
              label: session.label,
              has_session_token: session.has_session_token,
              expires_at: session.expires_at,
            }
          : null,
      }
    : null;
  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Inspect the active AWS principal and switch the identity used by this dashboard session."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              iconName="refresh"
              loading={loading}
              disabled={busy}
              onClick={() => refresh()}
            >
              Refresh identity
            </Button>
            {(session || error) && (
              <Button
                loading={busy}
                disabled={loading}
                onClick={() =>
                  changeIdentity(
                    clearSessionIdentity,
                    "Returned to configured credentials.",
                  )
                }
              >
                End session override
              </Button>
            )}
          </SpaceBetween>
        }
      >
        Session identity
      </Header>
      {error && (
        <Alert type="error" header="Unable to load session identity">
          {error} Refresh to retry.
        </Alert>
      )}
      {!loading && !error && !resolved && (
        <Alert type="warning" header="Caller identity could not be verified">
          {details?.identity_error ||
            "STS did not return a verified caller identity."}{" "}
          Configured credential hints are not a verified AWS principal.
        </Alert>
      )}
      {expiration.expired && (
        <Alert type="warning" header="Session expired">
          The reported expiration time has passed. Assume the role again or
          return to configured credentials.
        </Alert>
      )}
      {expiration.soon && (
        <Alert type="warning" header="Session expires soon">
          Less than 15 minutes remain before this session expires.
        </Alert>
      )}
      {notification && !confirmReplace && (
        <Alert
          type={notification.type}
          dismissible
          onDismiss={() => setNotification(null)}
        >
          {notification.text}
        </Alert>
      )}
      <Container header={<Header variant="h2">Active identity</Header>}>
        <KeyValuePairs
          columns={3}
          minColumnWidth={220}
          items={[
            {
              label: "Identity status",
              value: (
                <StatusIndicator
                  type={
                    loading
                      ? "loading"
                      : !resolved
                        ? "error"
                        : expiration.expired
                          ? "warning"
                          : "success"
                  }
                >
                  {loading
                    ? "Resolving identity"
                    : !resolved
                      ? "Unverified"
                      : expiration.expired
                        ? "Expired"
                        : "Verified by STS"}
                </StatusIndicator>
              ),
            },
            {
              label: "Account ID",
              value: resolved
                ? copy(identity?.account_id)
                : loading
                  ? "Loading…"
                  : "Unavailable",
            },
            {
              label: "Principal ARN",
              value: resolved
                ? copy(identity?.arn)
                : loading
                  ? "Loading…"
                  : "Unavailable",
            },
            {
              label: "Principal ID",
              value: resolved ? copy(identity?.user_id) : "Unavailable",
            },
            { label: "Session type", value: sessionType },
            {
              label: "Session label",
              value:
                session?.label || details?.identity_label || "Not reported",
            },
            { label: "Region", value: details?.region || "Unavailable" },
            { label: "Endpoint", value: copy(details?.endpoint_url) },
            { label: "Session expiration", value: expiration.label },
          ]}
        />
      </Container>
      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => {
          setNotification(null);
          if (onNavigate) onNavigate(`environment/${detail.activeTabId}`);
          else setLocalTab(detail.activeTabId);
        }}
        tabs={[
          {
            id: "role",
            label: "Switch role",
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description="Assume an IAM role using the current session. The role trust policy and your permissions determine whether the switch is allowed."
                  >
                    Switch role
                  </Header>
                }
              >
                <SpaceBetween size="l">
                  {options.errors.roles && (
                    <Alert type="warning" header="Role list unavailable">
                      {options.errors.roles} Enter the full role ARN manually or
                      retry the list.
                    </Alert>
                  )}
                  <FormField
                    label="Role ARN"
                    description="Choose a role or enter its full ARN, including any role path."
                    errorText={
                      roleArn && !validRole
                        ? "Enter a valid IAM role ARN with a 12-digit account ID."
                        : undefined
                    }
                  >
                    <Autosuggest
                      value={roleArn}
                      onChange={({ detail }) => setRoleArn(detail.value)}
                      options={options.roles
                        .filter((role) => role.arn)
                        .map((role) => ({
                          value: role.arn,
                          label: role.name,
                          description: role.arn,
                        }))}
                      enteredTextLabel={(value) => `Use role ARN: ${value}`}
                      placeholder="arn:aws:iam::123456789012:role/team/application"
                      empty={
                        optionsLoading
                          ? "Loading roles…"
                          : "No listed roles. Enter an ARN manually."
                      }
                    />
                  </FormField>
                  {validRole && (
                    <KeyValuePairs
                      columns={2}
                      minColumnWidth={220}
                      items={[
                        { label: "Target account", value: roleMatch![2] },
                        { label: "Role path and name", value: roleMatch![3] },
                      ]}
                    />
                  )}
                  <FormField
                    label="Role session name"
                    description="Use 2–64 letters, numbers, or +=,.@- characters."
                    errorText={
                      roleSession && !validSession
                        ? "Enter a valid role session name."
                        : undefined
                    }
                  >
                    <Input
                      value={roleSession}
                      onChange={({ detail }) => setRoleSession(detail.value)}
                    />
                  </FormField>
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button
                      variant="primary"
                      loading={busy}
                      disabled={loading || !validRole || !validSession}
                      onClick={() =>
                        changeIdentity(
                          () =>
                            assumeRoleIdentity(
                              roleMatch![3].split("/").pop()!,
                              roleSession,
                              roleMatch![2],
                              roleArn.trim(),
                            ),
                          "Role session assumed. The dashboard now uses the new principal.",
                        )
                      }
                    >
                      Switch role
                    </Button>
                    <Button
                      loading={optionsLoading}
                      disabled={busy}
                      onClick={() => setOptionsRevision((value) => value + 1)}
                    >
                      Reload identity lists
                    </Button>
                  </SpaceBetween>
                </SpaceBetween>
              </Container>
            ),
          },
          {
            id: "user",
            label: "IAM user",
            content: (
              <Container header={<Header variant="h2">Switch IAM user</Header>}>
                <SpaceBetween size="l">
                  <Alert type="info">
                    This local workflow creates an access key for the selected
                    user and attaches the dashboard policy that allows
                    sts:GetCallerIdentity. Service access remains subject to
                    that user's IAM policies.
                  </Alert>
                  {options.errors.users && (
                    <Alert type="warning" header="User list unavailable">
                      {options.errors.users} Enter an IAM user name manually or
                      retry the list.
                    </Alert>
                  )}
                  <FormField
                    label="IAM user name"
                    errorText={
                      userName && !validUser
                        ? "Use a valid IAM user name, up to 64 characters."
                        : undefined
                    }
                  >
                    <Autosuggest
                      value={userName}
                      onChange={({ detail }) => setUserName(detail.value)}
                      options={options.users.map((user) => ({
                        value: user.name,
                        label: user.name,
                        description: user.arn,
                      }))}
                      enteredTextLabel={(value) => `Use IAM user: ${value}`}
                      empty={
                        optionsLoading
                          ? "Loading users…"
                          : "No listed users. Enter a user name manually."
                      }
                      placeholder="Choose or enter a user name"
                    />
                  </FormField>
                  <Checkbox
                    checked={replaceKeys}
                    disabled={disabled}
                    onChange={({ detail }) => setReplaceKeys(detail.checked)}
                  >
                    Replace all existing access keys for this user
                  </Checkbox>
                  <SpaceBetween direction="horizontal" size="xs">
                    <Button
                      variant="primary"
                      loading={busy}
                      disabled={loading || !validUser}
                      onClick={() =>
                        replaceKeys ? setConfirmReplace(true) : userChange()
                      }
                    >
                      Switch user
                    </Button>
                    <Button
                      loading={optionsLoading}
                      disabled={busy}
                      onClick={() => setOptionsRevision((value) => value + 1)}
                    >
                      Reload identity lists
                    </Button>
                  </SpaceBetween>
                </SpaceBetween>
              </Container>
            ),
          },
          {
            id: "configured",
            label: "Configured credentials",
            content: (
              <Container
                header={<Header variant="h2">Configured credentials</Header>}
              >
                <SpaceBetween size="l">
                  <Box>
                    Remove the browser session override and use the dashboard's
                    configured AWS profile, environment credentials, or local
                    defaults. The resulting principal and permissions are
                    resolved after switching.
                  </Box>
                  <KeyValuePairs
                    columns={2}
                    minColumnWidth={220}
                    items={[
                      {
                        label: "Current credential source",
                        value: details?.credential_source
                          ? sourceLabels[details.credential_source] ||
                            details.credential_source
                          : "Unavailable",
                      },
                      {
                        label: "AWS profile",
                        value: details?.profile || "Not reported",
                      },
                    ]}
                  />
                  <Button
                    variant="primary"
                    loading={busy}
                    disabled={loading}
                    onClick={() =>
                      changeIdentity(
                        useAdminIdentity,
                        "Returned to configured credentials.",
                      )
                    }
                  >
                    Use configured credentials
                  </Button>
                </SpaceBetween>
              </Container>
            ),
          },
        ]}
      />
      <ExpandableSection headerText="Session metadata">
        <SpaceBetween size="m">
          <Box>
            Caller and session metadata only; credential secrets and session
            tokens are not displayed.
          </Box>
          {metadata ? (
            <CodeSnippet
              language="json"
              code={JSON.stringify(metadata, null, 2)}
            />
          ) : (
            <Box>Session metadata is unavailable.</Box>
          )}
        </SpaceBetween>
      </ExpandableSection>
      <Modal
        visible={confirmReplace}
        onDismiss={() => !busy && setConfirmReplace(false)}
        header="Replace user access keys"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setConfirmReplace(false)}
              >
                Cancel
              </Button>
              <Button variant="primary" loading={busy} onClick={userChange}>
                Replace keys and switch user
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            All existing access keys for {userName.trim()} will be deleted and a
            new key will be created. Applications using the old keys will lose
            access.
          </Alert>
          {confirmReplace && notification?.type === "error" && (
            <Alert type="error">{notification.text}</Alert>
          )}
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
