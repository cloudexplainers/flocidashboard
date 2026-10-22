import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import SpaceBetween from '@cloudscape-design/components/space-between';
import { CodeSnippet } from './CodeSnippet';
import { formatAwsDate } from '../format';

const isTimestampKey = (name: string) => /(created|creation|modified|updated|time|date|_at)$|^(created|start|end|issued|not_before|not_after)/i.test(name);

export const attributeLabel = (value: string) => value.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, c => c.toUpperCase());
export function ResourceValue({ value, name = '' }: { value: unknown; name?: string }) {
  if (value == null || value === '') return <>Not reported</>;
  // Feature switches read Enabled/Disabled in the console; other flags (Multi-AZ, Deleted) read Yes/No.
  if (typeof value === 'boolean') return <>{/enabled|encrypt|protection|monitoring|upgrade|logging/i.test(name) ? (value ? 'Enabled' : 'Disabled') : (value ? 'Yes' : 'No')}</>;
  if (typeof value === 'string' && isTimestampKey(name) && /^\d{4}-\d{2}-\d{2}T/.test(value)) return <>{formatAwsDate(value)}</>;
  if (/status$|state$/i.test(name) && typeof value === 'string') {
    const state = value.toUpperCase();
    const type = /FAILED|ERROR|UNHEALTHY|ALARM|DENIED/.test(state) ? 'error' : /^(ACTIVE|AVAILABLE|RUNNING|ISSUED|ENABLED|HEALTHY|SUCCEEDED|DEPLOYED|OK)$/.test(state) ? 'success' : /PENDING|CREATING|UPDATING|PROGRESS|DELETING/.test(state) ? 'in-progress' : 'info';
    return <StatusIndicator type={type}>{value}</StatusIndicator>;
  }
  return <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}</span>;
}
export function ResourceDetails({ resource, title = 'Resource details', columns = 2 }: { resource: Record<string, unknown>; title?: string; columns?: 1 | 2 }) {
  const entries = Object.entries(resource);
  return <Container header={<Header variant="h2">{title}</Header>}><SpaceBetween size="m">
    <KeyValuePairs columns={columns} items={entries.filter(([, value]) => value == null || typeof value !== 'object').map(([key, value]) => ({ label: attributeLabel(key), value: <ResourceValue name={key} value={value} /> }))} />
    {entries.filter(([, value]) => value !== null && typeof value === 'object').map(([key, value]) => <ExpandableSection key={key} headerText={attributeLabel(key)}><CodeSnippet language="json" code={JSON.stringify(value, null, 2)} /></ExpandableSection>)}
    <ExpandableSection headerText="Raw resource JSON"><CodeSnippet language="json" code={JSON.stringify(resource, null, 2)} /></ExpandableSection>
  </SpaceBetween></Container>;
}
