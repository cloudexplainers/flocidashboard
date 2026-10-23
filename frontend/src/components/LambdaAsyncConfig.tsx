import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface EventInvokeConfig {
  maximum_retry_attempts?: number | null;
  maximum_event_age_in_seconds?: number | null;
  on_success?: string;
  on_failure?: string;
  last_modified?: string;
}

const destinationLabel = (arn = "") =>
  !arn ? "None"
    : arn.includes(":sqs:") ? `SQS queue ${arn.split(":").pop()}`
      : arn.includes(":sns:") ? `SNS topic ${arn.split(":").pop()}`
        : arn.includes(":lambda:") ? `Lambda function ${arn.split(":").pop()}`
          : arn.includes(":events:") ? `EventBridge bus ${arn.split("/").pop()}` : arn;

/** Asynchronous invocation: retries, maximum event age, and on-success / on-failure destinations. */
export function LambdaAsyncConfig({ functionName }: { functionName: string }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [config, setConfig] = useState<EventInvokeConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<{ retries: string; age: string; success: string; failure: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const path = `functions/${encodeURIComponent(functionName)}/event-invoke-config/`;

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("lambda", path);
      if (!alive.current) return;
      setConfig(data.config || null);
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load asynchronous invocation settings");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [functionName]);

  const save = async () => {
    if (!form) return;
    setBusy(true);
    setError("");
    try {
      const data = await serviceRequest("lambda", path, "PUT", {
        maximum_retry_attempts: Number(form.retries),
        maximum_event_age_in_seconds: Number(form.age),
        on_success: form.success.trim(),
        on_failure: form.failure.trim(),
      });
      setConfig(data.config || null);
      setForm(null);
      setNotice("Asynchronous invocation settings saved.");
    } catch (err: any) {
      setError(err.message || "Unable to save asynchronous invocation settings");
    } finally {
      setBusy(false);
    }
  };

  const retries = config?.maximum_retry_attempts ?? 2;
  const age = config?.maximum_event_age_in_seconds ?? 21600;

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="How Lambda handles events from asynchronous invokes (InvocationType Event, S3, SNS, EventBridge)."
          actions={
            !form && (
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!config || loading} onClick={() => setRemoveOpen(true)}>Remove</Button>
                <Button
                  variant="primary"
                  disabled={loading}
                  onClick={() => setForm({ retries: String(retries), age: String(age), success: config?.on_success || "", failure: config?.on_failure || "" })}
                >
                  Edit
                </Button>
              </SpaceBetween>
            )
          }
        >
          Asynchronous invocation
        </Header>
      }
    >
      <SpaceBetween size="m">
        <FlociNote>
          Floci retries a failed asynchronous invoke as configured, about a
          minute and then two minutes later, sends the invocation record to the
          on-failure destination once retries run out, and sends successes to the
          on-success destination. It doesn&apos;t check that a destination exists,
          so a mistyped ARN loses the record.
        </FlociNote>
        {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
        {error && <Alert type="error">{error}</Alert>}
        {loading ? (
          <StatusIndicator type="loading">Loading settings</StatusIndicator>
        ) : form ? (
          <SpaceBetween size="m">
            <ColumnLayout columns={2}>
              <FormField label="Retry attempts" description="After the first failed attempt.">
                <Select
                  selectedOption={{ label: form.retries, value: form.retries }}
                  options={["0", "1", "2"].map((value) => ({ label: value, value }))}
                  onChange={({ detail }) => setForm({ ...form, retries: detail.selectedOption.value || "2" })}
                />
              </FormField>
              <FormField label="Maximum age of event (seconds)" constraintText="60 seconds to 6 hours (21600).">
                <Input type="number" value={form.age} onChange={({ detail }) => setForm({ ...form, age: detail.value })} />
              </FormField>
            </ColumnLayout>
            <FormField label="On-success destination" description="SQS queue, SNS topic, Lambda function or EventBridge bus ARN. Leave empty for none.">
              <Input value={form.success} onChange={({ detail }) => setForm({ ...form, success: detail.value })} placeholder="arn:aws:sqs:us-east-1:000000000000:orders-processed" />
            </FormField>
            <FormField label="On-failure destination" description="Gets the invocation record once retries run out or the event gets too old.">
              <Input value={form.failure} onChange={({ detail }) => setForm({ ...form, failure: detail.value })} placeholder="arn:aws:sqs:us-east-1:000000000000:orders-failed" />
            </FormField>
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setForm(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} onClick={save}>Save</Button>
            </SpaceBetween>
          </SpaceBetween>
        ) : (
          <ColumnLayout columns={2} variant="text-grid">
            <div><Box variant="awsui-key-label">Retry attempts</Box><div>{retries}{config ? "" : " (default)"}</div></div>
            <div><Box variant="awsui-key-label">Maximum age of event</Box><div>{Math.floor(age / 3600)}h {Math.floor((age % 3600) / 60)}m {age % 60}s{config ? "" : " (default)"}</div></div>
            <div><Box variant="awsui-key-label">On success</Box><div>{destinationLabel(config?.on_success)}</div></div>
            <div><Box variant="awsui-key-label">On failure</Box><div>{destinationLabel(config?.on_failure)}</div></div>
          </ColumnLayout>
        )}
      </SpaceBetween>
      <ConfirmActionModal
        visible={removeOpen}
        header="Remove asynchronous invocation settings"
        confirmLabel="Remove"
        loading={busy}
        onDismiss={() => setRemoveOpen(false)}
        onConfirm={async () => {
          setBusy(true);
          try {
            await serviceRequest("lambda", path, "DELETE");
            setConfig(null);
            setNotice("Settings removed. The function uses 2 retries, 6 hours and no destinations.");
          } catch (err: any) {
            setError(err.message || "Unable to remove the settings");
          } finally {
            setBusy(false);
            setRemoveOpen(false);
          }
        }}
      >
        The destinations are removed and retries go back to the defaults.
      </ConfirmActionModal>
    </Container>
  );
}
