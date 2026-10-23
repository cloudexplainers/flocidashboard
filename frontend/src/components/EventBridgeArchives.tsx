import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Multiselect from "@cloudscape-design/components/multiselect";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Textarea from "@cloudscape-design/components/textarea";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { serviceRequest } from "../api/client";

export interface Archive {
  name: string;
  arn?: string;
  event_source_arn?: string;
  description?: string;
  event_pattern?: string;
  state?: string;
  retention_days?: number;
  event_count?: number;
  size_bytes?: number;
  creation_time?: string;
}

export interface Replay {
  name: string;
  arn?: string;
  state?: string;
  state_reason?: string;
  event_source_arn?: string;
  destination_arn?: string;
  event_start_time?: string;
  event_end_time?: string;
  replay_start_time?: string;
  replay_end_time?: string;
}

type RuleOption = { name: string; arn?: string; bus: string };

const busFromArn = (arn = "") => arn.split("event-bus/")[1] || arn;
const archiveFromArn = (arn = "") => arn.split("archive/")[1] || arn;
const when = (value?: string) => (value ? new Date(value).toLocaleString() : "-");
const isoMinutesAgo = (minutes: number) =>
  new Date(Date.now() - minutes * 60_000).toISOString().replace(/\.\d+Z$/, "Z");

function replayStatus(state = "") {
  const type =
    state === "COMPLETED" ? "success"
      : state === "FAILED" ? "error"
        : state === "CANCELLED" ? "stopped"
          : state ? "in-progress" : "pending";
  return <StatusIndicator type={type}>{state || "Unknown"}</StatusIndicator>;
}

