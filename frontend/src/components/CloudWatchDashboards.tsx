import { useEffect, useRef, useState } from "react";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";
import Table from "./ResourceTable";
import ConfirmActionModal from "./ConfirmActionModal";
import { FlociNote } from "./FlociNote";
import { Metric, MetricExplorer } from "./MetricExplorer";
import { serviceRequest } from "../api/client";

export interface DashboardEntry {
  name: string;
  arn?: string;
  last_modified?: string;
  size?: number;
  body?: string;
}

type Widget = {
  type?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  properties?: Record<string, any>;
};

const STARTER_BODY = JSON.stringify(
  {
    widgets: [
      { type: "text", x: 0, y: 0, width: 24, height: 2, properties: { markdown: "# Orders service\nRequests and errors for the last hour." } },
      {
        type: "metric",
        x: 0,
        y: 2,
        width: 12,
        height: 6,
        properties: { title: "Lambda invocations", region: "us-east-1", stat: "Sum", period: 300, metrics: [["AWS/Lambda", "Invocations", "FunctionName", "orders"]] },
      },
    ],
  },
  null,
  2,
);

/**
 * Turns a metric widget's `metrics` rows into metrics. Each row is
 * [Namespace, MetricName, DimName, DimValue, ..., {options}]; "." repeats one value of the previous row and
 * "..." all the ones that follow, so ["...", "i-2"] is the previous metric with its last value replaced.
 */
export function widgetMetrics(rows: unknown): Metric[] {
  if (!Array.isArray(rows)) return [];
  const result: Metric[] = [];
  let previous: string[] = [];
  for (const row of rows) {
    if (!Array.isArray(row)) continue;
    const parts = row.filter((part): part is string => typeof part === "string");
    let values: string[] = [];
    parts.forEach((part, index) => {
      // "..." copies the previous row's values, leaving room for the values after it.
      if (part === "...") values = [...values, ...previous.slice(values.length, previous.length - (parts.length - index - 1))];
      else values.push(part === "." ? previous[values.length] ?? "" : part);
    });
    if (values.length < 2 || !values[0] || !values[1]) continue;
    previous = values;
    const dimensions = [];
    for (let i = 2; i + 1 < values.length; i += 2) dimensions.push({ Name: values[i], Value: values[i + 1] });
    const options = row.find((part) => part && typeof part === "object" && !Array.isArray(part)) as { stat?: string } | undefined;
    result.push({ Namespace: values[0], MetricName: values[1], Dimensions: dimensions, ...(options?.stat ? { Statistic: options.stat } : {}) });
  }
  return result;
}

function parseWidgets(body = ""): { widgets: Widget[]; error: string } {
  try {
    const parsed = JSON.parse(body || "{}");
    const widgets: Widget[] = Array.isArray(parsed?.widgets) ? parsed.widgets : [];
    widgets.sort((a, b) => (a.y ?? 0) - (b.y ?? 0) || (a.x ?? 0) - (b.x ?? 0));
    return { widgets, error: "" };
  } catch {
    return { widgets: [], error: "This dashboard body isn't valid JSON." };
  }
}

function WidgetView({ widget, index }: { widget: Widget; index: number }) {
  const props = widget.properties || {};
  const title = props.title || `${widget.type || "Unknown"} widget ${index + 1}`;
  if (widget.type === "text") {
    return (
      <Container header={<Header variant="h3">Text</Header>}>
        <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{String(props.markdown ?? "")}</div>
      </Container>
    );
  }
  if (widget.type === "metric") {
    const metrics = widgetMetrics(props.metrics);
    if (!metrics.length)
      return (
        <Container header={<Header variant="h3">{title}</Header>}>
          <Box color="text-status-inactive">This metric widget lists no metrics.</Box>
        </Container>
      );
    const preferred = props.stat ? { ...metrics[0], Statistic: metrics[0].Statistic || props.stat } : metrics[0];
    return (
      <SpaceBetween size="xs">
        <Box variant="h3">{title}</Box>
        <MetricExplorer metrics={metrics} preferredMetric={preferred} />
      </SpaceBetween>
    );
  }
  return (
    <Container header={<Header variant="h3">{title}</Header>}>
      <SpaceBetween size="xs">
        <Box color="text-status-inactive">The console doesn&apos;t draw {widget.type || "untyped"} widgets. Its definition:</Box>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: 0 }}>{JSON.stringify(widget, null, 2)}</pre>
      </SpaceBetween>
    </Container>
  );
}

