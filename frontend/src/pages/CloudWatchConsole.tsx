import { LogStreamExplorer } from "../components/LogStreamExplorer";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import { MetricExplorer } from "../components/MetricExplorer";
import React, { useState, useEffect, useRef } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Badge from "@cloudscape-design/components/badge";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Grid from "@cloudscape-design/components/grid";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Textarea from "@cloudscape-design/components/textarea";
import { formatBytes } from "../format";
import { useSession } from "../components/SessionContext";
import {
  alarmCondition,
  cloudWatchAlarms,
  monitoringDate,
  retentionLabel,
} from "./cloudwatchResources";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import { cloudWatchRequest } from "../api/client";
import { FlociNote } from '../components/FlociNote';

interface CloudWatchConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const CloudWatchConsole: React.FC<CloudWatchConsoleProps> = (props) => {
  const { identity, details } = useSession();
  return (
    <CloudWatchWorkspace
      key={JSON.stringify([
        identity?.arn,
        identity?.account_id,
        identity?.region,
        identity?.endpoint,
        identity?.expires_at,
        details?.session_identity?.access_key_id,
      ])}
      {...props}
    />
  );
};
/** Alarm states as the CloudWatch console labels them. */
export const alarmStateLabel = (state?: string) =>
  state === "ALARM" ? "In alarm" : state === "OK" ? "OK" : state === "INSUFFICIENT_DATA" ? "Insufficient data" : state || "Not reported";
