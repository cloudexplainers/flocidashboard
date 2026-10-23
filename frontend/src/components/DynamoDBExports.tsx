import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "./ResourceTable";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface TableExport {
  arn: string;
  status?: string;
  export_type?: string;
  export_format?: string;
  s3_bucket?: string;
  s3_prefix?: string;
  manifest?: string;
  item_count?: number;
  billed_size_bytes?: number;
  start_time?: string;
  end_time?: string;
  failure_code?: string;
  failure_message?: string;
}

/** Exports a table to S3 (full export, DynamoDB JSON) and lists earlier exports. */
export function DynamoDBExports({ tableName, pitrEnabled }: { tableName: string; pitrEnabled?: boolean }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [exports, setExports] = useState<TableExport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [open, setOpen] = useState(false);
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const path = `tables/${encodeURIComponent(tableName)}/exports/`;

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("dynamodb", path);
      if (!alive.current) return;
      setExports(Array.isArray(data.exports) ? data.exports : []);
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load exports");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [tableName]);

  const submit = async () => {
    setBusy(true);
    setFormError("");
    try {
      await serviceRequest("dynamodb", path, "POST", { s3_bucket: bucket.trim(), s3_prefix: prefix.trim() });
      setOpen(false);
      setNotice(`Export of ${tableName} to s3://${bucket.trim()} started.`);
      await load();
    } catch (err: any) {
      setFormError(err.message || "Unable to start the export");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SpaceBetween size="m">
      <FlociNote>
        Floci writes a full export to the bucket as gzipped DynamoDB JSON with the
        same manifest files as AWS, and finishes within seconds. It supports only
        full exports in DynamoDB JSON (no ION, no incremental exports), and it
        doesn&apos;t require point-in-time recovery, so the console checks that
        itself.
      </FlociNote>
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert type="error">{error}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading exports"
        items={exports}
        trackBy="arn"
        header={
          <Header
            variant="h3"
            counter={`(${exports.length})`}
            description="Exports read from point-in-time recovery backups, so they use no read capacity."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" ariaLabel="Refresh exports" onClick={load} loading={loading} />
                <Button variant="primary" disabled={pitrEnabled === false} onClick={() => { setBucket(""); setPrefix(""); setFormError(""); setOpen(true); }}>Export to S3</Button>
              </SpaceBetween>
            }
          >
            Exports to S3
          </Header>
        }
        columnDefinitions={[
          { id: "status", header: "Status", cell: (item) => <StatusIndicator type={item.status === "COMPLETED" ? "success" : item.status === "FAILED" ? "error" : "in-progress"}>{item.status || "Unknown"}</StatusIndicator> },
          { id: "destination", header: "Destination", cell: (item) => `s3://${item.s3_bucket}/${item.s3_prefix ? `${item.s3_prefix}/` : ""}` },
          { id: "items", header: "Items", cell: (item) => item.item_count ?? "-" },
          { id: "type", header: "Type", cell: (item) => `${item.export_type || "-"} (${item.export_format || "-"})` },
          { id: "started", header: "Started", cell: (item) => (item.start_time ? new Date(item.start_time).toLocaleString() : "-") },
          { id: "detail", header: "Manifest or error", cell: (item) => (item.status === "FAILED" ? `${item.failure_code}: ${item.failure_message}` : item.manifest || "-") },
        ]}
        empty={<Box textAlign="center">{pitrEnabled === false ? "Turn on point-in-time recovery (Capacity and backups tab) to export this table." : "No exports yet."}</Box>}
      />
      <Modal
        visible={open}
        onDismiss={() => !busy && setOpen(false)}
        header={`Export ${tableName} to S3`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!bucket.trim()} onClick={submit}>Export</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="S3 bucket" description="An existing bucket. A missing bucket makes the export fail.">
            <Input value={bucket} onChange={({ detail }) => setBucket(detail.value)} placeholder="orders-exports" />
          </FormField>
          <FormField label="S3 prefix" description="Optional. Files go under <prefix>/AWSDynamoDB/<export id>/.">
            <Input value={prefix} onChange={({ detail }) => setPrefix(detail.value)} placeholder="daily" />
          </FormField>
          <Box variant="small">Full export, current table state, DynamoDB JSON.</Box>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