/** Event archives (what EventBridge keeps) and replays (sending archived events back to a bus). */
export function EventBridgeArchives({ buses, rules }: { buses: string[]; rules: RuleOption[] }) {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [archives, setArchives] = useState<Archive[]>([]);
  const [replays, setReplays] = useState<Replay[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [selected, setSelected] = useState<Archive[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState({ name: "", bus: "default", pattern: "", retention: "0", description: "" });
  const [replayForm, setReplayForm] = useState({ name: "", archive: "", start: "", end: "", rules: [] as string[] });

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("eventbridge", "archives/");
      if (!alive.current) return;
      const next: Archive[] = Array.isArray(data.archives) ? data.archives : [];
      setArchives(next);
      setReplays(Array.isArray(data.replays) ? data.replays : []);
      setSelected((previous) => next.filter((item) => previous.some((p) => p.name === item.name)));
      setLoadError("");
    } catch (err: any) {
      if (alive.current) setLoadError(err.message || "Unable to load archives and replays");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const run = async (work: () => Promise<string>, close: () => void) => {
    setBusy(true);
    setFormError("");
    try {
      const text = await work();
      close();
      setNotice({ type: "success", text });
      await load();
    } catch (err: any) {
      setFormError(err.message || "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const active = selected[0];
  const busOptions = (buses.length ? buses : ["default"]).map((name) => ({ label: name, value: name }));
  const replayArchive = archives.find((item) => item.name === replayForm.archive);
  const replayBus = busFromArn(replayArchive?.event_source_arn);
  const ruleOptions = rules
    .filter((rule) => rule.arn && rule.bus === replayBus)
    .map((rule) => ({ label: rule.name, value: rule.arn as string }));

  const openReplay = (archive?: Archive) => {
    setFormError("");
    setReplayForm({
      name: `${archive?.name || "replay"}-${Date.now().toString(36)}`,
      archive: archive?.name || archives[0]?.name || "",
      start: isoMinutesAgo(60),
      end: isoMinutesAgo(0),
      rules: [],
    });
    setReplayOpen(true);
  };

  return (
    <SpaceBetween size="l">
      <FlociNote>
        Floci records matching events in the archive and a replay sends them back
        to the source bus, where its rules deliver them to targets again. Two
        differences from AWS: replayed events don&apos;t carry the
        <code> replay-name</code> field, so a rule can&apos;t tell them from live
        events, and a replay finishes as soon as it starts.
      </FlociNote>
      {notice && (
        <Alert type={notice.type} dismissible onDismiss={() => setNotice(null)}>
          {notice.text}
        </Alert>
      )}
      {loadError && <Alert type="error" header="Archives unavailable">{loadError}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading archives"
        items={archives}
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="name"
        header={
          <Header
            variant="h2"
            counter={`(${archives.length})`}
            description="Archives keep events that match a pattern so you can replay them later."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" ariaLabel="Refresh archives" onClick={load} loading={loading} />
                <Button disabled={!active} onClick={() => { setFormError(""); setDeleteOpen(true); }}>Delete</Button>
                <Button disabled={!archives.length} onClick={() => openReplay(active)}>Start replay</Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    setFormError("");
                    setForm({ name: "", bus: busOptions[0].value, pattern: "", retention: "0", description: "" });
                    setCreateOpen(true);
                  }}
                >
                  Create archive
                </Button>
              </SpaceBetween>
            }
          >
            Archives
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Archive name", cell: (item) => item.name },
          { id: "source", header: "Source event bus", cell: (item) => busFromArn(item.event_source_arn) },
          { id: "state", header: "State", cell: (item) => <StatusIndicator type={item.state === "ENABLED" ? "success" : "stopped"}>{item.state || "Unknown"}</StatusIndicator> },
          { id: "events", header: "Events", cell: (item) => item.event_count ?? 0 },
          { id: "retention", header: "Retention", cell: (item) => (item.retention_days ? `${item.retention_days} days` : "Indefinite") },
          { id: "pattern", header: "Event pattern", cell: (item) => item.event_pattern || "All events" },
          { id: "created", header: "Created", cell: (item) => when(item.creation_time) },
        ]}
        empty={<Box textAlign="center">No archives. Create one to start keeping events.</Box>}
      />
      <Table
        loading={loading}
        loadingText="Loading replays"
        items={replays}
        trackBy="name"
        header={
          <Header variant="h2" counter={`(${replays.length})`} description="Each replay sends the events archived in a time window back to an event bus.">
            Replays
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Replay name", cell: (item) => item.name },
          { id: "archive", header: "Archive", cell: (item) => archiveFromArn(item.event_source_arn) },
          { id: "state", header: "Status", cell: (item) => replayStatus(item.state) },
          { id: "window", header: "Event time window", cell: (item) => `${when(item.event_start_time)} – ${when(item.event_end_time)}` },
          { id: "destination", header: "Destination", cell: (item) => busFromArn(item.destination_arn) },
          { id: "started", header: "Replay started", cell: (item) => when(item.replay_start_time) },
          {
            id: "actions",
            header: "Actions",
            cell: (item) =>
              ["STARTING", "RUNNING"].includes(item.state || "") ? (
                <Button
                  variant="inline-link"
                  ariaLabel={`Cancel replay ${item.name}`}
                  onClick={() =>
                    run(async () => {
                      await serviceRequest("eventbridge", "replays/cancel/", "POST", { name: item.name });
                      return `Replay "${item.name}" cancelled.`;
                    }, () => undefined)
                  }
                >
                  Cancel
                </Button>
              ) : (
                "-"
              ),
          },
        ]}
        empty={<Box textAlign="center">No replays yet.</Box>}
      />

      <Modal
        visible={createOpen}
        onDismiss={() => !busy && setCreateOpen(false)}
        header="Create archive"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!form.name.trim()}
                onClick={() =>
                  run(async () => {
                    await serviceRequest("eventbridge", "archives/create/", "POST", {
                      name: form.name.trim(),
                      event_bus_name: form.bus,
                      event_pattern: form.pattern.trim() || undefined,
                      retention_days: Number(form.retention || 0),
                      description: form.description,
                    });
                    return `Archive "${form.name.trim()}" created.`;
                  }, () => setCreateOpen(false))
                }
              >
                Create archive
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="Archive name">
            <Input value={form.name} onChange={({ detail }) => setForm({ ...form, name: detail.value })} placeholder="orders-archive" />
          </FormField>
          <FormField label="Source event bus">
            <Select
              selectedOption={{ label: form.bus, value: form.bus }}
              options={busOptions}
              onChange={({ detail }) => setForm({ ...form, bus: detail.selectedOption.value || "default" })}
            />
          </FormField>
          <FormField label="Event pattern" description="Optional. Leave empty to archive every event on the bus." constraintText="JSON object, the same syntax as a rule's event pattern.">
            <Textarea rows={4} value={form.pattern} onChange={({ detail }) => setForm({ ...form, pattern: detail.value })} placeholder='{"source": ["ecommerce.orders"]}' />
          </FormField>
          <FormField label="Retention period (days)" description="0 keeps events indefinitely.">
            <Input type="number" value={form.retention} onChange={({ detail }) => setForm({ ...form, retention: detail.value })} />
          </FormField>
          <FormField label="Description">
            <Input value={form.description} onChange={({ detail }) => setForm({ ...form, description: detail.value })} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={replayOpen}
        onDismiss={() => !busy && setReplayOpen(false)}
        header="Start replay"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setReplayOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!replayForm.name.trim() || !replayForm.archive}
                onClick={() =>
                  run(async () => {
                    await serviceRequest("eventbridge", "replays/start/", "POST", {
                      name: replayForm.name.trim(),
                      archive_name: replayForm.archive,
                      start_time: replayForm.start,
                      end_time: replayForm.end,
                      rule_arns: replayForm.rules,
                    });
                    return `Replay "${replayForm.name.trim()}" started.`;
                  }, () => setReplayOpen(false))
                }
              >
                Start replay
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {formError && <Alert type="error">{formError}</Alert>}
          <FormField label="Replay name" constraintText="Replay names can't be reused.">
            <Input value={replayForm.name} onChange={({ detail }) => setReplayForm({ ...replayForm, name: detail.value })} />
          </FormField>
          <FormField label="Archive">
            <Select
              selectedOption={replayForm.archive ? { label: replayForm.archive, value: replayForm.archive } : null}
              options={archives.map((item) => ({ label: item.name, value: item.name }))}
              onChange={({ detail }) => setReplayForm({ ...replayForm, archive: detail.selectedOption.value || "", rules: [] })}
            />
          </FormField>
          <FormField label="Event start time" constraintText="ISO 8601, UTC. Only events archived in this window are replayed.">
            <Input value={replayForm.start} onChange={({ detail }) => setReplayForm({ ...replayForm, start: detail.value })} />
          </FormField>
          <FormField label="Event end time">
            <Input value={replayForm.end} onChange={({ detail }) => setReplayForm({ ...replayForm, end: detail.value })} />
          </FormField>
          <FormField label="Destination" description="Events go back to the archive's source bus.">
            <Box>{replayBus || "-"}</Box>
          </FormField>
          <FormField label="Rules" description="Optional. Limit the replay to these rules; leave empty to replay to every rule on the bus.">
            <Multiselect
              selectedOptions={ruleOptions.filter((option) => replayForm.rules.includes(option.value))}
              options={ruleOptions}
              placeholder="All rules"
              empty="No rules on this bus"
              onChange={({ detail }) => setReplayForm({ ...replayForm, rules: detail.selectedOptions.map((option) => option.value as string) })}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={deleteOpen}
        header="Delete archive"
        confirmLabel="Delete archive"
        confirmationText={active?.name}
        loading={busy}
        onDismiss={() => setDeleteOpen(false)}
        onConfirm={() =>
          active &&
          run(async () => {
            await serviceRequest("eventbridge", "archives/delete/", "POST", { name: active.name });
            return `Archive "${active.name}" deleted.`;
          }, () => setDeleteOpen(false))
        }
      >
        Deleting {active?.name} removes the events it holds. You can&apos;t replay them afterwards.
        {formError && <Box color="text-status-error">{formError}</Box>}
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
