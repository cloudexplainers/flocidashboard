import { WorkflowGraph } from '../components/WorkflowGraph';
import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import {
  fetchInventory,
  executeServiceAction,
  fetchStepFunctionExecutionHistory,
  stopStepFunctionExecution,
  updateStepFunctionDefinition,
  fetchStepFunctionActivities,
  createStepFunctionActivity,
  deleteStepFunctionActivity,
  sendStepFunctionTaskSuccess,
  sendStepFunctionTaskFailure,
} from '../api/client';

const SAMPLE_ASL = JSON.stringify(
  {
    Comment: 'A simple Floci Step Functions state machine workflow',
    StartAt: 'ValidateOrder',
    States: {
      ValidateOrder: {
        Type: 'Pass',
        Result: { status: 'VALID', processedBy: 'Floci Local' },
        Next: 'ProcessPayment',
      },
      ProcessPayment: {
        Type: 'Pass',
        Result: { transactionId: 'txn-89104', amount: 99.0 },
        End: true,
      },
    },
  },
  null,
  2
);

interface StepFunctionsConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const StepFunctionsConsole: React.FC<StepFunctionsConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({ state_machines: [], executions: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [selectedMachines, setSelectedMachines] = useState<any[]>([]);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'machines');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [machineName, setMachineName] = useState('');
  const [machineType, setMachineType] = useState({ label: 'STANDARD', value: 'STANDARD' });
  const [definition, setDefinition] = useState(SAMPLE_ASL);
  const [creating, setCreating] = useState(false);

  const [editDefOpen, setEditDefOpen] = useState(false);
  const [editDefDoc, setEditDefDoc] = useState('');
  const [savingDef, setSavingDef] = useState(false);

  const [startExecModalOpen, setStartExecModalOpen] = useState(false);
  const [execInput, setExecInput] = useState('{\n  "orderId": "order-12345"\n}');
  const [execName, setExecName] = useState('');
  const [executing, setExecuting] = useState(false);

  const [selectedExecHistory, setSelectedExecHistory] = useState<any[]>([]);
  const [historyModalOpen, setHistoryModalOpen] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [selectedExecArn, setSelectedExecArn] = useState('');

  const [activities, setActivities] = useState<any[]>([]);
  const [createActivityOpen, setCreateActivityOpen] = useState(false);
  const [newActivityName, setNewActivityName] = useState('');
  const [creatingActivity, setCreatingActivity] = useState(false);

  const [taskToken, setTaskToken] = useState('');
  const [taskOutput, setTaskOutput] = useState('{\n  "status": "APPROVED"\n}');
  const [sendingTask, setSendingTask] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const [invRes, actRes]: any[] = await Promise.all([
        fetchInventory('stepfunctions'),
        fetchStepFunctionActivities(),
      ]);
      setData(invRes || { state_machines: [], executions: [] });
      setActivities(actRes?.activities || actRes?.Activities || []);
      if (invRes.state_machines?.length > 0 && selectedMachines.length === 0) {
        setSelectedMachines([invRes.state_machines[0]]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeMachine = selectedMachines.length > 0 ? selectedMachines[0] : null;
  const activeMachineArn = activeMachine ? activeMachine.stateMachineArn || activeMachine.arn || `arn:aws:states:us-east-1:000000000000:stateMachine:${activeMachine.name || activeMachine.Name}` : '';

  useEffect(() => {
    if (activeMachine) {
      const def = activeMachine.definition || activeMachine.Definition || '';
      setEditDefDoc(typeof def === 'object' ? JSON.stringify(def, null, 2) : String(def));
    }
  }, [activeMachine?.Name || activeMachine?.name]);

  const handleCreateMachine = async () => {
    if (!machineName.trim()) return;
    setCreating(true);
    setActionMessage(null);
    try {
      let parsed = {};
      try { parsed = JSON.parse(definition); } catch (e) {}

      await executeServiceAction('stepfunctions', 'create_state_machine', {
        name: machineName.trim(),
        type: machineType.value,
        definition: typeof parsed === 'string' ? parsed : JSON.stringify(parsed),
        role_arn: 'arn:aws:iam::000000000000:role/StepFunctionsExecutionRole',
      });
      setActionMessage({ type: 'success', text: `State machine "${machineName.trim()}" created successfully.` });
      setCreateModalOpen(false);
      setMachineName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create state machine' });
    } finally {
      setCreating(false);
    }
  };

  const handleUpdateDefinition = async () => {
    if (!activeMachineArn || !editDefDoc.trim()) return;
    setSavingDef(true);
    try {
      await updateStepFunctionDefinition(activeMachineArn, editDefDoc.trim());
      setActionMessage({ type: 'success', text: 'State machine definition updated.' });
      setEditDefOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update definition' });
    } finally {
      setSavingDef(false);
    }
  };

  const handleStartExecution = async () => {
    if (!activeMachine) return;
    setExecuting(true);
    try {
      await executeServiceAction('stepfunctions', 'start_execution', {
        state_machine_arn: activeMachineArn,
        name: execName.trim() || undefined,
        input: execInput,
      });
      setActionMessage({ type: 'success', text: `Execution started for "${activeMachine.name || activeMachine.Name}".` });
      setStartExecModalOpen(false);
      setExecName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to start execution' });
    } finally {
      setExecuting(false);
    }
  };

  const handleStopExecution = async (execArn: string) => {
    try {
      await stopStepFunctionExecution(execArn, 'Stopped via Floci Step Functions Console');
      setActionMessage({ type: 'success', text: 'Execution stopped.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to stop execution' });
    }
  };

  const handleViewExecutionHistory = async (execArn: string) => {
    setSelectedExecArn(execArn);
    setSelectedExecHistory([]);
    setLoadingHistory(true);
    setHistoryModalOpen(true);
    try {
      const res: any = await fetchStepFunctionExecutionHistory(execArn);
      setSelectedExecHistory(res.events || res.Events || []);
    } catch (err) {
      console.error(err);
      setSelectedExecHistory([]);
    } finally {
      setLoadingHistory(false);
    }
  };

  const handleCreateActivity = async () => {
    if (!newActivityName.trim()) return;
    setCreatingActivity(true);
    try {
      await createStepFunctionActivity(newActivityName.trim());
      setActionMessage({ type: 'success', text: `Activity "${newActivityName.trim()}" created.` });
      setCreateActivityOpen(false);
      setNewActivityName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create activity' });
    } finally {
      setCreatingActivity(false);
    }
  };

  const handleDeleteActivity = async (arn: string) => {
    try {
      await deleteStepFunctionActivity(arn);
      setActionMessage({ type: 'success', text: 'Activity deleted.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete activity' });
    }
  };

  const handleSendTaskCallback = async (success: boolean) => {
    if (!taskToken.trim()) return;
    setSendingTask(true);
    try {
      if (success) {
        await sendStepFunctionTaskSuccess(taskToken.trim(), taskOutput);
        setActionMessage({ type: 'success', text: 'Sent Task Success callback.' });
      } else {
        await sendStepFunctionTaskFailure(taskToken.trim(), 'TaskFailed', 'Failure reported from Console');
        setActionMessage({ type: 'success', text: 'Sent Task Failure callback.' });
      }
      setTaskToken('');
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to send task response' });
    } finally {
      setSendingTask(false);
    }
  };

  const machinesList = (data.state_machines || []).map((m: any) => ({
    ...m,
    Name: m.name || m.Name,
    Arn: m.stateMachineArn || m.arn || `arn:aws:states:us-east-1:000000000000:stateMachine:${m.name || m.Name}`,
    Type: m.type || m.Type || 'STANDARD',
    CreationDate: m.creationDate || m.CreationDate,
    Definition: m.definition || m.Definition || '',
  }));

  const filteredMachines = machinesList.filter((m: any) => {
    const text = `${m.Name} ${m.Type} ${m.Arn}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });


  return (
    <SpaceBetween size="l">
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
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateModalOpen(true)}>
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
          <Box margin={{ bottom: 'm' }}>
            <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
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
              <span>{(data.executions || []).filter((execution: any) => execution.status === 'RUNNING').length} running executions</span>
            </Box>
          </Box>
        </div>
      </Container>

      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          onTabChange?.(detail.activeTabId);
        }}
        tabs={[
          {
            label: `State Machines (${machinesList.length})`,
            id: 'machines',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">State Machines</Header>}>
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={filterText}
                      filteringPlaceholder="Find state machine by name..."
                      onChange={({ detail }) => setFilterText(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Name',
                          cell: (item: any) => (
                            <Button variant="inline-link" onClick={() => setSelectedMachines([item])}>
                              <strong>{item.Name}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'type',
                          header: 'Type',
                          cell: (item: any) => <Badge color="blue">{item.Type}</Badge>,
                          width: 130,
                        },
                        {
                          id: 'arn',
                          header: 'ARN',
                          cell: (item: any) => <code>{item.Arn}</code>,
                        },
                      ]}
                      items={filteredMachines}
                      selectionType="single"
                      selectedItems={selectedMachines}
                      onSelectionChange={({ detail }) => setSelectedMachines(detail.selectedItems)}
                      empty={<Box textAlign="center">No state machines found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activeMachine && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button onClick={() => setEditDefOpen(true)}>Edit Definition</Button>
                            <Button variant="primary" iconName="caret-right-filled" onClick={() => setStartExecModalOpen(true)}>
                              Start Execution
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Machine: {activeMachine.Name || activeMachine.name}
                      </Header>
                    }
                  >
                    <Tabs
                      tabs={[
                        {
                          label: 'ASL Definition',
                          id: 'asl',
                          content: (
                            <Textarea
                              rows={12}
                              value={editDefDoc}
                              readOnly
                            />
                          ),
                        },
                        {
                          label: 'Visual State Map',
                          id: 'visual',
                          content: (
                            <Container header={<Header variant="h3">Workflow transitions</Header>}>
                              <WorkflowGraph key={activeMachineArn} definition={editDefDoc} />
                            </Container>
                          ),
                        },
                        {
                          label: `Executions History (${(data.executions || []).length})`,
                          id: 'history',
                          content: (
                            <Table
                              columnDefinitions={[
                                { id: 'name', header: 'Execution Name', cell: (i: any) => <strong>{i.name || i.executionArn?.split(':').pop() || 'Execution'}</strong> },
                                {
                                  id: 'status',
                                  header: 'Status',
                                  cell: (i: any) => {
                                    const st = i.status || 'SUCCEEDED';
                                    return <StatusIndicator type={st === 'SUCCEEDED' ? 'success' : st === 'RUNNING' ? 'in-progress' : 'error'}>{st}</StatusIndicator>;
                                  },
                                  width: 140,
                                },
                                { id: 'time', header: 'Start Date', cell: (i: any) => i.startDate || new Date().toISOString().split('T')[0], width: 160 },
                                {
                                  id: 'act',
                                  header: 'Actions',
                                  cell: (i: any) => (
                                    <SpaceBetween direction="horizontal" size="xs">
                                      <Button onClick={() => handleViewExecutionHistory(i.executionArn || i.arn)}>History</Button>
                                      <Button iconName="remove" onClick={() => handleStopExecution(i.executionArn || i.arn)}>Stop</Button>
                                    </SpaceBetween>
                                  ),
                                  width: 180,
                                },
                              ]}
                              items={data.executions || []}
                              empty={<Box textAlign="center">No executions recorded for this state machine.</Box>}
                            />
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
            label: `Activities (${activities.length})`,
            id: 'activities',
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateActivityOpen(true)}>
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
                      { id: 'name', header: 'Activity Name', cell: (a: any) => <strong>{a.name || a.Name}</strong> },
                      { id: 'arn', header: 'Activity ARN', cell: (a: any) => <code>{a.activityArn || a.ActivityArn}</code> },
                      { id: 'date', header: 'Creation Date', cell: (a: any) => a.creationDate || a.CreationDate || 'Today', width: 160 },
                      {
                        id: 'act',
                        header: 'Action',
                        cell: (a: any) => (
                          <Button iconName="remove" onClick={() => handleDeleteActivity(a.activityArn || a.ActivityArn)}>
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

                <Container header={<Header variant="h2">Send Task Token Callback (.waitForTaskToken)</Header>}>
                  <SpaceBetween size="m">
                    <FormField label="Task Token" description="Unique opaque string passed to worker or external system.">
                      <Input value={taskToken} onChange={({ detail }) => setTaskToken(detail.value)} placeholder="AAAAKgAAAAIAAAAAAAAAA..." />
                    </FormField>
                    <FormField label="Task Output (JSON)">
                      <Textarea rows={4} value={taskOutput} onChange={({ detail }) => setTaskOutput(detail.value)} />
                    </FormField>
                    <SpaceBetween direction="horizontal" size="xs">
                      <Button variant="primary" loading={sendingTask} onClick={() => handleSendTaskCallback(true)}>
                        Send Task Success
                      </Button>
                      <Button loading={sendingTask} onClick={() => handleSendTaskCallback(false)}>
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
              <Button variant="primary" loading={creating} onClick={handleCreateMachine}>
                Create State Machine
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="State Machine Name" description="Unique workflow name.">
            <Input value={machineName} onChange={({ detail }) => setMachineName(detail.value)} placeholder="OrderProcessingWorkflow" />
          </FormField>

          <FormField label="Workflow Type">
            <Select
              selectedOption={machineType}
              onChange={({ detail }) => setMachineType(detail.selectedOption as any)}
              options={[
                { label: 'STANDARD (Exactly-once execution model)', value: 'STANDARD' },
                { label: 'EXPRESS (High-throughput, at-least-once model)', value: 'EXPRESS' },
              ]}
            />
          </FormField>

          <FormField label="Amazon States Language (ASL) Definition (JSON)">
            <Textarea rows={10} value={definition} onChange={({ detail }) => setDefinition(detail.value)} />
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
              <Button variant="primary" loading={savingDef} onClick={handleUpdateDefinition}>
                Save Definition
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Amazon States Language (ASL) Definition">
          <Textarea rows={14} value={editDefDoc} onChange={({ detail }) => setEditDefDoc(detail.value)} />
        </FormField>
      </Modal>

      <Modal
        visible={startExecModalOpen}
        onDismiss={() => setStartExecModalOpen(false)}
        header={`Start Execution: ${activeMachine?.Name || activeMachine?.name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setStartExecModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={executing} onClick={handleStartExecution}>
                Start Execution
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Execution Name (Optional)">
            <Input value={execName} onChange={({ detail }) => setExecName(detail.value)} placeholder="exec-run-101" />
          </FormField>
          <FormField label="Execution Input (JSON)">
            <Textarea rows={6} value={execInput} onChange={({ detail }) => setExecInput(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={historyModalOpen}
        onDismiss={() => setHistoryModalOpen(false)}
        header={`Execution History: ${selectedExecArn}`}
        size="large"
      >
        <WorkflowGraph definition={editDefDoc} events={selectedExecHistory} />
        <Table
          columnDefinitions={[
            { id: 'id', header: 'Step ID', cell: (h: any) => h.id || h.Id, width: 80 },
            { id: 'type', header: 'Event Type', cell: (h: any) => <Badge color="blue">{h.type || h.Type}</Badge>, width: 220 },
            { id: 'time', header: 'Timestamp', cell: (h: any) => h.timestamp || 'Just now', width: 140 },
            {
              id: 'details',
              header: 'Event Details',
              cell: (h: any) => <code>{JSON.stringify(h.stateEnteredEventDetails || h.stateExitedEventDetails || h.executionSucceededEventDetails || {})}</code>,
            },
          ]}
          items={selectedExecHistory}
          loading={loadingHistory}
          empty={<Box textAlign="center">No detailed event history available.</Box>}
        />
      </Modal>

      <Modal
        visible={createActivityOpen}
        onDismiss={() => setCreateActivityOpen(false)}
        header="Create Step Functions Activity"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateActivityOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingActivity} onClick={handleCreateActivity}>
                Create Activity
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Activity Name">
          <Input value={newActivityName} onChange={({ detail }) => setNewActivityName(detail.value)} placeholder="ProcessHumanApprovalTask" />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
