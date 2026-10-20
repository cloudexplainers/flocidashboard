import { useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Table from '../components/ResourceTable';
import { ResourceDetails, ResourceValue } from '../components/ResourceDetails';
import { clusterName, serviceName, taskArn, taskId, clusterPath, servicePath, taskPath, parseEcsRoute } from './ecsResources';

type Props = { path: string; data: any; loading: boolean; error: string; navigate: (path: string) => void; createService: () => void; runTask: () => void; scale: (service: any) => void; deleteService: (service: any) => void; stopTask: (task: any) => void; deleteCluster: () => void };
export function ECSResourcePage({ path, data, loading, error, navigate, createService, runTask, scale, deleteService, stopTask, deleteCluster }: Props) {
  const route = parseEcsRoute(path);
  const [tab, setTab] = useState('overview');
  if (loading) return <ResourceValue name="status" value="Loading resource" />;
  if (error) return <Alert type="error" header="Unable to load ECS resource">{error}</Alert>;
  const cluster = data.clusters.find((item: any) => clusterName(item) === route.cluster);
  const belongs = (item: any) => (item.clusterArn || item.cluster || '').split('/').pop() === route.cluster;
  const services = data.services.filter(belongs);
  const service = services.find((item: any) => serviceName(item) === route.service);
  const tasks = data.tasks.filter(belongs).filter((item: any) => !route.service || item.group === `service:${route.service}`);
  const task = tasks.find((item: any) => taskId(item) === route.task);
  if (route.invalid || !cluster || (route.service && !service) || (route.task && !task)) return <Alert type="warning" header="ECS resource not found" action={<Button onClick={() => navigate('ecs/clusters')}>View clusters</Button>}>This resource is not reported in the current account and Region. Check the URL or refresh the inventory.</Alert>;
  const title = route.task ? `Task: ${route.task}` : route.service ? `Service: ${route.service}` : `Cluster: ${route.cluster}`;
  const taskTable = <Table items={tasks} columnDefinitions={[
    { id: 'task', header: 'Task ID', cell: (item: any) => <Button variant="inline-link" onClick={() => navigate(taskPath(route.cluster!, item, route.service))}>{taskId(item)}</Button> },
    { id: 'state', header: 'Last status', cell: (item: any) => <ResourceValue name="status" value={item.lastStatus} /> },
    { id: 'reason', header: 'Stopped reason', cell: (item: any) => item.stoppedReason || '—' },
  ]} empty={<span>No tasks reported for this resource.</span>} />;
  return <SpaceBetween size="l">
    <Header variant="h2" description={task ? taskArn(task) : service ? service.serviceArn : cluster.clusterArn} actions={<SpaceBetween direction="horizontal" size="xs">
      {task ? <Button disabled={task.lastStatus === 'STOPPED'} onClick={() => stopTask(task)}>Stop task</Button> : service ? <><Button onClick={() => scale(service)}>Update service</Button><Button onClick={() => deleteService(service)}>Delete service</Button></> : <><Button onClick={createService}>Create service</Button><Button onClick={runTask}>Run task</Button><Button onClick={deleteCluster}>Delete cluster</Button></>}
    </SpaceBetween>}>{title}</Header>
    {task ? <><Button onClick={() => navigate(route.service ? servicePath(route.cluster!, route.service) : clusterPath(route.cluster!))}>Back to {route.service ? 'service' : 'cluster'}</Button><ResourceDetails resource={task} title="Task configuration" /><Container header={<Header variant="h3">Containers</Header>}><Table items={task.containers || []} columnDefinitions={[
      { id: 'name', header: 'Name', cell: (item: any) => item.name }, { id: 'image', header: 'Image', cell: (item: any) => item.image || 'Not reported' }, { id: 'status', header: 'Status', cell: (item: any) => <ResourceValue name="status" value={item.lastStatus} /> }, { id: 'exit', header: 'Exit code / reason', cell: (item: any) => `${item.exitCode ?? 'Not reported'} / ${item.reason || 'Not reported'}` },
    ]} empty={<span>No container details reported.</span>} /></Container></> : <Tabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
      { id: 'overview', label: 'Overview', content: <SpaceBetween size="m"><KeyValuePairs columns={3} items={[
        { label: 'Status', value: <ResourceValue name="status" value={(service || cluster).status} /> },
        { label: 'Running tasks', value: service ? service.runningCount ?? 'Not reported' : cluster.running_tasks ?? cluster.runningTasksCount ?? tasks.filter((item: any) => item.lastStatus === 'RUNNING').length },
        { label: 'Desired tasks', value: service ? service.desiredCount ?? 'Not reported' : services.reduce((sum: number, item: any) => sum + Number(item.desiredCount || 0), 0) },
      ]} />{service ? <ResourceDetails resource={service} title="Service configuration" /> : <Table items={services} header={<Header variant="h3">Services</Header>} columnDefinitions={[
        { id: 'name', header: 'Service name', cell: (item: any) => <Button variant="inline-link" onClick={() => navigate(servicePath(route.cluster!, serviceName(item)))}>{serviceName(item)}</Button> },
        { id: 'desired', header: 'Desired tasks', cell: (item: any) => item.desiredCount ?? 'Not reported' },
        { id: 'running', header: 'Running tasks', cell: (item: any) => item.runningCount ?? 'Not reported' },
      ]} empty={<span>No services reported for this cluster.</span>} />}</SpaceBetween> },
      { id: 'tasks', label: `Tasks (${tasks.length})`, content: taskTable },
      ...(service ? [{ id: 'deployments', label: 'Deployments', content: <Table items={service.deployments || []} columnDefinitions={[
        { id: 'id', header: 'Deployment ID', cell: (item: any) => item.id }, { id: 'state', header: 'Rollout state', cell: (item: any) => <ResourceValue name="status" value={item.rolloutState || item.status} /> }, { id: 'reason', header: 'Reason', cell: (item: any) => item.rolloutStateReason || 'Not reported' },
      ]} empty={<span>No deployments reported.</span>} /> }, { id: 'events', label: 'Events', content: <Table items={service.events || []} columnDefinitions={[
        { id: 'time', header: 'Timestamp', cell: (item: any) => item.createdAt || 'Not reported' }, { id: 'message', header: 'Message', cell: (item: any) => item.message },
      ]} empty={<span>No service events reported.</span>} /> }] : [{ id: 'configuration', label: 'Configuration', content: <ResourceDetails resource={Object.fromEntries(Object.entries(cluster).filter(([key]) => !['tasks', 'services', 'container_instances'].includes(key)))} title="Cluster configuration" /> }]),
    ]} />}
  </SpaceBetween>;
}