/** CloudWatch dashboards: list, view (text and metric widgets), create or edit the JSON body, delete. */
export function CloudWatchDashboards() {
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const [items, setItems] = useState<DashboardEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selected, setSelected] = useState<DashboardEntry[]>([]);
  const [notice, setNotice] = useState("");
  const [editor, setEditor] = useState<{ name: string; body: string; existing: boolean } | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await serviceRequest("cloudwatch", "dashboards/");
      if (!alive.current) return;
      const next: DashboardEntry[] = Array.isArray(data.dashboards) ? data.dashboards : [];
      setItems(next);
      setSelected((previous) => next.filter((item) => previous.some((p) => p.name === item.name)));
      setLoadError("");
    } catch (err: any) {
      if (alive.current) setLoadError(err.message || "Unable to load dashboards");
    } finally {
      if (alive.current) setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const active = selected[0];
  const { widgets, error: bodyError } = parseWidgets(active?.body);

  const save = async () => {
    if (!editor) return;
    setBusy(true);
    setFormError("");
    try {
      try {
        JSON.parse(editor.body);
      } catch {
        throw new Error("Dashboard body must be valid JSON.");
      }
      const result = await serviceRequest("cloudwatch", "dashboards/", "PUT", { dashboard_name: editor.name.trim(), dashboard_body: editor.body });
      const warnings = (result.validation_messages || []).map((m: any) => m.Message).filter(Boolean);
      setNotice(`Dashboard "${editor.name.trim()}" saved.${warnings.length ? ` Warnings: ${warnings.join("; ")}` : ""}`);
      setEditor(null);
      setSelected([{ name: editor.name.trim() }]);
      await load();
    } catch (err: any) {
      setFormError(err.message || "Unable to save the dashboard");
    } finally {
      setBusy(false);
    }
  };

  return (
    <SpaceBetween size="l">
      <FlociNote>
        Floci stores each dashboard&apos;s JSON body and rejects a body that isn&apos;t
        JSON. The console draws text and metric widgets from that body itself;
        other widget types show their definition.
      </FlociNote>
      {notice && <Alert type="success" dismissible onDismiss={() => setNotice("")}>{notice}</Alert>}
      {loadError && <Alert type="error" header="Dashboards unavailable">{loadError}</Alert>}
      <Table
        loading={loading}
        loadingText="Loading dashboards"
        items={items}
        trackBy="name"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        header={
          <Header
            variant="h2"
            counter={`(${items.length})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" ariaLabel="Refresh dashboards" onClick={load} loading={loading} />
                <Button disabled={!active} onClick={() => { setFormError(""); setDeleteOpen(true); }}>Delete</Button>
                <Button disabled={!active} onClick={() => { setFormError(""); setEditor({ name: active!.name, body: active!.body || "{}", existing: true }); }}>Edit</Button>
                <Button variant="primary" onClick={() => { setFormError(""); setEditor({ name: "", body: STARTER_BODY, existing: false }); }}>Create dashboard</Button>
              </SpaceBetween>
            }
          >
            Dashboards
          </Header>
        }
        columnDefinitions={[
          { id: "name", header: "Name", cell: (item) => item.name },
          { id: "widgets", header: "Widgets", cell: (item) => parseWidgets(item.body).widgets.length },
          { id: "modified", header: "Last updated", cell: (item) => (item.last_modified ? new Date(item.last_modified).toLocaleString() : "-") },
          { id: "size", header: "Size (bytes)", cell: (item) => item.size ?? "-" },
        ]}
        empty={<Box textAlign="center">No dashboards. Create one to chart metrics side by side.</Box>}
      />
      {active && (
        <Container header={<Header variant="h2" description={`${widgets.length} widget${widgets.length === 1 ? "" : "s"}`}>{active.name}</Header>}>
          {bodyError ? (
            <Alert type="warning">{bodyError}</Alert>
          ) : widgets.length ? (
            <Grid gridDefinition={widgets.map((w) => ({ colspan: { default: 12, m: Math.min(12, Math.max(3, Math.round((w.width || 6) / 2))) } }))}>
              {widgets.map((widget, index) => <WidgetView key={index} widget={widget} index={index} />)}
            </Grid>
          ) : (
            <Box color="text-status-inactive">This dashboard has no widgets.</Box>
          )}
        </Container>
      )}
      <Modal
        visible={!!editor}
        size="large"
        onDismiss={() => !busy && setEditor(null)}
        header={editor?.existing ? `Edit ${editor.name}` : "Create dashboard"}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" disabled={busy} onClick={() => setEditor(null)}>Cancel</Button>
              <Button variant="primary" loading={busy} disabled={!editor?.name.trim()} onClick={save}>Save dashboard</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {editor && (
          <SpaceBetween size="m">
            {formError && <Alert type="error">{formError}</Alert>}
            <FormField label="Dashboard name" constraintText="Letters, numbers, - and _ only.">
              <Input value={editor.name} disabled={editor.existing} onChange={({ detail }) => setEditor({ ...editor, name: detail.value })} placeholder="orders-overview" />
            </FormField>
            <FormField label="Dashboard body" description='JSON with a "widgets" list. Metric rows are [Namespace, MetricName, DimensionName, DimensionValue, …].'>
              <Textarea rows={16} value={editor.body} onChange={({ detail }) => setEditor({ ...editor, body: detail.value })} spellcheck={false} />
            </FormField>
          </SpaceBetween>
        )}
      </Modal>
      <ConfirmActionModal
        visible={deleteOpen}
        header="Delete dashboard"
        confirmLabel="Delete dashboard"
        confirmationText={active?.name}
        loading={busy}
        onDismiss={() => setDeleteOpen(false)}
        onConfirm={async () => {
          if (!active) return;
          setBusy(true);
          try {
            await serviceRequest("cloudwatch", "dashboards/", "DELETE", { dashboard_name: active.name });
            setNotice(`Dashboard "${active.name}" deleted.`);
            setDeleteOpen(false);
            setSelected([]);
            await load();
          } catch (err: any) {
            setFormError(err.message || "Unable to delete the dashboard");
          } finally {
            setBusy(false);
          }
        }}
      >
        Deleting {active?.name} removes its widgets. Metrics and alarms aren&apos;t affected.
        {formError && <Box color="text-status-error">{formError}</Box>}
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
