import { ECSResourcePage } from './ECSResourcePage';
import { normalizeEcsInventory, parseEcsRoute, clusterPath, servicePath, taskPath, taskArn } from './ecsResources';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import React, { useState, useEffect, useMemo } from 'react';
import Table from '../components/ResourceTable';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Container from '@cloudscape-design/components/container';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import {
  createEcsCluster,
  deleteEcsCluster,
  registerEcsTaskDefinition,
  deregisterEcsTaskDefinition,
  runEcsTask,
  stopEcsTask,
  createEcsService,
  updateEcsService,
  deleteEcsService,
  updateEcsContainerInstanceState,
} from '../api/client';

/** ECS status values (ACTIVE, PROVISIONING, DRAINING, INACTIVE, RUNNING, STOPPED, ...) as console status indicators. */
const ecsStatusType = (status?: string): 'success' | 'stopped' | 'in-progress' | 'error' | 'info' => {
  if (!status) return 'info';
  if (['ACTIVE', 'RUNNING'].includes(status)) return 'success';
  if (['STOPPED', 'INACTIVE', 'DEPROVISIONING'].includes(status)) return 'stopped';
  if (['FAILED'].includes(status)) return 'error';
  return 'in-progress';
};
const titleStatus = (status?: string) => (status ? status.charAt(0) + status.slice(1).toLowerCase() : '-');

interface ECSConsoleProps {
  activeTab?: string;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  onTabChange?: (tabId: string) => void;
}

