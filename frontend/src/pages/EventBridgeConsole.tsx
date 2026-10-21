import {
  EventBridgeTargets,
  EventTarget,
} from "../components/EventBridgeTargets";
import { EventPatternTester } from "../components/EventPatternTester";
import { useSession } from "../components/SessionContext";
import { CodeSnippet } from "../components/CodeSnippet";
import React, { useState, useEffect, useRef } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Table from "../components/ResourceTable";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Badge from "@cloudscape-design/components/badge";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Grid from "@cloudscape-design/components/grid";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import {
  createEventBus,
  deleteEventBus,
  putEventRule,
  setEventRuleState,
  deleteEventRule,
  putEventTarget,
  removeEventTarget,
  putEvents,
} from "../api/client";

interface RuleItem {
  Name: string;
  Arn?: string;
  State: string;
  EventBusName: string;
  EventPattern?: string;
  ScheduleExpression?: string;
  Description?: string;
  targets?: EventTarget[];
  target_count?: number;
}

interface BusItem {
  Name: string;
  Arn?: string;
  Description?: string;
  Policy?: string;
  rule_count?: number;
  target_count?: number;
  rules?: RuleItem[];
}

type Props = { resourcePath?: string; onNavigate?: (path: string) => void };
export const EventBridgeConsole: React.FC<Props> = (props) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <StatusIndicator type="loading">Loading session context</StatusIndicator>
    );
  return (
    <EventBridgeWorkspace
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
const EventBridgeWorkspace: React.FC<Props> = ({
  resourcePath = "eventbridge",
  onNavigate,
}) => {
  const alive = useRef(true);
  const generation = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      generation.current++;
    };
  }, []);
  const query = new URLSearchParams(resourcePath.split("?")[1] || "");
  const pinnedRule = query.get("rule");
  const pinnedBus = query.get("bus") || "default";
  const ruleUrl = (rule: RuleItem, tab = "rules") =>
    `eventbridge/${tab}?${new URLSearchParams({ bus: rule.EventBusName, rule: rule.Name })}`;
  useEffect(() => {
    setTopTabId(resourcePath.split("?")[0].split("/")[1] || "rules");
  }, [resourcePath]);
  const [topTabId, setTopTabId] = useState("rules");
  const [buses, setBuses] = useState<BusItem[]>([]);
  const [rules, setRules] = useState<RuleItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Selected state
  const [selectedRules, setSelectedRules] = useState<RuleItem[]>([]);
  const [selectedBuses, setSelectedBuses] = useState<BusItem[]>([]);
  const [filterText, setFilterText] = useState("");
  const [busFilter, setBusFilter] = useState<{ label: string; value: string }>({
    label: "All Event Buses",
    value: "ALL",
  });
  const [actionAlert, setActionAlert] = useState<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);

  // Modals
  const [createRuleOpen, setCreateRuleOpen] = useState(false);
  const [createBusOpen, setCreateBusOpen] = useState(false);
  const [addTargetOpen, setAddTargetOpen] = useState(false);
  const [deleteRuleOpen, setDeleteRuleOpen] = useState(false);
  const [deleteBusOpen, setDeleteBusOpen] = useState(false);

  // Create Rule form state
  const [ruleName, setRuleName] = useState("");
  const [ruleBusName, setRuleBusName] = useState("default");
  const [ruleDescription, setRuleDescription] = useState(
    "Routes application events",
  );
  const [eventPattern, setEventPattern] = useState(
    '{\n  "source": ["ecommerce.orders"],\n  "detail-type": ["OrderPlaced"]\n}',
  );
  const [scheduleExpr, setScheduleExpr] = useState("");
  const [creatingRule, setCreatingRule] = useState(false);

  // Create Bus form state
  const [newBusName, setNewBusName] = useState("");
  const [creatingBus, setCreatingBus] = useState(false);

  // Add Target form state
  const [targetId, setTargetId] = useState("target-1");
  const [targetArn, setTargetArn] = useState("");
  const [targetRoleArn, setTargetRoleArn] = useState("");
  const [removeTargetId, setRemoveTargetId] = useState<string | null>(null);
  const [targetInput, setTargetInput] = useState("");
  const [addingTarget, setAddingTarget] = useState(false);

  // Send Event form state
  const [sendBusName, setSendBusName] = useState("default");
  const [eventSource, setEventSource] = useState("ecommerce.orders");
  const [detailType, setDetailType] = useState("OrderPlaced");
  const [eventDetail, setEventDetail] = useState(
    '{\n  "orderId": "ord-98214",\n  "amount": 49.99,\n  "customer": "Alice"\n}',
  );
  const [sendingEvent, setSendingEvent] = useState(false);
  const [sendResult, setSendResult] = useState<any | null>(null);

  const activeRule = pinnedRule
    ? rules.find(
        (rule) => rule.Name === pinnedRule && rule.EventBusName === pinnedBus,
      ) || null
    : selectedRules[0] || null;
  const activeBus = selectedBuses[0] || null;
  const ruleWorkspace =
    Boolean(pinnedRule) && !["buses", "send"].includes(topTabId);
  useEffect(() => {
    if (query.get("bus")) setSendBusName(pinnedBus);
  }, [pinnedBus]);

  const loadData = async () => {
    const version = ++generation.current;
    setLoading(true);
    try {
      const response = await fetch("/api/eventbridge/");
      const res = await response.json();
      if (!response.ok || res.error)
        throw new Error(res.error || "Unable to load EventBridge inventory");
      if (!alive.current || version !== generation.current) return;
      const rawBuses = res.event_buses || res.buses || [];
      const allRules: RuleItem[] = [];
      const busList: BusItem[] = rawBuses.map((b: any) => {
        const bName = b.name || b.Name || "default";
        const bRules = (b.rules || []).map((r: any) => {
          const ruleObj: RuleItem = {
            Name: r.name || r.Name || "",
            Arn: r.arn || r.Arn,
            State: r.state || r.State || "Unknown",
            EventBusName: bName,
            EventPattern: r.event_pattern || r.EventPattern || "",
            ScheduleExpression: r.schedule_expression || r.ScheduleExpression,
            Description: r.description || r.Description || "",
            targets: Array.isArray(r.targets) ? r.targets : [],
            target_count: r.target_count ?? (r.targets ? r.targets.length : 0),
          };
          allRules.push(ruleObj);
          return ruleObj;
        });

        return {
          Name: bName,
          Arn: b.arn || b.Arn,
          Description: b.description || b.Description || "",
          Policy: b.policy || b.Policy || "",
          rule_count: bRules.length,
          target_count: bRules.reduce(
            (acc: number, cur: RuleItem) => acc + (cur.target_count || 0),
            0,
          ),
          rules: bRules,
        };
      });

      // If top-level rules list exists directly
      if (res.rules && res.rules.length > 0 && allRules.length === 0) {
        res.rules.forEach((r: any) => {
          allRules.push({
            Name: r.Name || r.name,
            Arn: r.Arn || r.arn,
            State: r.State || r.state || "Unknown",
            EventBusName: r.EventBusName || r.event_bus_name || "default",
            EventPattern: r.EventPattern || r.event_pattern || "",
            ScheduleExpression: r.ScheduleExpression || r.schedule_expression,
            Description: r.Description || r.description || "",
            targets: Array.isArray(r.targets) ? r.targets : [],
            target_count: r.target_count ?? (r.targets ? r.targets.length : 0),
          });
        });
      }

      setBuses(busList);
      setRules(allRules);

      setSelectedRules((previous) =>
        previous.flatMap((selected) => {
          const refreshed = allRules.find(
            (r) =>
              r.Name === selected.Name &&
              r.EventBusName === selected.EventBusName,
          );
          return refreshed ? [refreshed] : [];
        }),
      );
    } catch (err: any) {
      if (!alive.current || version !== generation.current) return;
      setBuses([]);
      setRules([]);
      setSelectedRules([]);
      setActionAlert({
        type: "error",
        message: err.message || "Failed to load EventBridge resources.",
      });
    } finally {
      if (alive.current && version === generation.current) setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateRule = async () => {
    if (!ruleName.trim()) return;
    setCreatingRule(true);
    setActionAlert(null);
    try {
      let patternToSend: string | undefined = undefined;
      if (eventPattern.trim()) {
        try {
          const parsed = JSON.parse(eventPattern);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
            throw new Error();
          patternToSend = JSON.stringify(parsed);
        } catch (e) {
          throw new Error("Event pattern must be a valid JSON object.");
        }
      }

      await putEventRule(
        ruleName.trim(),
        ruleBusName.trim() || "default",
        patternToSend,
        scheduleExpr.trim() || undefined,
        ruleDescription.trim() || undefined,
        "ENABLED",
      );
      setActionAlert({
        type: "success",
        message: `EventBridge rule "${ruleName.trim()}" created successfully.`,
      });
      setCreateRuleOpen(false);
      setRuleName("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create rule.",
      });
    } finally {
      setCreatingRule(false);
    }
  };

  const handleToggleRuleState = async (rule: RuleItem) => {
    const isEnabling = rule.State !== "ENABLED";
    try {
      await setEventRuleState(rule.Name, rule.EventBusName, isEnabling);
      setActionAlert({
        type: "success",
        message: `Rule "${rule.Name}" ${isEnabling ? "enabled" : "disabled"}.`,
      });
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to change rule state.",
      });
    }
  };

  const handleDeleteRule = async () => {
    if (!activeRule) return;
    try {
      await deleteEventRule(activeRule.Name, activeRule.EventBusName);
      setActionAlert({
        type: "success",
        message: `Rule "${activeRule.Name}" deleted.`,
      });
      setDeleteRuleOpen(false);
      setSelectedRules([]);
      onNavigate?.("eventbridge/rules");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to delete rule.",
      });
    }
  };

  const handleAddTarget = async () => {
    if (!activeRule || !targetId.trim() || !targetArn.trim()) return;
    setAddingTarget(true);
    setActionAlert(null);
    try {
      if (targetInput.trim()) JSON.parse(targetInput);
      await putEventTarget(
        activeRule.Name,
        activeRule.EventBusName,
        targetId.trim(),
        targetArn.trim(),
        targetInput.trim() || undefined,
        targetRoleArn.trim(),
      );
      setActionAlert({
        type: "success",
        message: `Target "${targetId}" added to rule "${activeRule.Name}".`,
      });
      setAddTargetOpen(false);
      setTargetId("target-" + Math.floor(Math.random() * 1000));
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to attach target.",
      });
    } finally {
      setAddingTarget(false);
    }
  };

  const handleRemoveTarget = async (id: string) => {
    if (!activeRule) return;
    try {
      await removeEventTarget(activeRule.Name, activeRule.EventBusName, id);
      setActionAlert({
        type: "success",
        message: `Target "${id}" removed from rule.`,
      });
      setRemoveTargetId(null);
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to remove target.",
      });
    }
  };

  const handleCreateBus = async () => {
    if (!newBusName.trim()) return;
    setCreatingBus(true);
    setActionAlert(null);
    try {
      await createEventBus(newBusName.trim());
      setActionAlert({
        type: "success",
        message: `Event Bus "${newBusName.trim()}" created.`,
      });
      setCreateBusOpen(false);
      setNewBusName("");
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to create event bus.",
      });
    } finally {
      setCreatingBus(false);
    }
  };

  const handleDeleteBus = async () => {
    if (!activeBus || activeBus.Name === "default") return;
    try {
      await deleteEventBus(activeBus.Name);
      setActionAlert({
        type: "success",
        message: `Event bus "${activeBus.Name}" deleted.`,
      });
      setDeleteBusOpen(false);
      setSelectedBuses([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to delete event bus.",
      });
    }
  };

  const handleSendEvent = async () => {
    setSendingEvent(true);
    setSendResult(null);
    setActionAlert(null);
    try {
      const parsedDetail = JSON.parse(eventDetail);
      if (
        !parsedDetail ||
        typeof parsedDetail !== "object" ||
        Array.isArray(parsedDetail)
      )
        throw new Error("Event detail must be a JSON object.");
      const res = await putEvents([
        {
          EventBusName: sendBusName || "default",
          Source: eventSource,
          DetailType: detailType,
          Detail: JSON.stringify(parsedDetail),
        },
      ]);
      setSendResult(res);
      const failed =
        Number(res.failed_entry_count) > 0 || Boolean(res.error_code);
      setActionAlert({
        type: failed ? "error" : "success",
        message: failed
          ? `Event rejected: ${res.error_message || res.error_code || "See publish result."}`
          : "Event accepted by EventBridge. Target delivery is not confirmed by this response.",
      });
    } catch (err: any) {
      setActionAlert({
        type: "error",
        message: err.message || "Failed to send event.",
      });
    } finally {
      setSendingEvent(false);
    }
  };

  const busOptions = [
    { label: "All Event Buses", value: "ALL" },
    ...buses.map((b) => ({ label: b.Name, value: b.Name })),
  ];

  const filteredRules = rules.filter((r) => {
    const matchesBus =
      busFilter.value === "ALL" || r.EventBusName === busFilter.value;
    const matchesText =
      `${r.Name} ${r.EventBusName} ${r.EventPattern} ${r.Description}`
        .toLowerCase()
        .includes(filterText.toLowerCase());
    return matchesBus && matchesText;
  });

  return (
    <SpaceBetween size="l">
      {actionAlert && (
        <Alert
          type={actionAlert.type}
          dismissible
          onDismiss={() => setActionAlert(null)}
          header={
            actionAlert.type === "error"
              ? "EventBridge Error"
              : "EventBridge Notification"
          }
        >
          {actionAlert.message}
        </Alert>
      )}

      {/* Header */}
      {!ruleWorkspace && (
        <Container
          header={
            <Header
              variant="h1"
              counter={`(${rules.length})`}
              description="Serverless event bus that connects applications using data from custom sources, AWS services, and SaaS applications."
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button
                    iconName="refresh"
                    onClick={loadData}
                    loading={loading}
                  >
                    Refresh
                  </Button>
                  <Button
                    variant="primary"
                    iconName="add-plus"
                    onClick={() => setCreateRuleOpen(true)}
                  >
                    Create Rule
                  </Button>
                </SpaceBetween>
              }
            >
              Amazon EventBridge
            </Header>
          }
        >
          <div className="console-summary">
            <Box padding="m" textAlign="center">
              <Box variant="awsui-key-label">Event Rules</Box>
              <Box variant="h1" color="text-status-info">
                {rules.length}
              </Box>
            </Box>
            <Box padding="m" textAlign="center">
              <Box variant="awsui-key-label">Event Buses</Box>
              <Box variant="h1" color="text-status-info">
                {buses.length}
              </Box>
            </Box>
            <Box padding="m" textAlign="center">
              <Box variant="awsui-key-label">Event Routing Engine</Box>
              <Box variant="h2" color="text-status-info">
                <span>
                  {rules.filter((rule) => rule.State === "ENABLED").length}{" "}
                  enabled rules reported
                </span>
              </Box>
            </Box>
          </div>
        </Container>
      )}
      {ruleWorkspace && (
        <Header
          variant="h1"
          description={`Event bus: ${pinnedBus}`}
          actions={
            <Button onClick={loadData} loading={loading} iconName="refresh">
              Refresh
            </Button>
          }
        >
          Rule: {pinnedRule}
        </Header>
      )}
      {ruleWorkspace && (
        <Button
          variant="link"
          iconName="arrow-left"
          onClick={() => {
            setSelectedRules([]);
            onNavigate?.("eventbridge/rules");
          }}
        >
          Rules
        </Button>
      )}
      {pinnedRule && !loading && !activeRule && (
        <Alert type="warning" header="Rule not found">
          This rule is not reported on the selected event bus.
        </Alert>
      )}
      {ruleWorkspace && activeRule && (
        <Container
          header={
            <Header
              variant="h2"
              actions={
                <Button
                  disabled={!activeRule.EventPattern}
                  onClick={() => {
                    setTopTabId("tester");
                    onNavigate?.(ruleUrl(activeRule, "tester"));
                  }}
                >
                  Test rule pattern
                </Button>
              }
            >
              Event routing
            </Header>
          }
        >
          <div className="resource-flow eventbridge-routing-flow">
            <section>
              <Header variant="h3">Event bus</Header>
              <p>{activeRule.EventBusName}</p>
            </section>
            <span aria-hidden="true">→</span>
            <section>
              <Header variant="h3">Rule</Header>
              <p>{activeRule.Name}</p>
              <p>{activeRule.State}</p>
              {activeRule.ScheduleExpression && (
                <p>{activeRule.ScheduleExpression}</p>
              )}
            </section>
            <span aria-hidden="true">→</span>
            <section>
              <Header variant="h3">Targets</Header>
              {activeRule.targets?.length ? (
                activeRule.targets.map((target) => (
                  <p key={target.Id}>
                    {target.Id}: {target.Arn}
                  </p>
                ))
              ) : (
                <p>No targets reported.</p>
              )}
            </section>
          </div>
        </Container>
      )}
      {/* Top-Level Tabs */}
      <Tabs
        activeTabId={topTabId}
        onChange={({ detail }) => {
          setTopTabId(detail.activeTabId);
          onNavigate?.(
            activeRule
              ? ruleUrl(activeRule, detail.activeTabId)
              : `eventbridge/${detail.activeTabId}`,
          );
        }}
        tabs={[
          {
            id: "tester",
            label: "Pattern tester",
            content:
              pinnedRule && !activeRule ? (
                <Alert type="info">
                  Select an available rule before testing its pattern.
                </Alert>
              ) : activeRule && !activeRule.EventPattern ? (
                <Alert type="info">
                  This rule has no event pattern to test. See its schedule in
                  configuration.
                </Alert>
              ) : (
                <EventPatternTester
                  key={`${activeRule?.EventBusName}/${activeRule?.Name}`}
                  pattern={activeRule?.EventPattern}
                />
              ),
          },
          {
            id: "rules",
            label: ruleWorkspace ? "Rule details" : `Rules (${rules.length})`,
            content: (
              <SpaceBetween size="l">
                {!ruleWorkspace && (
                  <Table
                    header={
                      <Header
                        variant="h2"
                        counter={`(${rules.length})`}
                        description="Event pattern matchers, scheduled rules, and routed targets."
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              disabled={!activeRule}
                              onClick={() =>
                                activeRule && handleToggleRuleState(activeRule)
                              }
                            >
                              {activeRule?.State === "ENABLED"
                                ? "Disable Rule"
                                : "Enable Rule"}
                            </Button>
                            <Button
                              disabled={!activeRule}
                              iconName="remove"
                              onClick={() => setDeleteRuleOpen(true)}
                            >
                              Delete Rule
                            </Button>
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => setCreateRuleOpen(true)}
                            >
                              Create Rule
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Event Rules
                      </Header>
                    }
                    columnDefinitions={[
                      {
                        id: "name",
                        header: "Rule name",
                        cell: (item) => (
                          <Button
                            variant="inline-link"
                            onClick={() => {
                              setSelectedRules([item]);
                              onNavigate?.(ruleUrl(item));
                            }}
                          >
                            <strong>{item.Name}</strong>
                          </Button>
                        ),
                        sortingField: "Name",
                        isRowHeader: true,
                      },
                      {
                        id: "bus",
                        header: "Event Bus",
                        cell: (item) => (
                          <Badge color="blue">{item.EventBusName}</Badge>
                        ),
                        width: 140,
                      },
                      {
                        id: "status",
                        header: "Status",
                        cell: (item) => (
                          <StatusIndicator
                            type={
                              item.State === "ENABLED" ? "success" : "stopped"
                            }
                          >
                            {item.State}
                          </StatusIndicator>
                        ),
                        width: 130,
                      },
                      {
                        id: "targets",
                        header: "Targets",
                        cell: (item) => (
                          <Badge color="green">
                            {item.targets?.length || item.target_count || 0}{" "}
                            targets
                          </Badge>
                        ),
                        width: 120,
                      },
                      {
                        id: "pattern",
                        header: "Event Pattern / Schedule",
                        cell: (item) => (
                          <div
                            style={{
                              maxWidth: "400px",
                              wordBreak: "break-all",
                              fontSize: "11px",
                            }}
                          >
                            <code>
                              {item.ScheduleExpression || item.EventPattern}
                            </code>
                          </div>
                        ),
                      },
                    ]}
                    items={filteredRules}
                    loading={loading}
                    loadingText="Loading EventBridge rules..."
                    selectionType="single"
                    selectedItems={selectedRules}
                    onSelectionChange={({ detail }) => {
                      setSelectedRules(detail.selectedItems);
                      onNavigate?.(
                        detail.selectedItems[0]
                          ? ruleUrl(detail.selectedItems[0])
                          : "eventbridge/rules",
                      );
                    }}
                    filter={
                      <ColumnLayout columns={2}>
                        <TextFilter
                          filteringText={filterText}
                          filteringPlaceholder="Filter rules by name or pattern..."
                          onChange={({ detail }) =>
                            setFilterText(detail.filteringText)
                          }
                        />
                        <Select
                          selectedOption={busFilter}
                          onChange={({ detail }) =>
                            setBusFilter(detail.selectedOption as any)
                          }
                          options={busOptions}
                        />
                      </ColumnLayout>
                    }
                    empty={
                      <Box
                        textAlign="center"
                        color="inherit"
                        padding={{ vertical: "l" }}
                      >
                        <SpaceBetween size="m">
                          <b>No EventBridge rules found</b>
                          <p>
                            Create a rule to match incoming events and route
                            them to targets.
                          </p>
                          <Button
                            variant="primary"
                            onClick={() => setCreateRuleOpen(true)}
                          >
                            Create Rule
                          </Button>
                        </SpaceBetween>
                      </Box>
                    }
                  />
                )}
                {/* Selected Rule Inspector */}
                {activeRule && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        description={`Bus: ${activeRule.EventBusName} | Status: ${activeRule.State}`}
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={() => setDeleteRuleOpen(true)}>
                              Delete rule
                            </Button>
                            <Button
                              onClick={() => handleToggleRuleState(activeRule)}
                            >
                              {activeRule.State === "ENABLED"
                                ? "Disable"
                                : "Enable"}
                            </Button>
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => setAddTargetOpen(true)}
                            >
                              Add Target
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Rule: {activeRule.Name}
                      </Header>
                    }
                  >
                    <Tabs
                      tabs={[
                        {
                          id: "targets",
                          label: `Targets (${activeRule.targets?.length || activeRule.target_count || 0})`,
                          content: (
                            <SpaceBetween size="m">
                              <EventBridgeTargets
                                targets={activeRule.targets || []}
                                onRemove={setRemoveTargetId}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          id: "pattern_details",
                          label: "Configuration",
                          content: (
                            <SpaceBetween size="m">
                              <KeyValuePairs
                                columns={3}
                                items={[
                                  {
                                    label: "Rule Name",
                                    value: activeRule.Name,
                                  },
                                  {
                                    label: "Event Bus",
                                    value: activeRule.EventBusName,
                                  },
                                  { label: "State", value: activeRule.State },
                                  {
                                    label: "Schedule Expression",
                                    value:
                                      activeRule.ScheduleExpression ||
                                      "Not configured",
                                  },
                                  {
                                    label: "Description",
                                    value: activeRule.Description || "N/A",
                                  },
                                ]}
                              />

                              <Header variant="h3">Event Pattern (JSON)</Header>
                              {activeRule.EventPattern ? (
                                <CodeSnippet
                                  language="json"
                                  code={activeRule.EventPattern}
                                />
                              ) : (
                                <Box>
                                  No event pattern reported. Scheduled rules do
                                  not require an event pattern.
                                </Box>
                              )}
                            </SpaceBetween>
                          ),
                        },
                      ]}
                    />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            id: "buses",
            label: `Event Buses (${buses.length})`,
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${buses.length})`}
                      description="Event buses receive events from a variety of sources and route them to rules."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            disabled={
                              !activeBus || activeBus.Name === "default"
                            }
                            iconName="remove"
                            onClick={() => setDeleteBusOpen(true)}
                          >
                            Delete Bus
                          </Button>
                          <Button
                            variant="primary"
                            iconName="add-plus"
                            onClick={() => setCreateBusOpen(true)}
                          >
                            Create Event Bus
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      Event Buses
                    </Header>
                  }
                  columnDefinitions={[
                    {
                      id: "name",
                      header: "Bus Name",
                      cell: (b) => (
                        <Button
                          variant="inline-link"
                          onClick={() => setSelectedBuses([b])}
                        >
                          <strong>{b.Name}</strong>
                        </Button>
                      ),
                      sortingField: "Name",
                      isRowHeader: true,
                    },
                    {
                      id: "rules",
                      header: "Rules",
                      cell: (b) => `${b.rule_count || 0} rules`,
                    },
                    {
                      id: "targets",
                      header: "Targets",
                      cell: (b) => `${b.target_count || 0} targets`,
                    },
                    {
                      id: "arn",
                      header: "Bus ARN",
                      cell: (b) => (
                        <code style={{ fontSize: "11px" }}>
                          {b.Arn || "Not reported"}
                        </code>
                      ),
                    },
                  ]}
                  items={buses}
                  selectionType="single"
                  selectedItems={selectedBuses}
                  onSelectionChange={({ detail }) =>
                    setSelectedBuses(detail.selectedItems)
                  }
                  empty={<Box textAlign="center">No event buses found.</Box>}
                />
              </SpaceBetween>
            ),
          },
          {
            id: "send",
            label: "Send events",
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description="Send custom event payloads into EventBridge to trigger target Lambdas, SQS queues, or Step Functions."
                  >
                    Send events
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <Grid
                    gridDefinition={[
                      { colspan: { default: 12, m: 4 } },
                      { colspan: { default: 12, m: 4 } },
                      { colspan: { default: 12, m: 4 } },
                    ]}
                  >
                    <FormField label="Target Event Bus">
                      <Input
                        value={sendBusName}
                        onChange={({ detail }) => setSendBusName(detail.value)}
                        placeholder="default"
                      />
                    </FormField>
                    <FormField
                      label="Event Source"
                      description="e.g. ecommerce.orders"
                    >
                      <Input
                        value={eventSource}
                        onChange={({ detail }) => setEventSource(detail.value)}
                        placeholder="ecommerce.orders"
                      />
                    </FormField>
                    <FormField
                      label="Detail Type"
                      description="e.g. OrderPlaced"
                    >
                      <Input
                        value={detailType}
                        onChange={({ detail }) => setDetailType(detail.value)}
                        placeholder="OrderPlaced"
                      />
                    </FormField>
                  </Grid>

                  <FormField
                    label="Event Detail (JSON Payload)"
                    description="Enter the JSON event payload."
                  >
                    <Textarea
                      rows={6}
                      value={eventDetail}
                      onChange={({ detail }) => setEventDetail(detail.value)}
                      placeholder='{\n  "orderId": "ord-123"\n}'
                    />
                  </FormField>

                  <Button
                    variant="primary"
                    iconName="caret-right-filled"
                    loading={sendingEvent}
                    disabled={
                      !sendBusName.trim() ||
                      !eventSource.trim() ||
                      !detailType.trim() ||
                      !eventDetail.trim()
                    }
                    onClick={handleSendEvent}
                  >
                    Publish Event to EventBridge
                  </Button>

                  {sendResult && (
                    <Container
                      header={<Header variant="h3">Publish result</Header>}
                    >
                      <KeyValuePairs
                        columns={2}
                        items={[
                          {
                            label: "Event bus",
                            value: sendResult.event_bus_name || "Not reported",
                          },
                          {
                            label: "Event ID",
                            value: sendResult.event_id || "Not reported",
                          },
                          {
                            label: "Failed entries",
                            value: String(
                              sendResult.failed_entry_count ?? "Not reported",
                            ),
                          },
                          {
                            label: "Error code",
                            value: sendResult.error_code || "None reported",
                          },
                        ]}
                      />
                      <CodeSnippet
                        language="json"
                        code={JSON.stringify(sendResult, null, 2)}
                      />
                    </Container>
                  )}
                </SpaceBetween>
              </Container>
            ),
          },
        ].filter(
          (tab) => !ruleWorkspace || ["rules", "tester"].includes(tab.id),
        )}
      />

      {/* Create Rule Modal */}
      <Modal
        visible={createRuleOpen}
        onDismiss={() => setCreateRuleOpen(false)}
        header="Create EventBridge Rule"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateRuleOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creatingRule}
                onClick={handleCreateRule}
              >
                Create Rule
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Rule Name"
            description="Unique alphanumeric identifier."
          >
            <Input
              value={ruleName}
              onChange={({ detail }) => setRuleName(detail.value)}
              placeholder="e.g. order-processor-rule"
            />
          </FormField>

          <ColumnLayout columns={2}>
            <FormField label="Event Bus" description="Defaults to 'default'.">
              <Input
                value={ruleBusName}
                onChange={({ detail }) => setRuleBusName(detail.value)}
                placeholder="default"
              />
            </FormField>
            <FormField label="Description">
              <Input
                value={ruleDescription}
                onChange={({ detail }) => setRuleDescription(detail.value)}
              />
            </FormField>
          </ColumnLayout>

          <FormField
            label="Event Pattern (JSON)"
            description="Specify the pattern syntax to match event sources and details."
          >
            <Textarea
              rows={6}
              value={eventPattern}
              onChange={({ detail }) => setEventPattern(detail.value)}
              placeholder='{\n  "source": ["ecommerce.orders"]\n}'
            />
          </FormField>

          <FormField
            label="Schedule Expression (optional)"
            description="e.g. rate(5 minutes) or cron(0 20 * * ? *)"
          >
            <Input
              value={scheduleExpr}
              onChange={({ detail }) => setScheduleExpr(detail.value)}
              placeholder="e.g. rate(1 hour)"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Bus Modal */}
      <Modal
        visible={createBusOpen}
        onDismiss={() => setCreateBusOpen(false)}
        header="Create Event Bus"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateBusOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creatingBus}
                onClick={handleCreateBus}
              >
                Create Bus
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Event Bus Name"
            description="Must be unique within your account."
          >
            <Input
              value={newBusName}
              onChange={({ detail }) => setNewBusName(detail.value)}
              placeholder="e.g. custom-partner-bus"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Add Target Modal */}
      <Modal
        visible={addTargetOpen}
        onDismiss={() => setAddTargetOpen(false)}
        header={`Add Target to Rule: ${activeRule?.Name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddTargetOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={addingTarget}
                disabled={!targetId.trim() || !targetArn.trim()}
                onClick={handleAddTarget}
              >
                Attach Target
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Target ID"
            description="Unique identifier for this target on the rule."
          >
            <Input
              value={targetId}
              onChange={({ detail }) => setTargetId(detail.value)}
              placeholder="e.g. order-sqs-target"
            />
          </FormField>

          <FormField
            label="Target ARN"
            description="ARN of the destination SQS queue, Lambda function, SNS topic, or Step Functions state machine."
          >
            <Input
              value={targetArn}
              onChange={({ detail }) => setTargetArn(detail.value)}
              placeholder="arn:aws:sqs:us-east-1:000000000000:my-queue"
            />
          </FormField>

          <FormField
            label="Execution role ARN (optional)"
            description="Use a role authorized to invoke this target when required by the target service."
          >
            <Input
              value={targetRoleArn}
              onChange={({ detail }) => setTargetRoleArn(detail.value)}
            />
          </FormField>
          <FormField
            label="Constant Input Payload (optional)"
            description="Override the matched event with valid JSON. Leave empty to pass the matched event."
          >
            <Textarea
              rows={3}
              value={targetInput}
              onChange={({ detail }) => setTargetInput(detail.value)}
              placeholder='{"triggered": true}'
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={removeTargetId !== null}
        onDismiss={() => setRemoveTargetId(null)}
        header="Remove target"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRemoveTargetId(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                onClick={() =>
                  removeTargetId && handleRemoveTarget(removeTargetId)
                }
              >
                Remove target
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        Remove target <strong>{removeTargetId}</strong> from rule{" "}
        <strong>{activeRule?.Name}</strong>? Future matching events will no
        longer be routed to this target.
      </Modal>
      {/* Delete Rule Modal */}
      <Modal
        visible={deleteRuleOpen}
        onDismiss={() => setDeleteRuleOpen(false)}
        header={`Delete Rule: ${activeRule?.Name}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteRuleOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDeleteRule}>
                Delete Rule
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="error" header="Permanent Deletion">
          Are you sure you want to delete rule{" "}
          <strong>{activeRule?.Name}</strong> from bus{" "}
          <strong>{activeRule?.EventBusName}</strong>?
        </Alert>
      </Modal>

      {/* Delete Bus Modal */}
      <Modal
        visible={deleteBusOpen}
        onDismiss={() => setDeleteBusOpen(false)}
        header={`Delete Event Bus: ${activeBus?.Name}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteBusOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDeleteBus}>
                Delete Bus
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="error" header="Permanent Deletion">
          Are you sure you want to delete event bus{" "}
          <strong>{activeBus?.Name}</strong>?
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};
