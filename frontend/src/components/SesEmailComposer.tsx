import { useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import { sendSesEmail } from "../api/client";
import {
  SesIdentity,
  validSesEmail,
  verifiedSesSender,
} from "../pages/sesResources";

export function SesEmailComposer({
  identities,
  sendingEnabled,
  sandbox,
  configurationSets,
  initialSource,
  disabled,
  onSent,
}: {
  identities: SesIdentity[];
  sendingEnabled?: boolean;
  sandbox?: boolean;
  configurationSets: any[];
  initialSource: string;
  disabled: boolean;
  onSent: () => void;
}) {
  const [source, setSource] = useState(initialSource);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [html, setHtml] = useState("");
  const [configurationSet, setConfigurationSet] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [messageId, setMessageId] = useState<string | null>(null);
  const recipients = to
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const senderVerified = verifiedSesSender(source.trim(), identities);
  const validRecipients =
    recipients.length > 0 && recipients.every(validSesEmail);
  const selectedSet = configurationSets.find(
    (item) => item.name === configurationSet,
  );
  const allowed =
    !disabled &&
    sendingEnabled === true &&
    senderVerified &&
    validRecipients &&
    !!subject.trim() &&
    !!(text || html) &&
    selectedSet?.sending_enabled !== false;
  const send = async () => {
    setBusy(true);
    setError("");
    setMessageId(null);
    try {
      const result = await sendSesEmail({
        source: source.trim(),
        to_addresses: recipients,
        subject,
        ...(text ? { text } : {}),
        ...(html ? { html } : {}),
        ...(configurationSet
          ? { configuration_set_name: configurationSet }
          : {}),
      });
      if (!result.message_id)
        throw new Error(
          "No message ID was returned. Check the local mailbox before retrying.",
        );
      setMessageId(result.message_id);
      onSent();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to send test email.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <SpaceBetween size="l">
      <Alert type="info" header="Local test email">
        Floci captures these messages in its local mailbox. An accepted message
        ID does not establish delivery to an external inbox.
      </Alert>
      {sendingEnabled !== true && (
        <Alert type="warning">
          {sendingEnabled === false
            ? "Account sending is paused."
            : "Account sending status is unavailable. Refresh before sending."}
        </Alert>
      )}
      {sandbox && (
        <Alert type="info">
          This account reports sandbox access. AWS SES sandbox rules require
          verified recipients or mailbox simulator addresses; emulator behavior
          may differ.
        </Alert>
      )}
      {error && <Alert type="error">{error}</Alert>}
      {messageId && (
        <Alert type="success" header="Message accepted">
          Message ID: {messageId}. Inspect the local test mailbox for the
          captured message.
        </Alert>
      )}
      <Container
        header={
          <Header
            variant="h2"
            actions={
              <Button
                variant="primary"
                loading={busy}
                disabled={!allowed}
                onClick={send}
              >
                Send test email
              </Button>
            }
          >
            Compose test email
          </Header>
        }
      >
        <SpaceBetween size="l">
          <FormField
            label="From address"
            description="Use a verified email identity or an address under a verified domain."
            errorText={
              source && !senderVerified
                ? "This address does not match a verified identity in the current Region."
                : undefined
            }
          >
            <Input
              value={source}
              onChange={({ detail }) => {
                setSource(detail.value);
                setMessageId(null);
              }}
              placeholder="sender@example.com"
            />
          </FormField>
          {identities.some(
            (item) => item.verified && item.type === "Email address",
          ) && (
            <SpaceBetween direction="horizontal" size="xs">
              {identities
                .filter(
                  (item) => item.verified && item.type === "Email address",
                )
                .map((item) => (
                  <Button key={item.name} onClick={() => setSource(item.name)}>
                    Use {item.name}
                  </Button>
                ))}
            </SpaceBetween>
          )}
          <FormField
            label="To addresses"
            description="Separate recipient email addresses with commas."
            errorText={
              to && !validRecipients
                ? "Enter valid email addresses separated by commas."
                : undefined
            }
          >
            <Input
              value={to}
              onChange={({ detail }) => {
                setTo(detail.value);
                setMessageId(null);
              }}
            />
          </FormField>
          <FormField label="Subject">
            <Input
              value={subject}
              onChange={({ detail }) => {
                setSubject(detail.value);
                setMessageId(null);
              }}
            />
          </FormField>
          <FormField label="Configuration set (optional)">
            <Select
              selectedOption={{
                label: configurationSet || "None",
                value: configurationSet,
              }}
              options={[
                { label: "None", value: "" },
                ...configurationSets.map((item) => ({
                  label: item.name,
                  value: item.name,
                })),
              ]}
              onChange={({ detail }) =>
                setConfigurationSet(detail.selectedOption.value || "")
              }
            />
          </FormField>
          {selectedSet?.sending_enabled === false && (
            <Alert type="warning">
              Sending is paused for this configuration set.
            </Alert>
          )}
          <FormField label="Text body">
            <Textarea
              rows={5}
              value={text}
              onChange={({ detail }) => {
                setText(detail.value);
                setMessageId(null);
              }}
            />
          </FormField>
          <FormField label="HTML body (optional)">
            <Textarea
              rows={5}
              value={html}
              onChange={({ detail }) => {
                setHtml(detail.value);
                setMessageId(null);
              }}
            />
          </FormField>
          <Box>
            Provide at least one body format. Content is sent exactly as
            entered.
          </Box>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}
