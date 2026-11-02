import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";
import { formatBytes } from "../format";

export interface TableBackup {
  arn: string;
  name?: string;
  status?: string;
  type?: string;
  table?: string;
  size_bytes?: number;
  created?: string;
}

const NAME_PATTERN = /^[A-Za-z0-9_.-]{3,255}$/;
const nameError = (value: string, what: string) =>
  value.trim() && !NAME_PATTERN.test(value.trim()) ? `${what} must be 3-255 letters, numbers, underscores, hyphens or dots.` : undefined;

/** Default backup name, like the AWS console's: table name plus a UTC timestamp. */
function defaultBackupName(tableName: string) {
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 13);
  return `${tableName}-${stamp}`.slice(0, 255);
}

const isTimeValid = (value: string) => value.trim() !== "" && !Number.isNaN(new Date(value.trim()).valueOf());
const tableUrl = (tableName: string) => `tables/${encodeURIComponent(tableName)}`;

/** On-demand backups of a table: create, restore into a new table, and delete. */
export function DynamoDBBackups({ tableName, onRestored }: { tableName: string; onRestored: (message: string) => void }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [backups, setBackups] = useState<TableBackup[]>([]);
  const [selected, setSelected] = useState<TableBackup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modal, setModal] = useState<"create" | "restore" | "delete" | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const path = `${tableUrl(tableName)}/backups/`;
  const backup = selected[0];

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("dynamodb", path);
      if (!alive.current) return;
      const list: TableBackup[] = Array.isArray(data.backups) ? data.backups : [];
      setBackups(list);
      setSelected(previous => list.filter(item => item.arn === previous[0]?.arn));
      setError("");
    } catch (err: any) {
      if (alive.current) setError(err.message || "Unable to load backups");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, [tableName]);

  const open = (kind: "create" | "restore" | "delete") => {
    setFormError("");
    if (kind === "create") setName(defaultBackupName(tableName));
    if (kind === "restore") setName(`${tableName}-restored`);
    setModal(kind);
  };

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setFormError("");
    try {
      const message = await action();
      setModal(null);
      if (message) setNotice(message);
      await load();
    } catch (err: any) {
      setFormError(err.message || "The request failed");
    } finally {
      setBusy(false);
    }
  };

  const create = () => run(async () => {
    const result = await serviceRequest("dynamodb", path, "POST", { backup_name: name.trim() });
    return `Created backup ${result.name || name.trim()} of ${tableName}.`;
  });
  const restore = () => run(async () => {
    const target = name.trim();
    await serviceRequest("dynamodb", "backups/", "POST", { backup_arn: backup.arn, target_table_name: target });
    onRestored(`Restoring backup ${backup.name} into the new table ${target}.`);
    return "";
  });
  const remove = () => run(async () => {
    await serviceRequest("dynamodb", "backups/", "DELETE", { backup_arn: backup.arn });
    setSelected([]);
    return `Deleted backup ${backup.name}.`;
  });

  return (
    <SpaceBetween size="m">
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {error && <Alert type="error">{error}</Alert>}
      {modal === "delete" && formError && <Alert type="error">{formError}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading backups"
        items={backups}
        trackBy="arn"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        header={
          <Header
            variant="h3"
            counter={`(${backups.length})`}
            description="On-demand backups are full copies of the table that you keep until you delete them. Restoring creates a new table."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" ariaLabel="Refresh backups" onClick={load} loading={loading} />
                <Button disabled={!backup} onClick={() => open("delete")}>Delete</Button>
                <Button disabled={!backup || backup.status !== "AVAILABLE"} onClick={() => open("restore")}>Restore</Button>
                <Button variant="primary" onClick={() => open("create")}>Create backup</Button>
              </SpaceBetween>
            }
          >
            Backups
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Name", cell: (item) => item.name || "-" },
          { id: "status", header: "Status", cell: (item) => <StatusIndicator type={item.status === "AVAILABLE" ? "success" : item.status === "DELETED" ? "stopped" : "in-progress"}>{item.status || "Unknown"}</StatusIndicator> },
          { id: "type", header: "Type", cell: (item) => item.type || "-" },
          { id: "size", header: "Size", cell: (item) => (item.size_bytes === undefined ? "-" : formatBytes(item.size_bytes)) },
          { id: "created", header: "Created", cell: (item) => (item.created ? new Date(item.created).toLocaleString() : "-") },
        ]}
        empty={<Box textAlign="center">No backups of this table yet.</Box>}
      />
      <FlociNote>
        Floci keeps each backup as a snapshot of the table&apos;s items and indexes, and a
        restore builds a new table with the same keys and indexes. It reports every
        backup&apos;s size as 0 bytes, and the restored table doesn&apos;t keep the
        RestoreSummary that AWS shows on it.
      </FlociNote>

      <Modal
        visible={modal === "create"}
        onDismiss={() => !busy && setModal(null)}
        header={`Create backup of ${tableName}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setModal(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!NAME_PATTERN.test(name.trim())} onClick={create}>Create backup</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="Backup name" errorText={nameError(name, "Backup name")}>
            <Input value={name} onChange={({ detail }) => setName(detail.value)} />
          </FormField>
          <Box variant="small">The backup copies every item and index. It doesn&apos;t use the table&apos;s read capacity.</Box>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={modal === "restore"}
        onDismiss={() => !busy && setModal(null)}
        header="Restore backup"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setModal(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!NAME_PATTERN.test(name.trim())} onClick={restore}>Restore</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <Box>Creates a new table from backup <b>{backup?.name}</b>. {tableName} isn&apos;t changed.</Box>
          <FormField label="New table name" description="A table that doesn't exist yet." errorText={nameError(name, "Table name")}>
            <Input value={name} onChange={({ detail }) => setName(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={modal === "delete"}
        header="Delete backup?"
        confirmLabel="Delete"
        loading={busy}
        onConfirm={remove}
        onDismiss={() => setModal(null)}
      >
        Deleting backup {backup?.name} can&apos;t be undone. Tables already restored from it aren&apos;t affected.
      </ConfirmActionModal>
    </SpaceBetween>
  );
}

/** "Restore to point in time" button and dialog for a table with PITR on. */
export function DynamoDBPitrRestore({ tableName, pitrEnabled, onRestored }: { tableName: string; pitrEnabled?: boolean; onRestored: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"latest" | "custom">("latest");
  const [time, setTime] = useState("");
  const [target, setTarget] = useState("");
  const [earliest, setEarliest] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const show = async () => {
    setMode("latest");
    setTime("");
    setTarget(`${tableName}-pitr`);
    setFormError("");
    setEarliest("");
    setOpen(true);
    try {
      const data = await serviceRequest("dynamodb", `${tableUrl(tableName)}/backups/`);
      const pitr = data.continuous_backups?.PointInTimeRecoveryDescription || {};
      setEarliest(pitr.EarliestRestorableDateTime || "");
    } catch {
      // The dialog still works without the window; Floci checks the time itself.
    }
  };

  const submit = async () => {
    setBusy(true);
    setFormError("");
    try {
      const result = await serviceRequest("dynamodb", `${tableUrl(tableName)}/restore/`, "POST", {
        target_table_name: target.trim(),
        restore_time: mode === "custom" ? time.trim() : "",
      });
      const when = result.restore_time ? new Date(result.restore_time).toISOString() : "the latest restorable time";
      setOpen(false);
      onRestored(`Restoring ${tableName} as it was at ${when} into the new table ${target.trim()}.`);
    } catch (err: any) {
      setFormError(err.message || "Unable to restore the table");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button disabled={!pitrEnabled} onClick={show}>Restore to point in time</Button>
      <Modal
        visible={open}
        onDismiss={() => !busy && setOpen(false)}
        header="Restore to point in time"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!NAME_PATTERN.test(target.trim()) || (mode === "custom" && !isTimeValid(time))}
                onClick={submit}
              >
                Restore
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <Box>Creates a new table from {tableName} as it was at the time you choose. {tableName} isn&apos;t changed.</Box>
          <FormField label="Restore time">
            <RadioGroup
              value={mode}
              onChange={({ detail }) => setMode(detail.value as "latest" | "custom")}
              items={[
                { value: "latest", label: "Latest restorable time", description: "The table as it is now." },
                {
                  value: "custom",
                  label: "Custom date and time",
                  description: earliest ? `Earliest restorable time: ${new Date(earliest).toISOString()}` : undefined,
                },
              ]}
            />
          </FormField>
          {mode === "custom" && (
            <FormField
              label="Date and time (UTC)"
              constraintText="ISO 8601, for example 2026-11-02T15:30:00Z. It must be between the earliest restorable time and now."
              errorText={time.trim() && !isTimeValid(time) ? "Enter a valid date and time." : undefined}
            >
              <Input ariaLabel="Custom restore time" value={time} placeholder="2026-11-02T15:30:00Z" onChange={({ detail }) => setTime(detail.value)} />
            </FormField>
          )}
          <FormField label="New table name" description="A table that doesn't exist yet." errorText={nameError(target, "Table name")}>
            <Input value={target} onChange={({ detail }) => setTarget(detail.value)} />
          </FormField>
          <FlociNote>
            Floci keeps every change while PITR is on and restores the items as they were
            at the time you choose. It reports the latest restorable time as the moment
            PITR was turned on and never moves it forward, so for the latest time the
            console sends the current time instead (on AWS the latest restorable time is
            about five minutes ago). The new table has PITR off, as on AWS.
          </FlociNote>
        </SpaceBetween>
      </Modal>
    </>
  );
}
