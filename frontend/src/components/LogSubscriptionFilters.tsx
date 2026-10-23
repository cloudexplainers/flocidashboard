import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { serviceRequest } from "../api/client";

export interface SubscriptionFilter {
  filter_name: string;
  filter_pattern?: string;
  destination_arn?: string;
  role_arn?: string;
  distribution?: string;
  creation_time?: number;
}

const destinationKind = (arn = "") =>
  arn.includes(":kinesis:") ? "Kinesis stream"
    : arn.includes(":firehose:") ? "Firehose stream"
      : arn.includes(":lambda:") ? "Lambda function"
        : arn.includes(":logs:") ? "Logs destination" : "Destination";

/** Subscription filters stream matching log events from one log group to Kinesis, Firehose or Lambda. */
export function LogSubscriptionFilters({ groupName }: { groupName: string }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [filters, setFilters] = useState<SubscriptionFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<{ name: string; pattern: string; destination: string; role: string; distribution: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SubscriptionFilter | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("cloudwatch", `subscription-filters/?${new URLSearchParams({ log_group_name: groupName })}`);
      if (!alive.current) return;
      setFilters(Array.isArray(data.subscription_filters) ? data.subscription_filters : []);
      setLoadError("");
    } catch (err: any) {
      if (alive.current) setLoadError(err.message || "Unable to load subscription filters");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [groupName]);

  const submit = async () => {
    if (!form) return;
    setBusy(true);
    setFormError("");
    try {
      await serviceRequest("cloudwatch", "subscription-filters/", "PUT", {
        log_group_name: groupName,
        filter_name: form.name.trim(),
        filter_pattern: form.pattern,
        destination_arn: form.destination.trim(),
        role_arn: form.role.trim() || undefined,
        distribution: form.distribution,
      });
      setNotice(`Subscription filter "${form.name.trim()}" saved.`);
      setForm(null);
      await load();
    } catch (err: any) {
      setFormError(err.message || "Unable to save the subscription filter");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SpaceBetween size="m">
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {loadError && <Alert type="error" header="Subscription filters unavailable">{loadError}</Alert>}
      <Table
        variant="embedded"
        loading={loading}
        loadingText="Loading subscription filters"
        items={filters}
        trackBy="filter_name"
        header={
          <Header
            variant="h3"
            counter={`(${filters.length})`}
            description="Floci delivers matching events, gzip-compressed as in AWS, to Kinesis streams and Lambda functions as they arrive. AWS allows two filters per log group; Floci doesn't enforce that, so the console does."
            actions={
              <Button
                disabled={filters.length >= 2}
                onClick={() => {
                  setFormError("");
                  setForm({ name: "", pattern: "", destination: "", role: "", distribution: "ByLogStream" });
                }}
              >
                Create subscription filter
              </Button>
            }
          >
            Subscription filters
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Filter name", cell: (item) => item.filter_name },
          { id: "pattern", header: "Filter pattern", cell: (item) => item.filter_pattern || "All events" },
          { id: "destination", header: "Destination", cell: (item) => `${destinationKind(item.destination_arn)}: ${item.destination_arn}` },
          { id: "distribution", header: "Distribution", cell: (item) => item.distribution || "-" },
          {
            id: "actions",
            header: "Actions",
            cell: (item) => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="inline-link" ariaLabel={`Edit ${item.filter_name}`} onClick={() => { setFormError(""); setForm({ name: item.filter_name, pattern: item.filter_pattern || "", destination: item.destination_arn || "", role: item.role_arn || "", distribution: item.distribution || "ByLogStream" }); }}>Edit</Button>
                <Button variant="inline-link" ariaLabel={`Delete ${item.filter_name}`} onClick={() => { setFormError(""); setDeleteTarget(item); }}>Delete</Button>
              </SpaceBetween>
            ),
          },
        ]}
        empty={<Box textAlign="center">No subscription filters on this log group.</Box>}
      />
      <Modal
        visible={!!form}
        onDismiss={() => !busy && setForm(null)}
        header="Subscription filter"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setForm(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!form?.name.trim() || !form?.destination.trim()} onClick={submit}>Save filter</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {form && (
          <SpaceBetween size="m">
            {formError && <Alert type="error">{formError}</Alert>}
            <FormField label="Filter name" description="Saving with an existing name replaces that filter.">
              <Input value={form.name} onChange={({ detail }) => setForm({ ...form, name: detail.value })} placeholder="errors-to-kinesis" />
            </FormField>
            <FormField label="Filter pattern" description="Leave empty to send every event." constraintText='For example ERROR, or { $.level = "error" } for JSON logs.'>
              <Input value={form.pattern} onChange={({ detail }) => setForm({ ...form, pattern: detail.value })} />
            </FormField>
            <FormField label="Destination ARN" description="A Kinesis data stream, Firehose stream or Lambda function.">
              <Input value={form.destination} onChange={({ detail }) => setForm({ ...form, destination: detail.value })} placeholder="arn:aws:kinesis:us-east-1:000000000000:stream/app-logs" />
            </FormField>
            <FormField label="Role ARN" description="Required by AWS for Kinesis and Firehose destinations: CloudWatch Logs assumes it to write.">
              <Input value={form.role} onChange={({ detail }) => setForm({ ...form, role: detail.value })} placeholder="arn:aws:iam::000000000000:role/CWLtoKinesisRole" />
            </FormField>
            <FormField label="Distribution" description="How events spread across Kinesis shards.">
              <Select
                selectedOption={{ label: form.distribution, value: form.distribution }}
                options={[{ label: "ByLogStream", value: "ByLogStream" }, { label: "Random", value: "Random" }]}
                onChange={({ detail }) => setForm({ ...form, distribution: detail.selectedOption.value || "ByLogStream" })}
              />
            </FormField>
          </SpaceBetween>
        )}
      </Modal>
      <ConfirmActionModal
        visible={!!deleteTarget}
        header="Delete subscription filter"
        confirmLabel="Delete filter"
        loading={busy}
        onDismiss={() => setDeleteTarget(null)}
        onConfirm={async () => {
          if (!deleteTarget) return;
          setBusy(true);
          try {
            await serviceRequest("cloudwatch", "subscription-filters/", "DELETE", { log_group_name: groupName, filter_name: deleteTarget.filter_name });
            setNotice(`Subscription filter "${deleteTarget.filter_name}" deleted.`);
            setDeleteTarget(null);
            await load();
          } catch (err: any) {
            setFormError(err.message || "Unable to delete the subscription filter");
          } finally {
            setBusy(false);
          }
        }}
      >
        Events stop flowing to {deleteTarget?.destination_arn} once {deleteTarget?.filter_name} is deleted.
        {formError && <Box color="text-status-error">{formError}</Box>}
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
