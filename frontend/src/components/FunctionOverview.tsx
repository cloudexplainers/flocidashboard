import { useEffect, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from './ResourceTable';

export function FunctionOverview({ name, runtime, triggers, onShowTriggers }: { name: string; runtime: string; triggers: any[]; onShowTriggers: () => void }) {
  return <Container header={<Header variant="h2">Function overview</Header>}>
    <div className="resource-flow">
      <section><Header variant="h3">Event sources</Header>{triggers.length ? triggers.map((trigger, index) => <p key={trigger.UUID || index}>{trigger.EventSourceArn || trigger.event_source_arn} · {trigger.State || trigger.state || 'Unknown'}</p>) : <p>No event source mappings reported.</p>}<Button onClick={onShowTriggers}>Manage triggers</Button></section>
      <span aria-hidden="true">→</span>
      <section><Header variant="h3">{name}</Header><p>{runtime}</p></section>
      <span aria-hidden="true">→</span>
      <section><Header variant="h3">Monitoring</Header><p>Log group: /aws/lambda/{name}</p><Button href="/cloudwatch/logs">Open CloudWatch logs</Button></section>
    </div>
  </Container>;
}
export function FunctionLogs({ name }: { name: string }) {
  const [events, setEvents] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [filter, setFilter] = useState('');
  useEffect(() => {
    let alive = true;
    setLoading(true); setError(''); setEvents([]);
    fetch(`/api/inspector/lambda/log-events/?${new URLSearchParams({ log_group_name: `/aws/lambda/${name}`, limit: '100' })}`).then(async response => {
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || 'Logs unavailable');
      return data;
    }).then(data => { if (alive) setEvents(data.events || []); }).catch(err => { if (alive) setError(err.message); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [name, revision]);
  return <Container header={<Header variant="h3" actions={<Button onClick={() => setRevision(revision + 1)} iconName="refresh">Refresh logs</Button>}>Function logs</Header>}>
    <SpaceBetween size="m">
      {error && <Alert type="error">{error}</Alert>}
      <TextFilter filteringText={filter} filteringPlaceholder="Find function log events" onChange={({ detail }) => setFilter(detail.filteringText)} />
      <Table loading={loading} items={events.filter(event => String(event.message).toLowerCase().includes(filter.toLowerCase()))} columnDefinitions={[
        { id: 'timestamp', header: 'Timestamp', sortingField: 'timestamp', cell: event => new Date(event.timestamp).toLocaleString() },
        { id: 'message', header: 'Message', cell: event => <code style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{event.message}</code> },
      ]} empty={<div>{filter ? 'No matching log events.' : 'No log events reported for this function.'}</div>} />
    </SpaceBetween>
  </Container>;
}