const AlarmState = ({ state }: { state?: string }) => (
  <StatusIndicator
    type={
      state === "ALARM"
        ? "error"
        : state === "OK"
          ? "success"
          : state === "INSUFFICIENT_DATA"
            ? "warning"
            : "not-started"
    }
  >
    {alarmStateLabel(state)}
  </StatusIndicator>
);
const Detail = ({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) => (
  <div>
    <Box variant="awsui-key-label">{label}</Box>
    <div style={{ overflowWrap: "anywhere" }}>{children ?? "—"}</div>
  </div>
);
const CloudWatchWorkspace: React.FC<CloudWatchConsoleProps> = ({
  activeTab,
  onTabChange,
  resourcePath = "cloudwatch",
  onNavigate,
}) => {
  const [loadError, setLoadError] = useState("");
  const [stateFilter, setStateFilter] = useState("all");
  const [localAlarm, setLocalAlarm] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<any>(null);
  const [stateTarget, setStateTarget] = useState<any>(null);
  const [simulatedState, setSimulatedState] = useState("ALARM");
  const [stateReason, setStateReason] = useState(
    "Manual state test from the CloudWatch console",
  );
  const [mutating, setMutating] = useState(false);
  const [modalError, setModalError] = useState("");
  const mounted = useRef(true);
  const loadRevision = useRef(0);
  const mutationLock = useRef(false);
  const [statistic, setStatistic] = useState("Average");
  const [period, setPeriod] = useState("300");
  const [evaluationPeriods, setEvaluationPeriods] = useState("1");
  const [datapointsToAlarm, setDatapointsToAlarm] = useState("1");
  const [missingData, setMissingData] = useState("missing");
  const [alarmDimensions, setAlarmDimensions] = useState("[]");
  const [data, setData] = useState<any>({ metric_alarms: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState("");
  const [selectedAlarms, setSelectedAlarms] = useState<any[]>([]);
  const [actionMessage, setActionMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || "alarms");

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Create Alarm Modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [alarmName, setAlarmName] = useState("");
  const [metricName, setMetricName] = useState("CPUUtilization");
  const [namespace, setNamespace] = useState("AWS/EC2");
  const [threshold, setThreshold] = useState("80");
  const [comparisonOp, setComparisonOp] = useState({
    label: "GreaterThanThreshold (>)",
    value: "GreaterThanThreshold",
  });
  const [creating, setCreating] = useState(false);

  // Metrics Ingest State
  const [ingestNamespace, setIngestNamespace] = useState("MyApp/CustomMetrics");
  const [ingestMetricName, setIngestMetricName] = useState("RequestLatency");
  const [ingestValue, setIngestValue] = useState("42.5");
  const [ingestUnit, setIngestUnit] = useState({
    label: "Milliseconds",
    value: "Milliseconds",
  });
  const [publishingMetric, setPublishingMetric] = useState(false);
  const [metricsList, setMetricsList] = useState<any[]>([]);

  // CloudWatch Logs
  const [logGroups, setLogGroups] = useState<any[]>([]);
  const [selectedLogGroup, setSelectedLogGroup] = useState<any>(null);
  const [logRevision, setLogRevision] = useState(0);
  const [focusedMetric, setFocusedMetric] = useState<any>(null);

  // Retention Modal
  const [retentionModalOpen, setRetentionModalOpen] = useState(false);
  const [selectedRetention, setSelectedRetention] = useState({
    label: "30 days",
    value: "30",
  });
  const [savingRetention, setSavingRetention] = useState(false);

  // Create Stream Modal
  const [createStreamOpen, setCreateStreamOpen] = useState(false);
  const [newStreamName, setNewStreamName] = useState("");
  const [creatingStream, setCreatingStream] = useState(false);

  const loadData = async () => {
    const version = ++loadRevision.current;
    setLoading(true);
    setLoadError("");
    try {
      const res = await cloudWatchRequest();
      if (!mounted.current || version !== loadRevision.current) return;
      setData(res);
      setSelectedAlarms((previous) =>
        cloudWatchAlarms(res).filter((alarm) =>
          previous.some((item) => item.AlarmName === alarm.AlarmName),
        ),
      );
      setMetricsList(res.metrics || []);
      const groups = (res.log_groups || []).map((group: any) => ({
        ...group,
        logGroupName: group.logGroupName || group.name,
        retentionInDays: group.retentionInDays ?? group.retention_days,
        storedBytes: group.storedBytes ?? group.stored_bytes,
        arn: group.arn,
      }));
      setLogGroups(groups);
      setSelectedLogGroup((previous: any) => {
        const group =
          groups.find((item: any) => item.logGroupName === previous?.value) ||
          groups[0];
        return group
          ? { label: group.logGroupName, value: group.logGroupName }
          : null;
      });
    } catch (err: any) {
      if (mounted.current && version === loadRevision.current)
        setLoadError(err.message);
    } finally {
      if (mounted.current && version === loadRevision.current)
        setLoading(false);
    }
  };
  useEffect(() => {
    mounted.current = true;
    void loadData();
    return () => {
      mounted.current = false;
    };
  }, []);

  const handleCreateAlarm = async () => {
    if (!alarmName.trim() || mutationLock.current) return;
    mutationLock.current = true;
    setModalError("");
    setCreating(true);
    setActionMessage(null);
    try {
      await cloudWatchRequest("alarms/", "POST", {
        alarm_name: alarmName.trim(),
        metric_name: metricName,
        namespace,
        threshold: threshold === "" ? null : Number(threshold),
        comparison_operator: comparisonOp.value,
        evaluation_periods: Number(evaluationPeriods),
        datapoints_to_alarm: Number(datapointsToAlarm),
        period: Number(period),
        statistic,
        treat_missing_data: missingData,
        dimensions: JSON.parse(alarmDimensions),
      });
      setActionMessage({
        type: "success",
        text: `Metric Alarm "${alarmName.trim()}" created successfully.`,
      });
      setCreateModalOpen(false);
      setAlarmName("");
      await loadData();
    } catch (err: any) {
      setModalError(err.message || "Failed to create metric alarm");
    } finally {
      mutationLock.current = false;
      if (mounted.current) setCreating(false);
    }
  };

  const alarmMutation = async (kind: "state" | "delete") => {
    const alarm = kind === "state" ? stateTarget : deleteTarget;
    if (!alarm || mutationLock.current) return;
    mutationLock.current = true;
    setMutating(true);
    setModalError("");
    try {
      await cloudWatchRequest(
        kind === "state" ? "alarms/state/" : "alarms/",
        kind === "state" ? "POST" : "DELETE",
        {
          alarm_name: alarm.AlarmName,
          ...(kind === "state"
            ? { state_value: simulatedState, state_reason: stateReason }
            : {}),
        },
      );
      if (!mounted.current) return;
      setActionMessage({
        type: "success",
        text:
          kind === "state"
            ? `Set ${alarm.AlarmName} to ${simulatedState}.`
            : `Deleted ${alarm.AlarmName}.`,
      });
      setStateTarget(null);
      setDeleteTarget(null);
      setSelectedAlarms([]);
      if (kind === "delete") {
        setLocalAlarm("");
        onNavigate?.("cloudwatch/alarms");
      }
      await loadData();
    } catch (err: any) {
      if (mounted.current) setModalError(err.message);
    } finally {
      mutationLock.current = false;
      if (mounted.current) setMutating(false);
    }
  };

  const handlePublishMetric = async () => {
    if (!ingestNamespace.trim() || !ingestMetricName.trim()) return;
    setPublishingMetric(true);
    try {
      if (!ingestValue.trim() || !Number.isFinite(Number(ingestValue)))
        throw new Error("Enter a valid metric value.");
      await cloudWatchRequest("metric-data/", "POST", {
        namespace: ingestNamespace.trim(),
        metric_name: ingestMetricName.trim(),
        value: Number(ingestValue),
        unit: ingestUnit.value,
      });
      setMetricsList((prev) => [
        {
          Namespace: ingestNamespace.trim(),
          MetricName: ingestMetricName.trim(),
        },
        ...prev.filter(
          (m) =>
            !(
              m.Namespace === ingestNamespace.trim() &&
              m.MetricName === ingestMetricName.trim()
            ),
        ),
      ]);
      setActionMessage({
        type: "success",
        text: `Metric "${ingestMetricName.trim()}" published to "${ingestNamespace.trim()}".`,
      });
    } catch (err: any) {
      setActionMessage({
        type: "error",
        text: err.message || "Failed to publish metric",
      });
    } finally {
      setPublishingMetric(false);
    }
  };

  const handleSaveRetention = async () => {
    if (!selectedLogGroup?.value) return;
    setSavingRetention(true);
    try {
      await cloudWatchRequest(
        "retention/",
        Number(selectedRetention.value) === 0 ? "DELETE" : "PUT",
        {
          log_group_name: selectedLogGroup.value,
          ...(Number(selectedRetention.value) === 0
            ? {}
            : { retention_in_days: Number(selectedRetention.value) }),
        },
      );
      setActionMessage({
        type: "success",
        text: `Retention policy updated for ${selectedLogGroup.value}.`,
      });
      setRetentionModalOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({
        type: "error",
        text: err.message || "Failed to update retention policy",
      });
      setModalError(err.message || "Failed to update retention policy");
    } finally {
      setSavingRetention(false);
    }
  };

  const handleCreateLogStream = async () => {
    if (!selectedLogGroup?.value || !newStreamName.trim()) return;
    setCreatingStream(true);
    try {
      await cloudWatchRequest("log-streams/create/", "POST", {
        log_group_name: selectedLogGroup.value,
        log_stream_name: newStreamName.trim(),
      });
      setActionMessage({
        type: "success",
        text: `Log stream "${newStreamName.trim()}" created.`,
      });
      setCreateStreamOpen(false);
      setNewStreamName("");
      setLogRevision((value) => value + 1);
    } catch (err: any) {
      setActionMessage({
        type: "error",
        text: err.message || "Failed to create log stream",
      });
      setModalError(err.message || "Failed to create log stream");
    } finally {
      setCreatingStream(false);
    }
  };

  const alarmsList = cloudWatchAlarms(data);
  const openAlarmName = onNavigate
    ? new URLSearchParams(resourcePath.split("?")[1] || "").get("alarm")
    : localAlarm;
  const openedAlarm = alarmsList.find(
    (alarm: any) => alarm.AlarmName === openAlarmName,
  );
  const activeAlarm = openedAlarm || selectedAlarms[0];
  const group = logGroups.find(
    (item) => item.logGroupName === selectedLogGroup?.value,
  );
  const focusAlarm = (alarm: any) => {
    setLocalAlarm(alarm.AlarmName);
    setSelectedAlarms([alarm]);
    onNavigate?.(
      `cloudwatch/alarms?alarm=${encodeURIComponent(alarm.AlarmName)}`,
    );
  };
  const viewMetric = (alarm: any) => {
    setFocusedMetric({
      Namespace: alarm.Namespace,
      MetricName: alarm.MetricName,
      Dimensions: alarm.Dimensions || [],
      Statistic: alarm.Statistic,
    });
    setSelectedTabId("metrics");
    onTabChange?.("metrics");
  };
  const alarmButtons = (
    <SpaceBetween direction="horizontal" size="xs">
      <Button
        disabled={!activeAlarm}
        onClick={() => {
          setStateTarget({ ...activeAlarm });
          setSimulatedState("ALARM");
          setModalError("");
        }}
      >
        Set state for testing
      </Button>
      <Button
        disabled={!activeAlarm}
        onClick={() => {
          setDeleteTarget({ ...activeAlarm });
          setModalError("");
        }}
      >
        Delete alarm
      </Button>
    </SpaceBetween>
  );
  const filteredAlarms = alarmsList.filter((a: any) => {
    const text = `${a.AlarmName} ${a.MetricName} ${a.Namespace}`.toLowerCase();
    return (
      text.includes(filterText.toLowerCase()) &&
      (stateFilter === "all" || a.StateValue === stateFilter)
    );
  });

  return (
    <SpaceBetween size="l">
      {loadError && (
        <Alert type="error" header="Unable to load CloudWatch resources">
          {loadError}
        </Alert>
      )}
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            description="Observability and monitoring for AWS cloud resources and applications."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button
                  variant="primary"
                  iconName="add-plus"
                  onClick={() => {
                    setModalError("");
                    setCreateModalOpen(true);
                  }}
                >
                  Create alarm
                </Button>
              </SpaceBetween>
            }
          >
            {openAlarmName && selectedTabId === "alarms"
              ? `Alarm: ${openAlarmName}`
              : "Amazon CloudWatch"}
          </Header>
        }
      >
        {actionMessage && (
          <Box margin={{ bottom: "m" }}>
            <Alert
              type={actionMessage.type}
              dismissible
              onDismiss={() => setActionMessage(null)}
            >
              {actionMessage.text}
            </Alert>
          </Box>
        )}

        {!openAlarmName && (
          <div className="console-summary">
            <Box padding="m" textAlign="center">
              <Box variant="awsui-key-label">Alarms</Box>
              <Box variant="h1" color="text-status-info">
                {loading ? "—" : alarmsList.length}
              </Box>
            </Box>
            <Box padding="m" textAlign="center">
              <Box variant="awsui-key-label">Log groups</Box>
              <Box variant="h1" color="text-status-info">
                {loading ? "—" : logGroups.length}
              </Box>
            </Box>
            <Box padding="m" textAlign="center">
              <Box variant="awsui-key-label">Metrics</Box>
              <Box variant="h2" color="text-status-info">
                <span>
                  {loading ? "—" : metricsList.length} metrics reported
                </span>
              </Box>
            </Box>
          </div>
        )}
      </Container>

      {!openAlarmName && (
        <Container
          header={
            <Header
              variant="h2"
              description="Select a state to filter the alarm list."
            >
              Alarm health
            </Header>
          }
        >
          <ColumnLayout columns={4} variant="text-grid">
            {["all", "ALARM", "OK", "INSUFFICIENT_DATA"].map((state) => (
              <div key={state}>
                <Button
                  variant="inline-link"
                  onClick={() => {
                    setStateFilter(state);
                    setSelectedAlarms([]);
                    setSelectedTabId("alarms");
                    onTabChange?.("alarms");
                  }}
                >
                  {state === "all" ? "All alarms" : alarmStateLabel(state)}
                </Button>
                <Box variant="h1">
                  {loading
                    ? "—"
                    : state === "all"
                      ? alarmsList.length
                      : alarmsList.filter((alarm) => alarm.StateValue === state)
                          .length}
                </Box>
              </div>
            ))}
          </ColumnLayout>
        </Container>
      )}
      {openAlarmName &&
        selectedTabId === "alarms" &&
        (openedAlarm ? (
          <SpaceBetween size="m">
            <Button
              variant="link"
              iconName="arrow-left"
              onClick={() => {
                setLocalAlarm("");
                onNavigate?.("cloudwatch/alarms");
              }}
            >
              Back to alarms
            </Button>
            <Header variant="h2" actions={alarmButtons}>
              Alarm configuration
            </Header>
            <Container
              header={
                <Header
                  variant="h2"
                  actions={
                    <Button
                      disabled={
                        !openedAlarm.MetricName || !openedAlarm.Namespace
                      }
                      onClick={() => viewMetric(openedAlarm)}
                    >
                      View metric
                    </Button>
                  }
                >
                  Alarm details
                </Header>
              }
            >
              <ColumnLayout columns={2} variant="text-grid">
                <Detail label="State">
                  <AlarmState state={openedAlarm.StateValue} />
                </Detail>
                <Detail label="Last state update">
                  {monitoringDate(openedAlarm.StateUpdatedTimestamp)}
                </Detail>
                <Detail label="State reason">{openedAlarm.StateReason}</Detail>
                <Detail label="Alarm ARN">{openedAlarm.AlarmArn}</Detail>
                <Detail label="Description">
                  {openedAlarm.AlarmDescription}
                </Detail>
                <Detail label="Condition">{alarmCondition(openedAlarm)}</Detail>
                <Detail label="Metric">
                  {openedAlarm.MetricName ||
                    (openedAlarm.Metrics
                      ? "Metric math expression"
                      : "Not reported")}
                </Detail>
                <Detail label="Namespace">{openedAlarm.Namespace}</Detail>
                <Detail label="Statistic">
                  {openedAlarm.ExtendedStatistic || openedAlarm.Statistic}
                </Detail>
                <Detail label="Period">
                  {openedAlarm.Period == null
                    ? "—"
                    : `${openedAlarm.Period} seconds`}
                </Detail>
                <Detail label="Datapoints to alarm">
                  {openedAlarm.EvaluationPeriods == null
                    ? "—"
                    : `${openedAlarm.DatapointsToAlarm ?? openedAlarm.EvaluationPeriods} of ${openedAlarm.EvaluationPeriods}`}
                </Detail>
                <Detail label="Missing data treatment">
                  {openedAlarm.TreatMissingData || "missing (default)"}
                </Detail>
                <Detail label="Dimensions">
                  {openedAlarm.Dimensions.length
                    ? openedAlarm.Dimensions.map(
                        (d: any) => `${d.Name} = ${d.Value}`,
                      ).join(", ")
                    : "None"}
                </Detail>
                <Detail label="Actions">
                  {openedAlarm.ActionsEnabled === true
                    ? "Enabled"
                    : openedAlarm.ActionsEnabled === false
                      ? "Disabled"
                      : "Not reported"}
                </Detail>
              </ColumnLayout>
            </Container>
            <Container header={<Header variant="h2">Action targets</Header>}>
              <SpaceBetween size="m">
              <FlociNote>
                Floci evaluates the alarm and changes its state, but of the actions
                it runs only Application Auto Scaling scaling policies. SNS
                notifications, EC2 actions and EC2 Auto Scaling policies listed here
                don&apos;t run in Floci 2.3.0; newer Floci releases add SNS
                notifications.
              </FlociNote>
              <ColumnLayout columns={3} variant="text-grid">
                {[
                  ["ALARM", openedAlarm.AlarmActions],
                  ["OK", openedAlarm.OKActions],
                  ["INSUFFICIENT_DATA", openedAlarm.InsufficientDataActions],
                ].map(([label, targets]: any) => (
                  <Detail key={label} label={label}>
                    {targets?.length ? targets.join(", ") : "None reported"}
                  </Detail>
                ))}
              </ColumnLayout>
              </SpaceBetween>
            </Container>
          </SpaceBetween>
        ) : (
          !loading && (
            <Alert type="warning">
              Alarm not found. Select another alarm from the list.
            </Alert>
          )
        ))}
      {/* Main Tabs */}
      {!(openAlarmName && selectedTabId === "alarms") && (
        <Tabs
          activeTabId={selectedTabId}
          onChange={({ detail }) => {
            setSelectedTabId(detail.activeTabId);
            onTabChange?.(detail.activeTabId);
          }}
          tabs={[
            {
              label: `Alarms (${alarmsList.length})`,
              id: "alarms",
              content: (
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={openedAlarm ? undefined : alarmButtons}
                    >
                      Alarms
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <FormField label="Alarm state">
                      <Select
                        ariaLabel="Alarm state"
                        selectedOption={{
                          value: stateFilter,
                          label:
                            stateFilter === "all" ? "All alarms" : alarmStateLabel(stateFilter),
                        }}
                        options={[
                          "all",
                          "ALARM",
                          "OK",
                          "INSUFFICIENT_DATA",
                        ].map((value) => ({
                          value,
                          label: value === "all" ? "All alarms" : alarmStateLabel(value),
                        }))}
                        onChange={({ detail }) => {
                          setStateFilter(detail.selectedOption.value!);
                          setSelectedAlarms([]);
                        }}
                      />
                    </FormField>
                    <TextFilter
                      filteringText={filterText}
                      filteringPlaceholder="Filter alarms by name, metric, or namespace..."
                      onChange={({ detail }) => {
                        setFilterText(detail.filteringText);
                        setSelectedAlarms([]);
                      }}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: "name",
                          header: "Name",
                          minWidth: 210,
                          cell: (item) => (
                            <Button
                              variant="inline-link"
                              onClick={() => focusAlarm(item)}
                            >
                              {item.AlarmName}
                            </Button>
                          ),
                        },
                        {
                          id: "state",
                          header: "State",
                          cell: (item) => (
                            <AlarmState state={item.StateValue} />
                          ),
                          minWidth: 180,
                        },
                        {
                          id: "metric",
                          header: "Metric",
                          cell: (item) => (
                            <code>
                              {item.MetricName ||
                                (item.Metrics ? "Metric math" : "—")}
                            </code>
                          ),
                          minWidth: 180,
                        },
                        {
                          id: "namespace",
                          header: "Namespace",
                          cell: (item) => (
                            <Badge color="blue">{item.Namespace || "—"}</Badge>
                          ),
                          minWidth: 170,
                        },
                        {
                          id: "threshold",
                          header: "Conditions",
                          cell: (item) => <span>{alarmCondition(item)}</span>,
                          minWidth: 180,
                        },
                        {
                          id: "updated",
                          header: "Last state update",
                          cell: (item) => monitoringDate(item.StateUpdatedTimestamp),
                          minWidth: 200,
                        },
                        {
                          id: "actions",
                          header: "Actions",
                          cell: (item) =>
                            item.ActionsEnabled === false
                              ? "Actions disabled"
                              : [...(item.AlarmActions || []), ...(item.OKActions || []), ...(item.InsufficientDataActions || [])].length
                                ? "Actions enabled"
                                : "No actions",
                          minWidth: 150,
                        },
                      ]}
                      loading={loading}
                      loadingText="Loading alarms"
                      items={filteredAlarms}
                      selectionType="single"
                      selectedItems={selectedAlarms}
                      onSelectionChange={({ detail }) =>
                        setSelectedAlarms(detail.selectedItems)
                      }
                      empty={
                        <Box textAlign="center">No metric alarms found.</Box>
                      }
                    />
                  </SpaceBetween>
                </Container>
              ),
            },
            {
              label: "Metrics",
              id: "metrics",
              content: (
                <SpaceBetween size="l">
                  <MetricExplorer
                    key={JSON.stringify(focusedMetric)}
                    metrics={
                      focusedMetric &&
                      !metricsList.some(
                        (metric) =>
                          metric.Namespace === focusedMetric.Namespace &&
                          metric.MetricName === focusedMetric.MetricName &&
                          JSON.stringify(metric.Dimensions || []) ===
                            JSON.stringify(focusedMetric.Dimensions || []),
                      )
                        ? [...metricsList, focusedMetric]
                        : metricsList
                    }
                    preferredMetric={focusedMetric}
                  />
                  <ExpandableSection headerText="Publish custom metric data" headerDescription="Calls PutMetricData. Custom metrics appear in the Metrics list after the first data point.">
                    <SpaceBetween size="m">
                      <Grid
                        gridDefinition={[
                          { colspan: { default: 12, s: 3 } },
                          { colspan: { default: 12, s: 3 } },
                          { colspan: { default: 12, s: 3 } },
                          { colspan: { default: 12, s: 3 } },
                        ]}
                      >
                        <FormField label="Namespace">
                          <Input
                            value={ingestNamespace}
                            onChange={({ detail }) =>
                              setIngestNamespace(detail.value)
                            }
                            placeholder="MyApp/Metrics"
                          />
                        </FormField>
                        <FormField label="Metric name">
                          <Input
                            value={ingestMetricName}
                            onChange={({ detail }) =>
                              setIngestMetricName(detail.value)
                            }
                            placeholder="RequestCount"
                          />
                        </FormField>
                        <FormField label="Value">
                          <Input
                            value={ingestValue}
                            onChange={({ detail }) =>
                              setIngestValue(detail.value)
                            }
                            type="number"
                          />
                        </FormField>
                        <FormField label="Unit">
                          <Select
                            selectedOption={ingestUnit}
                            onChange={({ detail }) =>
                              setIngestUnit(detail.selectedOption as any)
                            }
                            options={[
                              { label: "Count", value: "Count" },
                              { label: "Milliseconds", value: "Milliseconds" },
                              { label: "Bytes", value: "Bytes" },
                              { label: "Percent", value: "Percent" },
                            ]}
                          />
                        </FormField>
                      </Grid>
                      <Button
                        variant="primary"
                        loading={publishingMetric}
                        onClick={handlePublishMetric}
                      >
                        Publish metric data
                      </Button>
                    </SpaceBetween>
                  </ExpandableSection>
                </SpaceBetween>
              ),
            },
            {
              label: `CloudWatch Logs (${logGroups.length})`,
              id: "logs",
              content: (
                <Container
                  header={
                    <Header
                      variant="h2"
                      description="Inspect log streams and retrieved events from the selected log group."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            disabled={!selectedLogGroup?.value}
                            onClick={() => {
                              const days = group?.retentionInDays ?? 0;
                              setSelectedRetention({
                                label: retentionLabel(days),
                                value: String(days),
                              });
                              setModalError("");
                              setRetentionModalOpen(true);
                            }}
                          >
                            Edit retention setting
                          </Button>
                          <Button
                            disabled={!selectedLogGroup?.value}
                            variant="primary"
                            iconName="add-plus"
                            onClick={() => {
                              setModalError("");
                              setCreateStreamOpen(true);
                            }}
                          >
                            Create log stream
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      Log groups
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <FormField label="Log group">
                      <Select
                        selectedOption={selectedLogGroup}
                        onChange={({ detail }) =>
                          setSelectedLogGroup(detail.selectedOption)
                        }
                        options={logGroups.map((g) => ({
                          label: g.logGroupName,
                          value: g.logGroupName,
                        }))}
                      />
                    </FormField>

                    {group && (
                      <ColumnLayout columns={3} variant="text-grid">
                        <Detail label="Retention">
                          {retentionLabel(group.retentionInDays)}
                        </Detail>
                        <Detail label="Stored bytes">
                          {group.storedBytes == null ? "—" : formatBytes(group.storedBytes)}
                        </Detail>
                        <Detail label="Log group ARN">{group.arn}</Detail>
                      </ColumnLayout>
                    )}
                    {!logGroups.length && !loading && (
                      <Box>
                        No log groups reported. Create a log group in your
                        application to inspect its streams.
                      </Box>
                    )}
                    {selectedLogGroup?.value && (
                      <LogStreamExplorer
                        key={selectedLogGroup.value}
                        groupName={selectedLogGroup.value}
                        revision={logRevision}
                      />
                    )}
                  </SpaceBetween>
                </Container>
              ),
            },
          ]}
        />
      )}
      <Modal
        visible={!!deleteTarget}
        onDismiss={() => {
          if (!mutating) setDeleteTarget(null);
        }}
        header="Delete alarm"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button disabled={mutating} onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={mutating}
                onClick={() => alarmMutation("delete")}
              >
                Delete alarm
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <Alert type="warning">
            Delete alarm <strong>{deleteTarget?.AlarmName}</strong>? The
            monitored metric and its data will remain available.
          </Alert>
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!stateTarget}
        onDismiss={() => {
          if (!mutating) setStateTarget(null);
        }}
        header={`Set state: ${stateTarget?.AlarmName || ""}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button disabled={mutating} onClick={() => setStateTarget(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={mutating}
                disabled={!stateReason.trim()}
                onClick={() => alarmMutation("state")}
              >
                Set state
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <Alert type="info">
            This temporarily overrides the alarm state for testing. Configured
            actions may run when the state changes. Metric evaluation may change
            the state again.
          </Alert>
          <FormField label="State">
            <Select
              selectedOption={{ label: simulatedState, value: simulatedState }}
              options={["ALARM", "OK", "INSUFFICIENT_DATA"].map((value) => ({
                label: value,
                value,
              }))}
              onChange={({ detail }) =>
                setSimulatedState(detail.selectedOption.value!)
              }
            />
          </FormField>
          <FormField label="State reason">
            <Textarea
              value={stateReason}
              onChange={({ detail }) => setStateReason(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
      {/* Create Alarm Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => {
          if (!creating) setCreateModalOpen(false);
        }}
        header="Create CloudWatch Metric Alarm"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creating}
                disabled={
                  !alarmName.trim() ||
                  !metricName.trim() ||
                  !namespace.trim() ||
                  !threshold.trim()
                }
                onClick={handleCreateAlarm}
              >
                Create alarm
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Alarm name" description="Must be unique within the Region.">
            <Input
              value={alarmName}
              onChange={({ detail }) => setAlarmName(detail.value)}
              placeholder="HighCPUAlarm"
            />
          </FormField>

          <FormField label="Metric name">
            <Input
              value={metricName}
              onChange={({ detail }) => setMetricName(detail.value)}
              placeholder="CPUUtilization"
            />
          </FormField>

          <FormField label="Namespace">
            <Input
              value={namespace}
              onChange={({ detail }) => setNamespace(detail.value)}
              placeholder="AWS/EC2"
            />
          </FormField>

          <FormField label="Threshold (Value)">
            <Input
              type="number"
              value={threshold}
              onChange={({ detail }) => setThreshold(detail.value)}
              placeholder="80"
            />
          </FormField>

          <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
            <FormField label="Statistic">
              <Select
                selectedOption={{ label: statistic, value: statistic }}
                options={[
                  "Average",
                  "Sum",
                  "Minimum",
                  "Maximum",
                  "SampleCount",
                ].map((value) => ({ label: value, value }))}
                onChange={({ detail }) =>
                  setStatistic(detail.selectedOption.value!)
                }
              />
            </FormField>
            <FormField label="Period (seconds)">
              <Input
                type="number"
                value={period}
                onChange={({ detail }) => setPeriod(detail.value)}
              />
            </FormField>
            <FormField label="Evaluation periods">
              <Input
                type="number"
                value={evaluationPeriods}
                onChange={({ detail }) => setEvaluationPeriods(detail.value)}
              />
            </FormField>
            <FormField label="Datapoints to alarm">
              <Input
                type="number"
                value={datapointsToAlarm}
                onChange={({ detail }) => setDatapointsToAlarm(detail.value)}
              />
            </FormField>
          </Grid>
          <FormField label="Missing data treatment">
            <Select
              selectedOption={{ label: missingData, value: missingData }}
              options={["missing", "ignore", "breaching", "notBreaching"].map(
                (value) => ({ label: value, value }),
              )}
              onChange={({ detail }) =>
                setMissingData(detail.selectedOption.value!)
              }
            />
          </FormField>
          <FormField
            label="Dimensions (JSON)"
            description={
              'An array of {"Name":"...","Value":"..."} objects. Use [] for no dimensions.'
            }
          >
            <Textarea
              value={alarmDimensions}
              onChange={({ detail }) => setAlarmDimensions(detail.value)}
            />
          </FormField>
          <FormField label="Comparison Operator">
            <Select
              selectedOption={comparisonOp}
              onChange={({ detail }) =>
                setComparisonOp(detail.selectedOption as any)
              }
              options={[
                {
                  label: "GreaterThanThreshold (>)",
                  value: "GreaterThanThreshold",
                },
                {
                  label: "GreaterThanOrEqualToThreshold (>=)",
                  value: "GreaterThanOrEqualToThreshold",
                },
                { label: "LessThanThreshold (<)", value: "LessThanThreshold" },
                {
                  label: "LessThanOrEqualToThreshold (<=)",
                  value: "LessThanOrEqualToThreshold",
                },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Retention Settings Modal */}
      <Modal
        visible={retentionModalOpen}
        onDismiss={() => setRetentionModalOpen(false)}
        header={`Set Retention for ${selectedLogGroup?.value}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setRetentionModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={savingRetention}
                onClick={handleSaveRetention}
              >
                Save Retention
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Retention Period">
            <Select
              selectedOption={selectedRetention}
              onChange={({ detail }) =>
                setSelectedRetention(detail.selectedOption as any)
              }
              options={[
                { label: "1 day", value: "1" },
                { label: "7 days", value: "7" },
                { label: "30 days", value: "30" },
                { label: "90 days", value: "90" },
                { label: "365 days", value: "365" },
                { label: "Never expire", value: "0" },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Log Stream Modal */}
      <Modal
        visible={createStreamOpen}
        onDismiss={() => setCreateStreamOpen(false)}
        header={`Create log stream in ${selectedLogGroup?.value}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateStreamOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creatingStream}
                disabled={!newStreamName.trim()}
                onClick={handleCreateLogStream}
              >
                Create Stream
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Log Stream Name">
            <Input
              value={newStreamName}
              onChange={({ detail }) => setNewStreamName(detail.value)}
              placeholder="2026/08/24/[$LATEST]abc123"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
