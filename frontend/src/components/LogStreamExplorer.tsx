import { useEffect, useState } from 'react';
import Select from '@cloudscape-design/components/select';
import FormField from '@cloudscape-design/components/form-field';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import Button from '@cloudscape-design/components/button';
import Alert from '@cloudscape-design/components/alert';
import Table from './ResourceTable';
import { fetchCloudWatchLogs } from '../api/client';

export function LogStreamExplorer({ groupName, revision = 0 }: { groupName: string; revision?: number }) {
  const [streams, setStreams] = useState<any[]>([]);
  const [stream, setStream] = useState('');
  const [events, setEvents] = useState<any[]>([]);
  const [filter, setFilter] = useState('');
  const [loadingStreams, setLoadingStreams] = useState(false);
  const [loadingEvents, setLoadingEvents] = useState(false);
  const [streamError, setStreamError] = useState('');
  const [eventError, setEventError] = useState('');
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    setStreamError(''); setStreams([]); setStream(''); setEvents([]); setLoadingStreams(true);
    fetchCloudWatchLogs('log-streams', { log_group_name: groupName }).then(data => {
      if (!alive) return;
      setStreams(data.streams || []); setStream(data.streams?.[0]?.logStreamName || '');
    }).catch(error => { if (alive) setStreamError(error.message); }).finally(() => { if (alive) setLoadingStreams(false); });
    return () => { alive = false; };
  }, [groupName, revision, refresh]);
  useEffect(() => {
    let alive = true;
    setEvents([]); setEventError(''); setLoadingEvents(false);
    if (!stream) return;
    setLoadingEvents(true);
    fetchCloudWatchLogs('log-events', { log_group_name: groupName, log_stream_name: stream }).then(data => {
      if (alive) setEvents(data.events || []);
    }).catch(error => { if (alive) setEventError(error.message); }).finally(() => { if (alive) setLoadingEvents(false); });
    return () => { alive = false; };
  }, [groupName, stream]);
  return <SpaceBetween size="m">
    <Button iconName="refresh" loading={loadingStreams || loadingEvents} onClick={() => setRefresh(value => value + 1)}>Refresh logs</Button>
    {(streamError || eventError) && <Alert type="error">{streamError || eventError}</Alert>}
    <FormField label="Log stream"><Select ariaLabel="Log stream" statusType={loadingStreams ? 'loading' : 'finished'} loadingText="Loading log streams"
      selectedOption={stream ? { value: stream, label: stream } : null} options={streams.map(item => ({ value: item.logStreamName, label: item.logStreamName }))}
      placeholder="Select a log stream" empty="No log streams in this group" onChange={({ detail }) => setStream(detail.selectedOption.value!)} /></FormField>
    <TextFilter filteringText={filter} filteringPlaceholder="Filter log messages" onChange={({ detail }) => setFilter(detail.filteringText)} />
    <Table items={events.filter(event => String(event.message).toLowerCase().includes(filter.toLowerCase()))} loading={loadingEvents} loadingText="Loading log events"
      columnDefinitions={[
        { id: 'timestamp', header: 'Timestamp', cell: event => new Date(event.timestamp).toLocaleString() },
        { id: 'message', header: 'Message', cell: event => <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: 'monospace' }}>{event.message}</span> },
      ]} empty={filter ? 'No log events match your filter.' : stream ? 'No events in this log stream.' : 'Select a log stream to inspect its events.'} />
  </SpaceBetween>;
}
