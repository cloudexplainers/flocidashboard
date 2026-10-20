import Alert from '@cloudscape-design/components/alert';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Table from '../components/ResourceTable';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { ResourceDetails, ResourceValue } from '../components/ResourceDetails';

export function EKSHealth({ health }: { health: any }) {
  if (!health || !Array.isArray(health.issues)) return <Alert type="info">Health information not reported.</Alert>;
  if (!health.issues.length) return <Alert type="success">No health issues reported.</Alert>;
  return <Container header={<Header variant="h3">Health issues</Header>}><Table items={health.issues} columnDefinitions={[
    { id: 'code', header: 'Issue code', cell: (item: any) => <StatusIndicator type="error">{item.code || 'Unknown issue'}</StatusIndicator> },
    { id: 'message', header: 'Message', cell: (item: any) => item.message || 'Not reported' },
    { id: 'resources', header: 'Affected resources', cell: (item: any) => item.resourceIds?.join(', ') || 'Not reported' },
  ]} /></Container>;
}
export function EKSNetworking({ config, navigate }: { config: any; navigate: (path: string) => void }) {
  return <Container header={<Header variant="h3">Networking</Header>}><KeyValuePairs columns={2} items={[
    { label: 'VPC', value: config?.vpcId ? <Button variant="inline-link" onClick={() => navigate('ec2/vpcs')}>{config.vpcId}</Button> : 'Not reported' },
    { label: 'Subnets', value: config?.subnetIds?.length ? <SpaceBetween size="xs">{config.subnetIds.map((id: string) => <Button key={id} variant="inline-link" onClick={() => navigate('ec2/subnets')}>{id}</Button>)}</SpaceBetween> : 'Not reported' },
    { label: 'Security groups', value: config?.securityGroupIds?.length ? <SpaceBetween size="xs">{config.securityGroupIds.map((id: string) => <Button key={id} variant="inline-link" onClick={() => navigate('ec2/security_groups')}>{id}</Button>)}</SpaceBetween> : 'Not reported' },
    { label: 'Cluster security group', value: config?.clusterSecurityGroupId ? <Button variant="inline-link" onClick={() => navigate('ec2/security_groups')}>{config.clusterSecurityGroupId}</Button> : 'Not reported' },
    { label: 'Public endpoint access', value: <ResourceValue value={config?.endpointPublicAccess} /> },
    { label: 'Private endpoint access', value: <ResourceValue value={config?.endpointPrivateAccess} /> },
  ]} /></Container>;
}
export function EKSNodegroupDetails({ cluster, nodegroup, navigate, onDelete }: { cluster: any; nodegroup: any; navigate: (path: string) => void; onDelete: () => void }) {
  return <SpaceBetween size="l">
    <Header variant="h2" description={nodegroup.arn || nodegroup.nodegroupArn} actions={<Button onClick={onDelete}>Delete node group</Button>}>Node group: {nodegroup.name}</Header>
    {nodegroup.details?.error && <Alert type="warning">{nodegroup.details.error}</Alert>}
    <KeyValuePairs columns={3} items={[
      { label: 'Status', value: <ResourceValue name="status" value={nodegroup.status} /> },
      { label: 'Instance types', value: nodegroup.instanceTypes?.join(', ') || 'Not reported' },
      { label: 'Capacity type', value: nodegroup.capacityType || 'Not reported' },
      { label: 'Minimum size', value: nodegroup.scalingConfig?.minSize ?? 'Not reported' },
      { label: 'Desired size', value: nodegroup.scalingConfig?.desiredSize ?? 'Not reported' },
      { label: 'Maximum size', value: nodegroup.scalingConfig?.maxSize ?? 'Not reported' },
      { label: 'Disk size', value: nodegroup.diskSize == null ? 'Not reported' : `${nodegroup.diskSize} GiB` },
      { label: 'Node IAM role', value: nodegroup.nodeRole || 'Not reported' },
    ]} />
    <EKSHealth health={nodegroup.health} />
    <EKSNetworking config={{ ...cluster.resourcesVpcConfig, subnetIds: nodegroup.subnets, securityGroupIds: nodegroup.resources?.remoteAccessSecurityGroup ? [nodegroup.resources.remoteAccessSecurityGroup] : undefined }} navigate={navigate} />
    <Container header={<Header variant="h3" description="Compute associations returned by EKS. Kubernetes node readiness is not reported by this inventory.">Compute resources</Header>}><Table items={nodegroup.resources?.autoScalingGroups || []} columnDefinitions={[
      { id: 'name', header: 'Auto Scaling group', cell: (item: any) => <Button variant="inline-link" onClick={() => navigate('autoscaling')}>{item.name}</Button> },
    ]} empty={<span>No Auto Scaling groups reported.</span>} /></Container>
    <ResourceDetails resource={Object.fromEntries(Object.entries(nodegroup).filter(([key, value]) => {
      const aliases: Record<string, string> = { scalingConfig: 'scaling_config', instanceTypes: 'instance_types', nodeRole: 'node_role', capacityType: 'capacity_type', diskSize: 'disk_size' };
      return value != null && key !== 'nodegroupName' && !(aliases[key] && aliases[key] in nodegroup);
    }))} title="Node group configuration" />
  </SpaceBetween>;
}