export const ECSConsole: React.FC<ECSConsoleProps> = ({ activeTab, onTabChange, resourcePath = 'ecs', onNavigate }) => {
  const [data, setData] = useState<any>({
    clusters: [],
    services: [],
    task_definitions: [],
    tasks: [],
    container_instances: [],
  });
  const route = parseEcsRoute(resourcePath);
  const navigate = (path: string) => onNavigate?.(path);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Selections
  const [selectedClusters, setSelectedClusters] = useState<any[]>([]);

  // Create Cluster Modal
  const [createClusterOpen, setCreateClusterOpen] = useState(false);
  const [clusterName, setClusterName] = useState('');
  const [creatingCluster, setCreatingCluster] = useState(false);

  // Register Task Definition Modal
  const [registerTaskDefOpen, setRegisterTaskDefOpen] = useState(false);
  const [taskDefFamily, setTaskDefFamily] = useState('');
  const [taskDefCpu, setTaskDefCpu] = useState('256');
  const [taskDefMemory, setTaskDefMemory] = useState('512');
  const [taskDefNetworkMode, setTaskDefNetworkMode] = useState({ label: 'awsvpc (Fargate/ECS standard)', value: 'awsvpc' });
  const [taskDefContainersDoc, setTaskDefContainersDoc] = useState(
    JSON.stringify(
      [
        {
          name: 'web-app',
          image: 'nginx:latest',
          essential: true,
          portMappings: [{ containerPort: 80, hostPort: 80, protocol: 'tcp' }],
          environment: [{ name: 'ENVIRONMENT', value: 'production' }],
        },
      ],
      null,
      2
    )
  );
  const [registeringTaskDef, setRegisteringTaskDef] = useState(false);

  // Run Task Modal
  const [runTaskOpen, setRunTaskOpen] = useState(false);
  const [runTaskCluster, setRunTaskCluster] = useState('');
  const [runTaskDefinition, setRunTaskDefinition] = useState('');
  const [runTaskLaunchType, setRunTaskLaunchType] = useState({ label: 'FARGATE', value: 'FARGATE' });
  const [runTaskCount, setRunTaskCount] = useState('1');
  const [runningTask, setRunningTask] = useState(false);

  // Create Service Modal
  const [createServiceOpen, setCreateServiceOpen] = useState(false);
  const [serviceName, setServiceName] = useState('');
  const [serviceCluster, setServiceCluster] = useState('');
  const [serviceTaskDef, setServiceTaskDef] = useState('');
  const [serviceDesiredCount, setServiceDesiredCount] = useState('1');
  const [serviceLaunchType, setServiceLaunchType] = useState({ label: 'FARGATE', value: 'FARGATE' });
  const [serviceSubnets, setServiceSubnets] = useState('');
  const [serviceSecurityGroups, setServiceSecurityGroups] = useState('');
  const [creatingService, setCreatingService] = useState(false);

  // Scale Service Modal
  const [scaleServiceOpen, setScaleServiceOpen] = useState(false);
  const [scaleTargetService, setScaleTargetService] = useState<any | null>(null);
  const [scaleDesiredCount, setScaleDesiredCount] = useState('1');
  const [scalingService, setScalingService] = useState(false);

  const loadData = async () => {
    setLoading(true); setLoadError('');
    try {
      const response = await fetch('/api/ecs/');
      const payload = await response.json();
      if (!response.ok || payload.error) throw new Error(payload.error || 'Unable to load ECS inventory');
      const res = normalizeEcsInventory(payload);
      setData(res || { clusters: [], services: [], task_definitions: [], tasks: [], container_instances: [] });
      setSelectedClusters(previous => previous.length ? res.clusters.filter((cluster: any) => cluster.clusterName === previous[0].clusterName) : res.clusters.slice(0, 1));
      if (res?.clusters?.length > 0 && selectedClusters.length === 0) {
        setSelectedClusters([res.clusters[0]]);
        setRunTaskCluster(res.clusters[0].clusterName || res.clusters[0].cluster_name || '');
        setServiceCluster(res.clusters[0].clusterName || res.clusters[0].cluster_name || '');
      }
      if (res?.task_definitions?.length > 0 && !taskDefFamily) {
        const firstDef = res.task_definitions[0];
        const fam = firstDef.family || firstDef.taskDefinitionArn?.split('/')[1]?.split(':')[0] || '';
        setRunTaskDefinition(firstDef.taskDefinitionArn || fam);
        setServiceTaskDef(firstDef.taskDefinitionArn || fam);
      }
    } catch (err: any) {
      setLoadError(err.message || 'Unable to load ECS inventory');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeCluster = route.cluster ? data.clusters.find((item: any) => item.clusterName === route.cluster) : data.clusters.find((item: any) => item.clusterName === selectedClusters[0]?.clusterName);
  useEffect(() => {
    const name = activeCluster?.clusterName || '';
    setRunTaskCluster(name); setServiceCluster(name); setScaleServiceOpen(false); setCreateServiceOpen(false); setRunTaskOpen(false);
  }, [activeCluster?.clusterName, resourcePath]);
  const activeClusterName = activeCluster ? activeCluster.clusterName || activeCluster.cluster_name : '';

  // Filtered lists
  const filteredClusters = useMemo(() => {
    const list = data.clusters || [];
    if (!filterText) return list;
    return list.filter((c: any) =>
      (c.clusterName || c.cluster_name || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [data.clusters, filterText]);

  const filteredServices = useMemo(() => {
    const list = data.services || [];
    if (!activeClusterName) return list;
    return list.filter((s: any) => {
      const cArn = s.clusterArn || s.cluster || '';
      return cArn === activeClusterName || cArn.split('/').pop() === activeClusterName;
    });
  }, [data.services, activeClusterName]);

  const filteredTasks = useMemo(() => {
    const list = data.tasks || [];
    if (!activeClusterName) return list;
    return list.filter((t: any) => {
      const cArn = t.clusterArn || t.cluster || '';
      return cArn === activeClusterName || cArn.split('/').pop() === activeClusterName;
    });
  }, [data.tasks, activeClusterName]);

  // Actions
  const handleCreateCluster = async () => {
    if (!clusterName.trim()) return;
    setCreatingCluster(true);
    try {
      await createEcsCluster(clusterName.trim());
      setActionMessage({ type: 'success', text: `ECS Cluster "${clusterName.trim()}" created successfully.` });
      setCreateClusterOpen(false);
      setClusterName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create cluster' });
    } finally {
      setCreatingCluster(false);
    }
  };

  const handleDeleteCluster = async (cName: string) => {
    if (!confirm(`Are you sure you want to delete ECS cluster "${cName}"?`)) return;
    try {
      await deleteEcsCluster(cName);
      setActionMessage({ type: 'success', text: `Cluster "${cName}" deleted.` });
      navigate('ecs/clusters');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete cluster' });
    }
  };

  const handleRegisterTaskDef = async () => {
    if (!taskDefFamily.trim()) return;
    setRegisteringTaskDef(true);
    try {
      let parsedContainers = [];
      try {
        parsedContainers = JSON.parse(taskDefContainersDoc);
      } catch {
        throw new Error('Invalid JSON in Container Definitions document');
      }
      await registerEcsTaskDefinition({
        family: taskDefFamily.trim(),
        container_definitions: parsedContainers,
        cpu: taskDefCpu,
        memory: taskDefMemory,
        network_mode: taskDefNetworkMode.value,
        requires_compatibilities: ['FARGATE', 'EC2'],
      });
      setActionMessage({ type: 'success', text: `Task Definition "${taskDefFamily.trim()}" registered.` });
      setRegisterTaskDefOpen(false);
      setTaskDefFamily('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to register task definition' });
    } finally {
      setRegisteringTaskDef(false);
    }
  };

  const handleDeregisterTaskDef = async (arnOrName: string) => {
    if (!confirm(`Deregister ${arnOrName.split('/').pop()}? It becomes INACTIVE: existing tasks and services keep running, but it can't be used for new ones.`)) return;
    try {
      await deregisterEcsTaskDefinition(arnOrName);
      setActionMessage({ type: 'success', text: `Task definition deregistered.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to deregister task definition' });
    }
  };

  const handleRunTask = async () => {
    if (!runTaskCluster || !runTaskDefinition) return;
    setRunningTask(true);
    try {
      await runEcsTask({
        cluster: runTaskCluster,
        task_definition: runTaskDefinition,
        launch_type: runTaskLaunchType.value,
        count: parseInt(runTaskCount, 10) || 1,
      });
      setActionMessage({ type: 'success', text: `Task launched on cluster "${runTaskCluster}".` });
      setRunTaskOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to run task' });
    } finally {
      setRunningTask(false);
    }
  };

  const handleStopTask = async (taskArn: string, cluster = activeClusterName) => {
    const targetCluster = cluster || activeClusterName;
    if (!targetCluster || !taskArn) return;
    if (!confirm(`Stop task ${taskArn.split('/').pop()}? The task's containers receive SIGTERM, then SIGKILL after the stop timeout.`)) return;
    try {
      await stopEcsTask(targetCluster, taskArn);
      setActionMessage({ type: 'success', text: 'Task stop requested.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to stop task' });
    }
  };

  const handleCreateService = async () => {
    if (!serviceCluster || !serviceName.trim() || !serviceTaskDef) return;
    setCreatingService(true);
    try {
      await createEcsService({
        cluster: serviceCluster,
        service_name: serviceName.trim(),
        task_definition: serviceTaskDef,
        desired_count: Number(serviceDesiredCount),
        network_configuration: serviceSubnets.trim() ? { awsvpcConfiguration: { subnets: serviceSubnets.split(',').map(value => value.trim()).filter(Boolean), securityGroups: serviceSecurityGroups.split(',').map(value => value.trim()).filter(Boolean), assignPublicIp: 'DISABLED' } } : undefined,
        launch_type: serviceLaunchType.value,
      });
      setActionMessage({ type: 'success', text: `Service "${serviceName.trim()}" created.` });
      setCreateServiceOpen(false);
      setServiceName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create service' });
    } finally {
      setCreatingService(false);
    }
  };

  const handleScaleService = async () => {
    if (!scaleTargetService || !activeClusterName) return;
    setScalingService(true);
    try {
      const sName = scaleTargetService.serviceName || scaleTargetService.service_name;
      await updateEcsService({
        cluster: activeClusterName,
        service: sName,
        desired_count: parseInt(scaleDesiredCount, 10) || 0,
      });
      setActionMessage({ type: 'success', text: `Service "${sName}" scaled to ${scaleDesiredCount} desired tasks.` });
      setScaleServiceOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to scale service' });
    } finally {
      setScalingService(false);
    }
  };

  const handleToggleInstanceState = async (instanceArn: string, currentState: string) => {
    if (!activeClusterName) return;
    const nextStatus = currentState === 'DRAINING' ? 'ACTIVE' : 'DRAINING';
    try {
      await updateEcsContainerInstanceState(activeClusterName, [instanceArn], nextStatus);
      setActionMessage({ type: 'success', text: `Container instance state set to ${nextStatus}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update instance state' });
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Amazon Elastic Container Service (ECS) is a fully managed container orchestration service."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateClusterOpen(true)}>
              Create Cluster
            </Button>
          </SpaceBetween>
        }
      >
        Amazon Elastic Container Service (ECS)
      </Header>

      {actionMessage && (
        <Alert
          type={actionMessage.type}
          dismissible
          onDismiss={() => setActionMessage(null)}
        >
          {actionMessage.text}
        </Alert>
      )}

      {!route.cluster && <><FormField label="Cluster context" description="Services and tasks are scoped to this cluster.">
        <Select ariaLabel="Cluster context" selectedOption={activeClusterName ? { label: activeClusterName, value: activeClusterName } : null} placeholder="Select a cluster" options={(data.clusters || []).map((cluster: any) => ({ label: cluster.clusterName || cluster.cluster_name, value: cluster.clusterName || cluster.cluster_name }))} onChange={({ detail }) => setSelectedClusters((data.clusters || []).filter((cluster: any) => (cluster.clusterName || cluster.cluster_name) === detail.selectedOption.value))} />
      </FormField>
      {/* Cluster Overview Metrics */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
        <Container>
          <Box variant="awsui-key-label">Total Clusters</Box>
          <Box variant="awsui-value-large">{(data.clusters || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Active Services</Box>
          <Box variant="awsui-value-large">{(data.services || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Running Tasks</Box>
          <Box variant="awsui-value-large">{(data.tasks || []).filter((task: any) => (task.lastStatus || task.last_status) === 'RUNNING').length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Task Definitions</Box>
          <Box variant="awsui-value-large">{(data.task_definitions || []).length}</Box>
        </Container>
      </Grid>

      {activeCluster && <Container data-testid="ecs-cluster-summary" header={<Header variant="h2" actions={<SpaceBetween direction="horizontal" size="xs"><Button onClick={() => onTabChange?.('services')}>View services</Button><Button onClick={() => onTabChange?.('tasks')}>View tasks</Button></SpaceBetween>}>Cluster: {activeClusterName}</Header>}>
        <KeyValuePairs columns={3} items={[
          { label: 'Desired tasks', value: filteredServices.reduce((sum: number, service: any) => sum + Number(service.desiredCount ?? service.desired_count ?? 0), 0) },
          { label: 'Running tasks', value: filteredServices.reduce((sum: number, service: any) => sum + Number(service.runningCount ?? service.running_count ?? 0), 0) },
          { label: 'Pending tasks', value: filteredServices.reduce((sum: number, service: any) => sum + Number(service.pendingCount ?? service.pending_count ?? 0), 0) },
        ]} />
      </Container>}
      <Tabs
        activeTabId={activeTab || 'clusters'}
        onChange={({ detail }) => onTabChange && onTabChange(detail.activeTabId)}
        tabs={[
          {
            label: `Clusters (${(data.clusters || []).length})`,
            id: 'clusters',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateClusterOpen(true)}>
                        Create Cluster
                      </Button>
                    }
                  >
                    ECS Clusters
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                    filteringPlaceholder="Filter clusters by name..."
                  />
                  <Table
                    columnDefinitions={[
                      {
                        id: 'name',
                        header: 'Cluster',
                        cell: (item: any) => (
                          <Button variant="inline-link" onClick={() => { setSelectedClusters([item]); navigate(clusterPath(item.clusterName)); }}>
                            <strong>{item.clusterName || item.cluster_name}</strong>
                          </Button>
                        ),
                        sortingField: 'clusterName',
                      },
                      {
                        id: 'status',
                        header: 'Status',
                        cell: (item: any) => <StatusIndicator type={ecsStatusType(item.status)}>{titleStatus(item.status)}</StatusIndicator>,
                        width: 140,
                      },
                      {
                        id: 'services',
                        header: 'Services',
                        cell: (item: any) => item.activeServicesCount ?? '-',
                        width: 120,
                      },
                      {
                        id: 'tasks',
                        header: 'Running tasks',
                        cell: (item: any) => item.runningTasksCount ?? '-',
                        width: 130,
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <Button
                            iconName="remove"
                            onClick={() => handleDeleteCluster(item.clusterName || item.cluster_name)}
                          >
                            Delete
                          </Button>
                        ),
                        width: 110,
                      },
                    ]}
                    items={filteredClusters}
                    selectionType="single"
                    selectedItems={selectedClusters}
                    onSelectionChange={({ detail }) => setSelectedClusters(detail.selectedItems)}
                    empty={<Box textAlign="center">No ECS clusters found.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Services (${(data.services || []).length})`,
            id: 'services',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeClusterName ? `Showing services in cluster "${activeClusterName}"` : 'All ECS Services'}
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateServiceOpen(true)}>
                        Create Service
                      </Button>
                    }
                  >
                    Services
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'serviceName',
                      header: 'Service name',
                      cell: (item: any) => <Button variant="inline-link" onClick={() => navigate(servicePath(activeClusterName, item.serviceName || item.service_name))}>{item.serviceName || item.service_name}</Button>,
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      cell: (item: any) => <StatusIndicator type={ecsStatusType(item.status)}>{titleStatus(item.status)}</StatusIndicator>,
                      width: 130,
                    },
                    {
                      id: 'taskDef',
                      header: 'Task definition',
                      cell: (item: any) => <code>{item.taskDefinition || item.task_definition}</code>,
                    },
                    {
                      id: 'tasks',
                      header: 'Desired / running tasks',
                      cell: (item: any) => `${item.desiredCount ?? '-'} / ${item.runningCount ?? '-'}`,
                      width: 160,
                    },
                    {
                      id: 'launchType',
                      header: 'Launch type',
                      cell: (item: any) => item.launchType || (item.capacityProviderStrategy?.length ? 'Capacity provider' : '-'),
                      width: 140,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            onClick={() => {
                              setScaleTargetService(item);
                              setScaleDesiredCount(String(item.desiredCount ?? 1));
                              setScaleServiceOpen(true);
                            }}
                          >
                            Scale
                          </Button>
                          <Button
                            iconName="remove"
                            onClick={() => deleteEcsService(activeClusterName, item.serviceName || item.service_name).then(loadData)}
                          >
                            Delete
                          </Button>
                        </SpaceBetween>
                      ),
                      width: 200,
                    },
                  ]}
                  items={filteredServices}
                  empty={<Box textAlign="center">No services found in cluster.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Tasks (${(data.tasks || []).length})`,
            id: 'tasks',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeClusterName ? `Showing tasks running in "${activeClusterName}"` : 'All Tasks'}
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setRunTaskOpen(true)}>
                        Run New Task
                      </Button>
                    }
                  >
                    Tasks
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'taskArn',
                      header: 'Task ARN',
                      cell: (item: any) => <Button variant="inline-link" onClick={() => navigate(taskPath(activeClusterName, item))}>{(item.taskArn || item.task_arn)?.split('/').pop()}</Button>,
                    },
                    {
                      id: 'status',
                      header: 'Last status',
                      cell: (item: any) => {
                        const st = item.lastStatus || item.status;
                        return <StatusIndicator type={ecsStatusType(st)}>{titleStatus(st)}</StatusIndicator>;
                      },
                      width: 140,
                    },
                    {
                      id: 'taskDef',
                      header: 'Task definition',
                      cell: (item: any) => <code>{item.taskDefinitionArn || item.task_definition}</code>,
                    },
                    {
                      id: 'cpu',
                      header: 'CPU / memory',
                      cell: (item: any) => `${item.cpu ?? '-'} / ${item.memory ?? '-'}`,
                      width: 140,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <Button iconName="remove" onClick={() => handleStopTask(item.taskArn || item.task_arn)}>
                          Stop Task
                        </Button>
                      ),
                      width: 130,
                    },
                  ]}
                  items={filteredTasks}
                  empty={<Box textAlign="center">No tasks currently running.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Task Definitions (${(data.task_definitions || []).length})`,
            id: 'definitions',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setRegisterTaskDefOpen(true)}>
                        Create new Task Definition
                      </Button>
                    }
                  >
                    Task Definitions
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'family',
                      header: 'Task definition',
                      cell: (item: any) => <strong>{item.family || item.taskDefinitionArn?.split('/')[1]?.split(':')[0] || '-'}</strong>,
                    },
                    {
                      id: 'revision',
                      header: 'Revision',
                      cell: (item: any) => item.revision ?? '-',
                      width: 110,
                    },
                    {
                      id: 'compatibilities',
                      header: 'Compatibilities',
                      cell: (item: any) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          {(item.requiresCompatibilities || item.compatibilities || []).map((c: string) => (
                            <Badge key={c} color="blue">{c}</Badge>
                          ))}
                        </SpaceBetween>
                      ),
                      width: 200,
                    },
                    {
                      id: 'specs',
                      header: 'CPU / memory',
                      cell: (item: any) => `${item.cpu ?? '-'} / ${item.memory ?? '-'}`,
                      width: 140,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <Button
                          iconName="remove"
                          onClick={() => handleDeregisterTaskDef(item.taskDefinitionArn || item.family)}
                        >
                          Deregister
                        </Button>
                      ),
                      width: 130,
                    },
                  ]}
                  items={data.task_definitions || []}
                  empty={<Box textAlign="center">No task definitions registered.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Container Instances (${(data.container_instances || []).length})`,
            id: 'instances',
            content: (
              <Container header={<Header variant="h2">Registered EC2 Container Instances</Header>}>
                <Table
                  columnDefinitions={[
                    {
                      id: 'arn',
                      header: 'Container instance',
                      cell: (item: any) => <code>{item.containerInstanceArn || item.container_instance_arn}</code>,
                    },
                    {
                      id: 'ec2Id',
                      header: 'EC2 instance',
                      cell: (item: any) => item.ec2InstanceId || '-',
                      width: 180,
                    },
                    {
                      id: 'agent',
                      header: 'Agent connected',
                      cell: (item: any) => (item.agentConnected === true ? <StatusIndicator type="success">True</StatusIndicator> : item.agentConnected === false ? <StatusIndicator type="error">False</StatusIndicator> : '-'),
                      width: 160,
                    },
                    {
                      id: 'status',
                      header: 'State',
                      cell: (item: any) => <StatusIndicator type={ecsStatusType(item.status)}>{titleStatus(item.status)}</StatusIndicator>,
                      width: 130,
                    },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (item: any) => (
                        <Button
                          disabled={!item.status}
                          onClick={() => handleToggleInstanceState(item.containerInstanceArn, item.status)}
                        >
                          {item.status === 'DRAINING' ? 'Set Active' : 'Drain'}
                        </Button>
                      ),
                      width: 130,
                    },
                  ]}
                  items={data.container_instances || []}
                  empty={<Box textAlign="center">No EC2 container instances registered.</Box>}
                />
              </Container>
            ),
          },
        ]}
      />

      </>}
      {loadError && !route.cluster && <Alert type="error">{loadError}</Alert>}
      {route.cluster && <ECSResourcePage key={resourcePath} path={resourcePath} data={data} loading={loading} error={loadError} navigate={navigate}
        createService={() => setCreateServiceOpen(true)} runTask={() => setRunTaskOpen(true)}
        scale={service => { setScaleTargetService(service); setScaleDesiredCount(String(service.desiredCount ?? 0)); setScaleServiceOpen(true); }}
        deleteService={async service => { if (!confirm(`Delete ECS service "${service.serviceName}"?`)) return; try { await deleteEcsService(activeClusterName, service.serviceName); navigate(clusterPath(activeClusterName)); await loadData(); } catch (error: any) { setActionMessage({ type: 'error', text: error.message }); } }}
        stopTask={task => handleStopTask(taskArn(task), route.cluster || activeClusterName)} deleteCluster={() => handleDeleteCluster(route.cluster || activeClusterName)} />}
      {/* Create Cluster Modal */}
      <Modal
        visible={createClusterOpen}
        onDismiss={() => setCreateClusterOpen(false)}
        header="Create ECS Cluster"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateClusterOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingCluster} onClick={handleCreateCluster}>
                Create Cluster
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Cluster Name" description="Unique name for the ECS cluster.">
          <Input value={clusterName} onChange={({ detail }) => setClusterName(detail.value)} placeholder="production-cluster" />
        </FormField>
      </Modal>

      {/* Register Task Definition Modal */}
      <Modal
        visible={registerTaskDefOpen}
        onDismiss={() => setRegisterTaskDefOpen(false)}
        header="Register Task Definition"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRegisterTaskDefOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={registeringTaskDef} onClick={handleRegisterTaskDef}>
                Register Definition
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Task Definition Family">
            <Input value={taskDefFamily} onChange={({ detail }) => setTaskDefFamily(detail.value)} placeholder="api-service" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
            <FormField label="CPU (Units)">
              <Input value={taskDefCpu} onChange={({ detail }) => setTaskDefCpu(detail.value)} placeholder="256" />
            </FormField>
            <FormField label="Memory (MB)">
              <Input value={taskDefMemory} onChange={({ detail }) => setTaskDefMemory(detail.value)} placeholder="512" />
            </FormField>
            <FormField label="Network Mode">
              <Select
                selectedOption={taskDefNetworkMode}
                onChange={({ detail }) => setTaskDefNetworkMode(detail.selectedOption as any)}
                options={[
                  { label: 'awsvpc (Fargate/ECS)', value: 'awsvpc' },
                  { label: 'bridge (Docker)', value: 'bridge' },
                  { label: 'host', value: 'host' },
                ]}
              />
            </FormField>
          </Grid>
          <FormField label="Container Definitions (JSON)">
            <Textarea
              rows={9}
              value={taskDefContainersDoc}
              onChange={({ detail }) => setTaskDefContainersDoc(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Run Task Modal */}
      <Modal
        visible={runTaskOpen}
        onDismiss={() => setRunTaskOpen(false)}
        header="Run Standalone Task"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRunTaskOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={runningTask} onClick={handleRunTask}>
                Run Task
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Cluster">
            <Input value={runTaskCluster} onChange={({ detail }) => setRunTaskCluster(detail.value)} placeholder="cluster-name" />
          </FormField>
          <FormField label="Task Definition">
            <Input value={runTaskDefinition} onChange={({ detail }) => setRunTaskDefinition(detail.value)} placeholder="task-def:1" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
            <FormField label="Launch Type">
              <Select
                selectedOption={runTaskLaunchType}
                onChange={({ detail }) => setRunTaskLaunchType(detail.selectedOption as any)}
                options={[
                  { label: 'FARGATE', value: 'FARGATE' },
                  { label: 'EC2', value: 'EC2' },
                ]}
              />
            </FormField>
            <FormField label="Number of Tasks">
              <Input type="number" value={runTaskCount} onChange={({ detail }) => setRunTaskCount(detail.value)} />
            </FormField>
          </Grid>
        </SpaceBetween>
      </Modal>

      {/* Create Service Modal */}
      <Modal
        visible={createServiceOpen}
        onDismiss={() => setCreateServiceOpen(false)}
        header="Create ECS Service"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateServiceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingService} onClick={handleCreateService}>
                Create Service
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Cluster">
            <Input value={serviceCluster} onChange={({ detail }) => setServiceCluster(detail.value)} placeholder="cluster-name" />
          </FormField>
          <FormField label="Subnet IDs" description="Comma-separated subnet IDs. Required for task definitions using awsvpc networking."><Input value={serviceSubnets} onChange={({ detail }) => setServiceSubnets(detail.value)} /></FormField>
          <FormField label="Security group IDs" description="Optional comma-separated security group IDs."><Input value={serviceSecurityGroups} onChange={({ detail }) => setServiceSecurityGroups(detail.value)} /></FormField>
          <FormField label="Service Name">
            <Input value={serviceName} onChange={({ detail }) => setServiceName(detail.value)} placeholder="backend-svc" />
          </FormField>
          <FormField label="Task Definition">
            <Input value={serviceTaskDef} onChange={({ detail }) => setServiceTaskDef(detail.value)} placeholder="api-service:1" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
            <FormField label="Desired Tasks">
              <Input type="number" value={serviceDesiredCount} onChange={({ detail }) => setServiceDesiredCount(detail.value)} />
            </FormField>
            <FormField label="Launch Type">
              <Select
                selectedOption={serviceLaunchType}
                onChange={({ detail }) => setServiceLaunchType(detail.selectedOption as any)}
                options={[
                  { label: 'FARGATE', value: 'FARGATE' },
                  { label: 'EC2', value: 'EC2' },
                ]}
              />
            </FormField>
          </Grid>
        </SpaceBetween>
      </Modal>

      {/* Scale Service Modal */}
      <Modal
        visible={scaleServiceOpen}
        onDismiss={() => setScaleServiceOpen(false)}
        header={`Scale Service "${scaleTargetService?.serviceName || scaleTargetService?.service_name}"`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setScaleServiceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={scalingService} onClick={handleScaleService}>
                Update Desired Count
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Desired Tasks Count">
          <Input type="number" value={scaleDesiredCount} onChange={({ detail }) => setScaleDesiredCount(detail.value)} />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
