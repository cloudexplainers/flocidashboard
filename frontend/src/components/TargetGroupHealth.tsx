import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from './ResourceTable';
import { ResourceValue } from './ResourceDetails';

export function TargetGroupHealth({ group, targets }: { group: any; targets: any[] }) {
  return <Container header={<Header variant="h2" description={group.TargetGroupArn}>Target group: {group.TargetGroupName}</Header>}><SpaceBetween size="m">
    <KeyValuePairs columns={3} items={[
      { label: 'Protocol / port', value: `${group.Protocol || 'Not reported'} / ${group.Port ?? 'Not reported'}` },
      { label: 'VPC', value: group.VpcId || 'Not reported' },
      { label: 'Target type', value: group.TargetType || 'Not reported' },
      { label: 'Healthy targets', value: targets.filter(item => item.TargetHealth?.State === 'healthy').length },
      { label: 'Unhealthy targets', value: targets.filter(item => item.TargetHealth?.State === 'unhealthy').length },
      { label: 'Other target states', value: targets.filter(item => !['healthy', 'unhealthy'].includes(item.TargetHealth?.State)).length },
      { label: 'Health check path', value: group.HealthCheckPath || 'Not reported' },
    ]} />
    <Table header={<Header variant="h3">Target health details</Header>} items={targets} columnDefinitions={[
      { id: 'id', header: 'Target ID', cell: (item: any) => item.Target?.Id || item.Id || 'Not reported' },
      { id: 'port', header: 'Port', cell: (item: any) => item.Target?.Port ?? 'Not reported' },
      { id: 'health', header: 'Health', cell: (item: any) => <ResourceValue name="status" value={item.TargetHealth?.State} /> },
      { id: 'reason', header: 'Reason', cell: (item: any) => item.TargetHealth?.Reason || 'Not reported' },
      { id: 'description', header: 'Description', cell: (item: any) => item.TargetHealth?.Description || 'Not reported' },
    ]} empty={<span>No targets reported for this target group.</span>} />
  </SpaceBetween></Container>;
}
