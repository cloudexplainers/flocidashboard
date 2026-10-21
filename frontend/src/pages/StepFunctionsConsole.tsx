import {
  StepExecutionView,
  executionDate,
} from "../components/StepExecutionView";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import { useSession } from "../components/SessionContext";
import { WorkflowGraph } from "../components/WorkflowGraph";
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
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import {
  stepFunctionsRequest,
  stopStepFunctionExecution,
  updateStepFunctionDefinition,
  fetchStepFunctionActivities,
  createStepFunctionActivity,
  deleteStepFunctionActivity,
  sendStepFunctionTaskSuccess,
  sendStepFunctionTaskFailure,
} from "../api/client";

const SAMPLE_ASL = JSON.stringify(
  {
    Comment: "A simple Floci Step Functions state machine workflow",
    StartAt: "ValidateOrder",
    States: {
      ValidateOrder: {
        Type: "Pass",
        Result: { status: "VALID", processedBy: "Floci Local" },
        Next: "ProcessPayment",
      },
      ProcessPayment: {
        Type: "Pass",
        Result: { transactionId: "txn-89104", amount: 99.0 },
        End: true,
      },
    },
  },
  null,
  2,
);

interface StepFunctionsConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const StepFunctionsConsole: React.FC<StepFunctionsConsoleProps> = (
  props,
) => {
  const { identity, details, loading } = useSession();
  if (!identity && loading)
    return (
      <SpaceBetween size="m">
        <Header variant="h1">AWS Step Functions</Header>
        <StatusIndicator type="loading">
          Loading session context
        </StatusIndicator>
      </SpaceBetween>
    );
  return (
    <StepWorkspace
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
const StepWorkspace: React.FC<StepFunctionsConsoleProps> = ({
  activeTab,
  onTabChange,
  resourcePath,
  onNavigate,
}) => {
  const mounted = useRef(true);
  const loadGeneration = useRef(0);
  const mutationLock = useRef(false);
  const [loadError, setLoadError] = useState("");
  const [modalError, setModalError] = useState("");
  const [roleArn, setRoleArn] = useState("");
  const [stopTarget, setStopTarget] = useState("");
  const [stopping, setStopping] = useState(false);
  const validateDefinition = (text: string) => {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error("Definition must be valid JSON.");
    }
    if (
      !parsed ||
      typeof parsed !== "object" ||
      !parsed.States ||
      !parsed.States[parsed.StartAt]
    )
      throw new Error(
        "Definition must contain States and a valid StartAt state.",
      );
    return JSON.stringify(parsed);
  };
  const route = new URLSearchParams((resourcePath || "").split("?")[1] || "");
  const requestedMachine = route.get("machine");
  const requestedExecution = route.get("execution");
  const requestedPanel = route.get("panel") || "asl";
  const navigateMachine = (
    machine: any,
    tab = selectedTabId,
    panel = requestedPanel,
    execution?: string,
  ) => {
    const query = new URLSearchParams({
      machine:
        machine?.arn ||
        machine?.stateMachineArn ||
        machine?.Arn ||
        machine?.name ||
        machine?.Name ||
        "",
      panel,
    });
    if (execution) query.set("execution", execution);
    onNavigate?.(`stepfunctions/${tab}?${query}`);
  };
  const [data, setData] = useState<any>({ state_machines: [], executions: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState("");
  const [selectedMachines, setSelectedMachines] = useState<any[]>([]);
  const [actionMessage, setActionMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || "machines");

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [machineName, setMachineName] = useState("");
  const [machineType, setMachineType] = useState({
    label: "STANDARD",
    value: "STANDARD",
  });
  const [definition, setDefinition] = useState(SAMPLE_ASL);
  const [creating, setCreating] = useState(false);

  const [editDefOpen, setEditDefOpen] = useState(false);
  const [editDefDoc, setEditDefDoc] = useState("");
  const [savingDef, setSavingDef] = useState(false);

  const [startExecModalOpen, setStartExecModalOpen] = useState(false);
  const [execInput, setExecInput] = useState(
    '{\n  "orderId": "order-12345"\n}',
  );
  const [execName, setExecName] = useState("");
  const [executing, setExecuting] = useState(false);

  const [selectedExecArn, setSelectedExecArn] = useState("");

  const [activities, setActivities] = useState<any[]>([]);
  const [createActivityOpen, setCreateActivityOpen] = useState(false);
  const [newActivityName, setNewActivityName] = useState("");
  const [creatingActivity, setCreatingActivity] = useState(false);

  const [taskToken, setTaskToken] = useState("");
  const [taskOutput, setTaskOutput] = useState('{\n  "status": "APPROVED"\n}');
  const [sendingTask, setSendingTask] = useState(false);

  const loadData = async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setLoadError("");
    try {
      const [invRes, actRes]: any[] = await Promise.all([
        stepFunctionsRequest(),
        fetchStepFunctionActivities(),
      ]);
      if (!mounted.current || generation !== loadGeneration.current) return;
      setData(invRes || { state_machines: [], executions: [] });
      setActivities(Array.isArray(actRes) ? actRes : []);
    } catch (err) {
      if (mounted.current && generation === loadGeneration.current)
        setLoadError(
          err instanceof Error
            ? err.message
            : "Unable to load Step Functions resources",
        );
    } finally {
      if (mounted.current && generation === loadGeneration.current)
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

  const activeMachine =
    selectedMachines.length > 0 ? selectedMachines[0] : null;
  const activeMachineArn = activeMachine
    ? activeMachine.stateMachineArn ||
      activeMachine.arn ||
      activeMachine.Arn ||
      ""
    : "";

  useEffect(() => {
    if (requestedMachine)
      setSelectedMachines(
        (data.state_machines || []).filter((machine: any) =>
          [
            machine.arn,
            machine.stateMachineArn,
            machine.Arn,
            machine.name,
            machine.Name,
          ].includes(requestedMachine),
        ),
      );
  }, [requestedMachine, data]);
  const activeExecutions = Array.isArray(activeMachine?.executions)
    ? activeMachine.executions
    : (data.executions || []).filter(
        (execution: any) =>
          (execution.state_machine_arn || execution.stateMachineArn) ===
          activeMachineArn,
      );
  const allExecutions = (data.state_machines || []).flatMap((machine: any) =>
    Array.isArray(machine.executions) ? machine.executions : [],
  );

  useEffect(() => {
    if (activeMachine) {
      const def = activeMachine.definition || activeMachine.Definition || "";
      setEditDefDoc(
        typeof def === "object" ? JSON.stringify(def, null, 2) : String(def),
      );
    }
  }, [activeMachineArn, activeMachine?.definition, activeMachine?.Definition]);

  const handleCreateMachine = async () => {
    if (!machineName.trim() || !roleArn.trim() || mutationLock.current) return;
    mutationLock.current = true;
    setModalError("");
    setCreating(true);
    setActionMessage(null);
    try {
      await stepFunctionsRequest("state-machines/", "POST", {
        name: machineName.trim(),
        type: machineType.value,
        definition: validateDefinition(definition),
        role_arn: roleArn.trim(),
      });
      if (!mounted.current) return;
      setActionMessage({
        type: "success",
        text: `State machine "${machineName.trim()}" created successfully.`,
      });
      setCreateModalOpen(false);
      setMachineName("");
      await loadData();
    } catch (err: any) {
      setModalError(err.message || "Failed to create state machine");
    } finally {
      mutationLock.current = false;
      if (mounted.current) setCreating(false);
    }
  };

  const handleUpdateDefinition = async () => {
    if (!activeMachineArn || !editDefDoc.trim()) return;
    setSavingDef(true);
    try {
      await updateStepFunctionDefinition(
        activeMachineArn,
        validateDefinition(editDefDoc),
      );
      setActionMessage({
        type: "success",
        text: "State machine definition updated.",
      });
      setEditDefOpen(false);
      await loadData();
    } catch (err: any) {
      setModalError(err.message || "Failed to update definition");
    } finally {
      setSavingDef(false);
    }
  };

  const handleStartExecution = async () => {
    if (!activeMachine) return;
    setExecuting(true);
    try {
      JSON.parse(execInput);
      const result = await stepFunctionsRequest("executions/start/", "POST", {
        state_machine_arn: activeMachineArn,
        name: execName.trim() || undefined,
        input: execInput,
      });
      if (!mounted.current) return;
      if (result.execution_arn)
        navigateMachine(
          activeMachine,
          "machines",
          "history",
          result.execution_arn,
        );
      setActionMessage({
        type: "success",
        text: `Execution started for "${activeMachine.name || activeMachine.Name}".`,
      });
      setStartExecModalOpen(false);
      setExecName("");
      await loadData();
    } catch (err: any) {
      setModalError(err.message || "Failed to start execution");
    } finally {
      setExecuting(false);
    }
  };

  const handleStopExecution = async (execArn: string) => {
    try {
      setStopping(true);
      setModalError("");
      await stopStepFunctionExecution(execArn);
      setStopTarget("");
      setActionMessage({ type: "success", text: "Execution stopped." });
      await loadData();
    } catch (err: any) {
      setModalError(err.message || "Failed to stop execution");
    } finally {
      setStopping(false);
    }
  };

  const handleViewExecutionHistory = (arn: string) => {
    if (onNavigate) navigateMachine(activeMachine, "machines", "history", arn);
    else setSelectedExecArn(arn);
  };
  const handleCreateActivity = async () => {
    if (!newActivityName.trim()) return;
    setCreatingActivity(true);
    try {
      await createStepFunctionActivity(newActivityName.trim());
      setActionMessage({
        type: "success",
        text: `Activity "${newActivityName.trim()}" created.`,
      });
      setCreateActivityOpen(false);
      setNewActivityName("");
      await loadData();
    } catch (err: any) {
      setActionMessage({
        type: "error",
        text: err.message || "Failed to create activity",
      });
    } finally {
      setCreatingActivity(false);
    }
  };

  const handleDeleteActivity = async (arn: string) => {
    try {
      await deleteStepFunctionActivity(arn);
      setActionMessage({ type: "success", text: "Activity deleted." });
      await loadData();
    } catch (err: any) {
      setActionMessage({
        type: "error",
        text: err.message || "Failed to delete activity",
      });
    }
  };

  const handleSendTaskCallback = async (success: boolean) => {
    if (!taskToken.trim()) return;
    setSendingTask(true);
    try {
      if (success) {
        await sendStepFunctionTaskSuccess(taskToken.trim(), taskOutput);
        setActionMessage({
          type: "success",
          text: "Sent Task Success callback.",
        });
      } else {
        await sendStepFunctionTaskFailure(
          taskToken.trim(),
          "TaskFailed",
          "Failure reported from Console",
        );
        setActionMessage({
          type: "success",
          text: "Sent Task Failure callback.",
        });
      }
      setTaskToken("");
    } catch (err: any) {
      setActionMessage({
        type: "error",
        text: err.message || "Failed to send task response",
      });
    } finally {
      setSendingTask(false);
    }
  };

  const machinesList = (data.state_machines || []).map((m: any) => ({
    ...m,
    Name: m.name || m.Name,
    Arn: m.stateMachineArn || m.arn || m.Arn || "",
    Type: m.type || m.Type || "Not reported",
    CreationDate: m.creationDate || m.CreationDate || m.created,
    Definition: m.definition || m.Definition || "",
  }));

  const filteredMachines = machinesList.filter((m: any) => {
    const text = `${m.Name} ${m.Type} ${m.Arn}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  const executionArn = requestedExecution || selectedExecArn;
  if (executionArn && activeMachine)
    return (
      <StepExecutionView
        key={executionArn}
        arn={executionArn}
        machineArn={activeMachineArn}
        definition={
          typeof activeMachine.definition === "object"
            ? JSON.stringify(activeMachine.definition)
            : activeMachine.definition || activeMachine.Definition || ""
        }
        onBack={() => {
          setSelectedExecArn("");
          navigateMachine(activeMachine, "machines", "history");
        }}
        onChanged={loadData}
      />
    );

  return (
    <SpaceBetween size="l">
      {loadError && (
        <Alert type="error" header="Unable to load Step Functions resources">
          {loadError}
        </Alert>
      )}
      <Container
        header={
          <Header
            variant="h1"
            description="Coordinate distributed services and serverless components using visual workflow state machines."
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
                  Create State Machine
                </Button>
              </SpaceBetween>
            }
          >
            AWS Step Functions
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

        <div className="console-summary">
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">State Machines</Box>
            <Box variant="h1" color="text-status-info">
              {machinesList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Activities Defined</Box>
            <Box variant="h1" color="text-status-info">
              {activities.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Workflow Engine</Box>
            <Box variant="h2" color="text-status-info">
              <span>
                {
                  (allExecutions.length
                    ? allExecutions
                    : data.executions || []
                  ).filter((execution: any) => execution.status === "RUNNING")
                    .length
                }{" "}
                running executions
              </span>
            </Box>
          </Box>
        </div>
      </Container>

      {requestedMachine && !loading && !activeMachine && (
        <Alert type="warning">
          Requested state machine was not found. Select an available workflow.
        </Alert>
      )}
      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          if (activeMachine) navigateMachine(activeMachine, detail.activeTabId);
          else onTabChange?.(detail.activeTabId);
        }}
        tabs={[
          {
            label: `State Machines (${machinesList.length})`,
            id: "machines",
            content: (
              <SpaceBetween size="l">
                {!requestedMachine && (
                  <Container
                    header={<Header variant="h2">State Machines</Header>}
                  >
                    <SpaceBetween size="m">
                      <TextFilter
                        filteringText={filterText}
                        filteringPlaceholder="Find state machine by name..."
                        onChange={({ detail }) =>
                          setFilterText(detail.filteringText)
                        }
                      />

                      <Table
                        columnDefinitions={[
                          {
                            id: "name",
                            header: "Name",
                            cell: (item: any) => (
                              <Button
                                variant="inline-link"
                                onClick={() => {
                                  setSelectedMachines([item]);
                                  navigateMachine(item);
                                }}
                              >
                                <strong>{item.Name}</strong>
                              </Button>
                            ),
                          },
                          {
                            id: "type",
                            header: "Type",
                            cell: (item: any) => (
                              <Badge color="blue">{item.Type}</Badge>
                            ),
                            width: 130,
                          },
                          {
                            id: "arn",
                            header: "ARN",
                            cell: (item: any) => <code>{item.Arn}</code>,
                          },
                        ]}
                        items={filteredMachines}
                        selectionType="single"
                        selectedItems={selectedMachines}
                        onSelectionChange={({ detail }) => {
                          setSelectedMachines(detail.selectedItems);
                          if (detail.selectedItems[0])
                            navigateMachine(detail.selectedItems[0]);
                        }}
                        empty={
                          <Box textAlign="center">No state machines found.</Box>
                        }
                      />
                    </SpaceBetween>
                  </Container>
                )}

                {activeMachine && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              onClick={() => {
                                setModalError("");
                                setEditDefOpen(true);
                              }}
                            >
                              Edit Definition
                            </Button>
                            <Button
                              variant="primary"
                              iconName="caret-right-filled"
                              onClick={() => {
                                setModalError("");
                                setStartExecModalOpen(true);
                              }}
                            >
                              Start Execution
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Machine: {activeMachine.Name || activeMachine.name}
                      </Header>
                    }
                  >
                    <SpaceBetween size="l">
                      <Button
                        variant="link"
                        iconName="arrow-left"
                        onClick={() => {
                          setSelectedMachines([]);
                          onNavigate?.("stepfunctions/machines");
                        }}
                      >
                        State machines
                      </Button>
                      <KeyValuePairs
                        columns={2}
                        minColumnWidth={280}
                        items={[
                          {
                            label: "State machine ARN",
                            value: activeMachineArn,
                          },
                          {
                            label: "Workflow type",
                            value:
                              activeMachine.type ||
                              activeMachine.Type ||
                              "Not reported",
                          },
                          {
                            label: "Execution role",
                            value:
                              activeMachine.role_arn ||
                              activeMachine.roleArn ||
                              "Not reported",
                          },
                          {
                            label: "Created",
                            value: executionDate(
                              activeMachine.created ||
                                activeMachine.creationDate ||
                                activeMachine.CreationDate,
                            ),
                          },
                          {
                            label: "Revision ID",
                            value:
                              activeMachine.revision_id ||
                              activeMachine.revisionId ||
                              "Not reported",
                          },
                          {
                            label: "Status",
                            value: activeMachine.status || "Not reported",
                          },
                        ]}
                      />
                      <Tabs
                        activeTabId={requestedPanel}
                        onChange={({ detail }) =>
                          navigateMachine(
                            activeMachine,
                            "machines",
                            detail.activeTabId,
                          )
                        }
                        tabs={[
                          {
                            label: "ASL Definition",
                            id: "asl",
                            content: (
                              <Textarea rows={12} value={editDefDoc} readOnly />
                            ),
                          },
                          {
                            label: "Visual State Map",
                            id: "visual",
                            content: (
                              <Container
                                header={
                                  <Header variant="h3">
                                    Workflow transitions
                                  </Header>
                                }
                              >
                                <WorkflowGraph
                                  key={activeMachineArn}
                                  definition={editDefDoc}
                                />
                              </Container>
                            ),
                          },
                          {
                            label: `Executions History (${activeExecutions.length})`,
                            id: "history",
                            content: (
                              <Table
                                columnDefinitions={[
                                  {
                                    id: "name",
                                    header: "Execution Name",
                                    cell: (i: any) => (
                                      <strong>
                                        {i.name ||
                                          i.executionArn?.split(":").pop() ||
                                          "Execution"}
                                      </strong>
                                    ),
                                  },
                                  {
                                    id: "status",
                                    header: "Status",
                                    cell: (i: any) => {
                                      const st = i.status || "Not reported";
                                      return (
                                        <StatusIndicator
                                          type={
                                            st === "SUCCEEDED"
                                              ? "success"
                                              : st === "RUNNING"
                                                ? "in-progress"
                                                : st === "Not reported"
                                                  ? "info"
                                                  : "error"
                                          }
                                        >
                                          {st}
                                        </StatusIndicator>
                                      );
                                    },
                                    width: 140,
                                  },
                                  {
                                    id: "time",
                                    header: "Start Date",
                                    cell: (i: any) =>
                                      executionDate(i.startDate || i.started),
                                    width: 160,
                                  },
                                  {
                                    id: "act",
                                    header: "Actions",
                                    cell: (i: any) => (
                                      <SpaceBetween
                                        direction="horizontal"
                                        size="xs"
                                      >
                                        <Button
                                          onClick={() =>
                                            handleViewExecutionHistory(
                                              i.executionArn || i.arn,
                                            )
                                          }
                                        >
                                          History
                                        </Button>
                                        <Button
                                          iconName="remove"
                                          disabled={i.status !== "RUNNING"}
                                          onClick={() => {
                                            setModalError("");
                                            setStopTarget(
                                              i.executionArn || i.arn,
                                            );
                                          }}
                                        >
                                          Stop
                                        </Button>
                                      </SpaceBetween>
                                    ),
                                    width: 180,
                                  },
                                ]}
                                items={activeExecutions}
                                empty={
                                  <Box textAlign="center">
                                    No executions recorded for this state
                                    machine.
                                  </Box>
                                }
                              />
                            ),
                          },
                        ]}
                      />
                    </SpaceBetween>
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: `Activities (${activities.length})`,
            id: "activities",
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button
                          variant="primary"
                          iconName="add-plus"
                          onClick={() => setCreateActivityOpen(true)}
                        >
                          Create Activity
                        </Button>
                      }
                    >
                      Step Functions Activities (Worker Pollers)
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      {
                        id: "name",
                        header: "Activity Name",
                        cell: (a: any) => <strong>{a.name || a.Name}</strong>,
                      },
                      {
                        id: "arn",
                        header: "Activity ARN",
                        cell: (a: any) => (
                          <code>{a.activityArn || a.ActivityArn}</code>
                        ),
                      },
                      {
                        id: "date",
                        header: "Creation Date",
                        cell: (a: any) =>
                          executionDate(a.creationDate || a.CreationDate),
                        width: 160,
                      },
                      {
                        id: "act",
                        header: "Action",
                        cell: (a: any) => (
                          <Button
                            iconName="remove"
                            onClick={() =>
                              handleDeleteActivity(
                                a.activityArn || a.ActivityArn,
                              )
                            }
                          >
                            Delete
                          </Button>
                        ),
                        width: 110,
                      },
                    ]}
                    items={activities}
                    empty={<Box textAlign="center">No activities defined.</Box>}
                  />
                </Container>

                <Container
                  header={
                    <Header variant="h2">
                      Send Task Token Callback (.waitForTaskToken)
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <FormField
                      label="Task Token"
                      description="Unique opaque string passed to worker or external system."
                    >
                      <Input
                        value={taskToken}
                        onChange={({ detail }) => setTaskToken(detail.value)}
                        placeholder="AAAAKgAAAAIAAAAAAAAAA..."
                      />
                    </FormField>
                    <FormField label="Task Output (JSON)">
                      <Textarea
                        rows={4}
                        value={taskOutput}
                        onChange={({ detail }) => setTaskOutput(detail.value)}
                      />
                    </FormField>
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button
                        variant="primary"
                        loading={sendingTask}
                        onClick={() => handleSendTaskCallback(true)}
                      >
                        Send Task Success
                      </Button>
                      <Button
                        loading={sendingTask}
                        onClick={() => handleSendTaskCallback(false)}
                      >
                        Send Task Failure
                      </Button>
                    </SpaceBetween>
                  </SpaceBetween>
                </Container>
              </SpaceBetween>
            ),
          },
        ]}
      />

      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create State Machine"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creating}
                disabled={!machineName.trim() || !roleArn.trim()}
                onClick={handleCreateMachine}
              >
                Create State Machine
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField
            label="State Machine Name"
            description="Unique workflow name."
          >
            <Input
              value={machineName}
              onChange={({ detail }) => setMachineName(detail.value)}
              placeholder="OrderProcessingWorkflow"
            />
          </FormField>

          <FormField
            label="Execution role ARN"
            description="An existing IAM role used by this workflow to call AWS services."
          >
            <Input
              value={roleArn}
              onChange={({ detail }) => setRoleArn(detail.value)}
              placeholder="arn:aws:iam::123456789012:role/WorkflowExecutionRole"
            />
          </FormField>
          <FormField label="Workflow Type">
            <Select
              selectedOption={machineType}
              onChange={({ detail }) =>
                setMachineType(detail.selectedOption as any)
              }
              options={[
                {
                  label: "STANDARD (Exactly-once execution model)",
                  value: "STANDARD",
                },
                {
                  label: "EXPRESS (High-throughput, at-least-once model)",
                  value: "EXPRESS",
                },
              ]}
            />
          </FormField>

          <FormField label="Amazon States Language (ASL) Definition (JSON)">
            <Textarea
              rows={10}
              value={definition}
              onChange={({ detail }) => setDefinition(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={editDefOpen}
        onDismiss={() => setEditDefOpen(false)}
        header={`Edit ASL Definition: ${activeMachine?.Name || activeMachine?.name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setEditDefOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={savingDef}
                onClick={handleUpdateDefinition}
              >
                Save Definition
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Amazon States Language (ASL) Definition">
            <Textarea
              rows={14}
              value={editDefDoc}
              onChange={({ detail }) => setEditDefDoc(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={startExecModalOpen}
        onDismiss={() => setStartExecModalOpen(false)}
        header={`Start Execution: ${activeMachine?.Name || activeMachine?.name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setStartExecModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={executing}
                onClick={handleStartExecution}
              >
                Start Execution
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <FormField label="Execution Name (Optional)">
            <Input
              value={execName}
              onChange={({ detail }) => setExecName(detail.value)}
              placeholder="exec-run-101"
            />
          </FormField>
          <FormField label="Execution Input (JSON)">
            <Textarea
              rows={6}
              value={execInput}
              onChange={({ detail }) => setExecInput(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={!!stopTarget}
        onDismiss={() => {
          if (!stopping) setStopTarget("");
        }}
        header="Stop execution"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button disabled={stopping} onClick={() => setStopTarget("")}>
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={stopping}
                onClick={() => handleStopExecution(stopTarget)}
              >
                Confirm stop
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {modalError && <Alert type="error">{modalError}</Alert>}
          <Alert type="warning">
            Stop execution {stopTarget}? Completed work remains in execution
            history.
          </Alert>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createActivityOpen}
        onDismiss={() => setCreateActivityOpen(false)}
        header="Create Step Functions Activity"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                onClick={() => setCreateActivityOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={creatingActivity}
                onClick={handleCreateActivity}
              >
                Create Activity
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Activity Name">
          <Input
            value={newActivityName}
            onChange={({ detail }) => setNewActivityName(detail.value)}
            placeholder="ProcessHumanApprovalTask"
          />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
