import { useEffect, useRef, useState } from 'react';
import { useSession } from '../components/SessionContext';
import Table from '../components/ResourceTable';
import { ResourceDetails, ResourceValue, attributeLabel } from '../components/ResourceDetails';
import { CodeSnippet } from '../components/CodeSnippet';
import { eventDocument, trailLogging, trailPath } from './cloudtrailResources';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Tabs from '@cloudscape-design/components/tabs';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import TextFilter from '@cloudscape-design/components/text-filter';
import FormField from '@cloudscape-design/components/form-field';
import Modal from '@cloudscape-design/components/modal';
import Checkbox from '@cloudscape-design/components/checkbox';
import Textarea from '@cloudscape-design/components/textarea';
import StatusIndicator from '@cloudscape-design/components/status-indicator';

type Props = { resourcePath?: string; onNavigate: (path: string) => void };
function useDetailColumns(): 1 | 2 {
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 600px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 600px)');
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  return narrow ? 1 : 2;
}

async function request(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')?.content || '' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}
export function CloudTrailConsole(props: Props) {
  const { identity, details, loading } = useSession();
  if (!identity && loading) return <StatusIndicator type="loading">Loading session context</StatusIndicator>;
  return <CloudTrailWorkspace key={JSON.stringify([identity, details])} {...props} />;
}
function CloudTrailWorkspace({ resourcePath = 'cloudtrail', onNavigate }: Props) {
  const query = new URLSearchParams(resourcePath.split('?')[1] || '');
  const trailId = query.get('trail') || '';
  const eventId = query.get('event') || '';
  const section = resourcePath.split('?')[0].split('/')[1] || 'events';
  return trailId ? <TrailWorkspace key={trailId} trailId={trailId} tab={query.get('tab') || 'overview'} onNavigate={onNavigate} /> : <CloudTrailInventory key={section} section={section} eventId={eventId} onNavigate={onNavigate} />;
}
function CloudTrailInventory({ section, eventId, onNavigate }: { section: string; eventId: string; onNavigate: Props['onNavigate'] }) {
  const [data, setData] = useState<any>({});
  const [events, setEvents] = useState<any[]>([]);
  const [eventLoading, setEventLoading] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState<any>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [nextToken, setNextToken] = useState('');
  const [attribute, setAttribute] = useState('EventName');
  const [value, setValue] = useState('');
  const [applied, setApplied] = useState({ attribute: '', value: '' });
  const [filter, setFilter] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState(''); const [bucket, setBucket] = useState('');
  // Console defaults: trails are multi-Region, include global service events, and start logging on create.
  const [multi, setMulti] = useState(true); const [global, setGlobal] = useState(true); const [organization, setOrganization] = useState(false); const [startLogging, setStartLogging] = useState(true);
  const [saving, setSaving] = useState(false); const [formError, setFormError] = useState('');
  const generation = useRef(0);
  const load = async (lookup = applied, token = '') => {
    const current = ++generation.current; setLoading(true); setError(''); if (!token) setNextToken('');
    try {
      if (section === 'trails') { const result = await request('/api/cloudtrail/'); if (current === generation.current) setData(result); }
      else {
        const params = new URLSearchParams();
        if (lookup.value) { params.set('attribute', lookup.attribute); params.set('value', lookup.value); }
        if (token) params.set('next_token', token);
        const result = await request(`/api/cloudtrail/events/?${params}`);
        if (current === generation.current) { setEvents(previous => token ? [...previous, ...(result.events || [])] : result.events || []); setNextToken(result.next_token || ''); }
      }
    } catch (error: any) { if (current === generation.current) { setError(error.message); if (!token) { setData({}); setEvents([]); } } }
    finally { if (current === generation.current) setLoading(false); }
  };
  useEffect(() => { load(); return () => { generation.current++; }; }, []);
  useEffect(() => {
    let alive = true; setSelectedEvent(null); setEventLoading(!!eventId);
    if (eventId) request(`/api/cloudtrail/events/?${new URLSearchParams({ attribute: 'EventId', value: eventId })}`).then(result => { if (alive) setSelectedEvent((result.events || []).find((event: any) => event.EventId === eventId) || null); }).catch(error => { if (alive) setError(error.message); }).finally(() => { if (alive) setEventLoading(false); });
    return () => { alive = false; };
  }, [eventId]);
  const activeEvent = events.find(event => event.EventId === eventId) || selectedEvent;
  return <SpaceBetween size="l">
    <Header variant="h1" description="Investigate API activity and configure delivery of trail logs to Amazon S3." actions={<Button iconName="refresh" loading={loading} onClick={() => load()}>Refresh</Button>}>AWS CloudTrail</Header>
    {error && <Alert type="error">{error}</Alert>}
    <Tabs activeTabId={section === 'trails' ? 'trails' : 'events'} onChange={({ detail }) => onNavigate(`cloudtrail/${detail.activeTabId}`)} tabs={[
      { id: 'events', label: 'Event history', content: <SpaceBetween size="m">
        <Alert type="info">Event history reflects the events returned by the current account and Region. Trail logging and S3 delivery are configured separately.</Alert>
        <Container header={<Header variant="h2" counter={`(${events.length}${nextToken ? '+' : ''})`}>Events</Header>}><SpaceBetween size="m">
          <SpaceBetween direction="horizontal" size="s"><FormField label="Lookup attribute"><Select selectedOption={{ label: attribute, value: attribute }} options={['EventName', 'EventId', 'Username', 'EventSource', 'ResourceName', 'ResourceType', 'ReadOnly', 'AccessKeyId'].map(value => ({ label: value, value }))} onChange={({ detail }) => setAttribute(detail.selectedOption.value!)} /></FormField><FormField label="Lookup value"><Input value={value} onChange={({ detail }) => setValue(detail.value)} onKeyDown={({ detail }) => { if (detail.key === 'Enter') { const lookup = { attribute, value: value.trim() }; setApplied(lookup); load(lookup); } }} /></FormField><Button loading={loading} onClick={() => { const lookup = { attribute, value: value.trim() }; setApplied(lookup); load(lookup); }}>Search events</Button><Button disabled={loading} onClick={() => { setValue(''); setApplied({ attribute: '', value: '' }); load({ attribute: '', value: '' }); }}>Clear</Button></SpaceBetween>
          {applied.value && <Box>Applied lookup: {applied.attribute} = {applied.value}</Box>}
          <Table loading={loading} wrapLines={false} items={events} columnDefinitions={[
            { id: 'name', header: 'Event name', cell: (event: any) => <Button variant="inline-link" onClick={() => onNavigate(`cloudtrail/events?${new URLSearchParams({ event: event.EventId })}`)}>{event.EventName || event.EventId || 'Not reported'}</Button> },
            { id: 'time', header: 'Event time', cell: (event: any) => <ResourceValue value={event.EventTime} /> },
            { id: 'user', header: 'User name', cell: (event: any) => <ResourceValue value={event.Username} /> },
            { id: 'source', header: 'Event source', cell: (event: any) => <ResourceValue value={event.EventSource || eventDocument(event).document.eventSource} /> },
            { id: 'resources', header: 'Resources', cell: (event: any) => <ResourceValue value={event.Resources?.map((item: any) => item.ResourceName).join(', ')} /> },
            { id: 'error', header: 'Error code', cell: (event: any) => <ResourceValue value={eventDocument(event).document.errorCode} /> },
          ]} empty={<Box textAlign="center">No events returned for this lookup.</Box>} />
          {nextToken && <Button loading={loading} onClick={() => load(applied, nextToken)}>Load more events</Button>}
        </SpaceBetween></Container>
        {eventId && (activeEvent ? <EventDetails event={activeEvent} /> : eventLoading ? <StatusIndicator type="loading">Loading event record</StatusIndicator> : !loading && <Alert type="warning">Event not found in the active account and Region.</Alert>)}
      </SpaceBetween> },
      { id: 'trails', label: 'Trails', content: <Container header={<Header variant="h2" counter={`(${(data.trails || []).length})`} actions={<Button variant="primary" onClick={() => { setFormError(''); setCreateOpen(true); }}>Create trail</Button>}>Trails</Header>}><SpaceBetween size="m"><TextFilter filteringText={filter} onChange={({ detail }) => setFilter(detail.filteringText)} filteringPlaceholder="Find trails" /><Table loading={loading} items={(data.trails || []).filter((trail: any) => `${trail.name} ${trail.arn} ${trail.s3_bucket_name}`.toLowerCase().includes(filter.toLowerCase()))} columnDefinitions={[
        { id: 'name', header: 'Trail name', cell: (trail: any) => <Button variant="inline-link" onClick={() => onNavigate(trailPath(trail.name || trail.arn))}>{trail.name || trail.arn}</Button> },
        { id: 'logging', header: 'Logging', cell: (trail: any) => trailLogging(trail) },
        { id: 'bucket', header: 'S3 bucket', cell: (trail: any) => <ResourceValue value={trail.s3_bucket_name} /> },
        { id: 'region', header: 'Home Region', cell: (trail: any) => <ResourceValue value={trail.home_region} /> },
        { id: 'multi', header: 'Multi-Region', cell: (trail: any) => <ResourceValue value={trail.is_multi_region_trail} /> },
      ]} empty={<Box textAlign="center">No trails reported.</Box>} /></SpaceBetween></Container> },
    ]} />
    <Modal visible={createOpen} header="Create trail" onDismiss={() => { if (!saving) setCreateOpen(false); }} footer={<SpaceBetween direction="horizontal" size="s"><Button disabled={saving} onClick={() => setCreateOpen(false)}>Cancel</Button><Button variant="primary" loading={saving} onClick={async () => { if (saving) return; setSaving(true); setFormError(''); try { const result = await request('/api/cloudtrail/trails/', 'POST', { name: name.trim(), s3_bucket_name: bucket.trim(), include_global_service_events: global, is_multi_region_trail: multi, is_organization_trail: organization }); if (startLogging) { try { await request(`/api/cloudtrail/trails/${encodeURIComponent(result.name)}/logging/`, 'POST', { enabled: true }); } catch (error: any) { throw new Error(`Trail ${result.name} was created, but logging could not be started: ${error.message} Open the trail from the list to start logging.`); } } setCreateOpen(false); onNavigate(trailPath(result.name)); } catch (error: any) { setFormError(error.message); } finally { setSaving(false); } }}>Create trail</Button></SpaceBetween>}><SpaceBetween size="m">{formError && <Alert type="error">{formError}</Alert>}<Alert type="info">CreateTrail only configures the trail; the console then calls StartLogging, as this form does unless you clear the option. Inspect delivery status afterwards.</Alert><FormField label="Trail name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField><FormField label="S3 bucket name"><Input value={bucket} onChange={({ detail }) => setBucket(detail.value)} /></FormField><Checkbox checked={multi} onChange={({ detail }) => setMulti(detail.checked)}>Multi-Region trail</Checkbox><Checkbox checked={global} onChange={({ detail }) => setGlobal(detail.checked)}>Include global service events</Checkbox><Checkbox checked={organization} onChange={({ detail }) => setOrganization(detail.checked)}>Organization trail</Checkbox><Checkbox checked={startLogging} onChange={({ detail }) => setStartLogging(detail.checked)}>Start logging after the trail is created</Checkbox></SpaceBetween></Modal>
  </SpaceBetween>;
}
function EventDetails({ event }: { event: any }) {
  const columns = useDetailColumns();
  const { document, error } = eventDocument(event);
  return <SpaceBetween size="m">{error && <Alert type="warning">{error}</Alert>}<ResourceDetails columns={columns} title={`Event: ${event.EventName || event.EventId}`} resource={{ event_id: event.EventId, event_time: event.EventTime, event_source: document.eventSource || event.EventSource, identity_type: document.userIdentity?.type, principal_arn: document.userIdentity?.arn, principal_id: document.userIdentity?.principalId, source_ip: document.sourceIPAddress, user_agent: document.userAgent, aws_region: document.awsRegion, read_only: document.readOnly ?? event.ReadOnly, error_code: document.errorCode, error_message: document.errorMessage, resources: event.Resources, request_parameters: document.requestParameters, response_elements: document.responseElements }} /><Container header={<Header variant="h2">Event record</Header>}><CodeSnippet language="json" code={error ? String(event.CloudTrailEvent) : JSON.stringify(document, null, 2)} /></Container></SpaceBetween>;
}
function TrailWorkspace({ trailId, tab, onNavigate }: { trailId: string; tab: string; onNavigate: Props['onNavigate'] }) {
  const columns = useDetailColumns();
  const [trail, setTrail] = useState<any>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [selectors, setSelectors] = useState<any>(null); const [selectorError, setSelectorError] = useState('');
  const [revision, setRevision] = useState(0); const [saving, setSaving] = useState(false); const lock = useRef(false);
  const [message, setMessage] = useState(''); const [modal, setModal] = useState(''); const [formError, setFormError] = useState('');
  const [bucket, setBucket] = useState(''); const [multi, setMulti] = useState(false); const [global, setGlobal] = useState(false); const [selectorText, setSelectorText] = useState('');
  const path = `/api/cloudtrail/trails/${encodeURIComponent(trailId)}/`;
  useEffect(() => {
    let alive = true; setLoading(true); setError(''); setSelectorError(''); setSelectors(null);
    request('/api/cloudtrail/').then(result => { if (alive) setTrail((result.trails || []).find((item: any) => item.name === trailId || item.arn === trailId) || null); }).catch(error => { if (alive) { setTrail(null); setError(error.message); } }).finally(() => { if (alive) setLoading(false); });
    request(`${path}selectors/`).then(result => { if (alive) setSelectors(result); }).catch(error => { if (alive) setSelectorError(error.message); });
    return () => { alive = false; };
  }, [revision, path, trailId]);
  const open = (kind: string) => { setFormError(''); setBucket(trail?.s3_bucket_name || ''); setMulti(trail?.is_multi_region_trail === true); setGlobal(trail?.include_global_service_events === true); setSelectorText(JSON.stringify(selectors?.AdvancedEventSelectors?.length ? { AdvancedEventSelectors: selectors.AdvancedEventSelectors } : { EventSelectors: selectors?.EventSelectors || [] }, null, 2)); setModal(kind); };
  const save = async () => {
    if (lock.current) return; lock.current = true; setSaving(true); setFormError('');
    try {
      if (modal === 'delete') { await request(path, 'DELETE'); onNavigate('cloudtrail/trails'); return; }
      if (modal === 'logging') await request(`${path}logging/`, 'POST', { enabled: !trail.status.IsLogging });
      if (modal === 'edit') { if (!bucket.trim()) throw new Error('S3 bucket name is required.'); await request(path, 'PATCH', { s3_bucket_name: bucket.trim(), ...(typeof trail.is_multi_region_trail === 'boolean' ? { is_multi_region_trail: multi } : {}), ...(typeof trail.include_global_service_events === 'boolean' ? { include_global_service_events: global } : {}) }); }
      if (modal === 'selectors') { let value; try { value = JSON.parse(selectorText); } catch { throw new Error('Enter valid JSON for the event selectors.'); } if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Enter a selector JSON object.'); await request(`${path}selectors/`, 'PUT', value); }
      setMessage('Change accepted. Review the refreshed configuration and delivery status.'); setModal(''); setRevision(value => value + 1);
    } catch (error: any) { setFormError(error.message); }
    finally { lock.current = false; setSaving(false); }
  };
  return <SpaceBetween size="l"><Button variant="link" onClick={() => onNavigate('cloudtrail/trails')}>Trails</Button><Header variant="h1" description="Review collection settings and reported log delivery independently." actions={<SpaceBetween direction="horizontal" size="xs"><Button iconName="refresh" loading={loading} onClick={() => setRevision(value => value + 1)}>Refresh</Button>{trail && <SpaceBetween direction="horizontal" size="xs"><Button disabled={loading || trail.status?.IsLogging == null} onClick={() => open('logging')}>{trail.status?.IsLogging === true ? 'Stop logging' : 'Start logging'}</Button><Button disabled={loading} onClick={() => open('edit')}>Edit trail</Button><Button disabled={loading} onClick={() => open('delete')}>Delete trail</Button></SpaceBetween>}</SpaceBetween>}>Trail: {trailId}</Header>
    {message && <Alert type="success" dismissible onDismiss={() => setMessage('')}>{message}</Alert>}{error && <Alert type="error">{error}</Alert>}
    {loading && <StatusIndicator type="loading">Loading trail</StatusIndicator>}
    {!loading && !error && !trail && <Alert type="warning">Trail not found in the active account and Region.</Alert>}
    {!loading && trail && <Tabs activeTabId={tab} onChange={({ detail }) => onNavigate(trailPath(trailId, detail.activeTabId))} tabs={[
      { id: 'overview', label: 'Overview', content: <SpaceBetween size="m">
        {trail.status_details && <Alert type="error">Logging status unavailable: {trail.status_details.error || JSON.stringify(trail.status_details)}</Alert>}
        <ResourceDetails columns={columns} title="Trail configuration" resource={{ name: trail.name, arn: trail.arn, logging: trailLogging(trail), s3_bucket_name: trail.s3_bucket_name, home_region: trail.home_region, multi_region: trail.is_multi_region_trail, global_service_events: trail.include_global_service_events, organization_trail: trail.is_organization_trail }} />
        <Container header={<Header variant="h2">Log delivery</Header>}><SpaceBetween size="m"><Alert type="info">Logging enabled indicates collection state. Inspect delivery timestamps and errors to assess delivery to the destination.</Alert>{['LatestDeliveryError', 'LatestNotificationError', 'LatestCloudWatchLogsDeliveryError', 'LatestDigestDeliveryError'].filter(key => trail.status?.[key]).map(key => <Alert key={key} type="error" header={attributeLabel(key)}>{trail.status[key]}</Alert>)}<ResourceDetails columns={columns} title="Reported delivery status" resource={{ latest_delivery_time: trail.status?.LatestDeliveryTime, latest_notification_time: trail.status?.LatestNotificationTime, latest_cloudwatch_logs_delivery_time: trail.status?.LatestCloudWatchLogsDeliveryTime, latest_digest_delivery_time: trail.status?.LatestDigestDeliveryTime, start_logging_time: trail.status?.StartLoggingTime, stop_logging_time: trail.status?.StopLoggingTime }} /></SpaceBetween></Container>
      </SpaceBetween> },
      { id: 'selectors', label: 'Event selectors', content: <Container header={<Header variant="h2" description="Selectors control events collected by this trail. Event history is a separate lookup." actions={<Button disabled={!selectors || !!selectorError} onClick={() => open('selectors')}>Edit event selectors</Button>}>Event selectors</Header>}><SpaceBetween size="m">{selectorError && <Alert type="error">{selectorError}</Alert>}{selectors ? <><Box>{selectors.AdvancedEventSelectors?.length ? 'Advanced event selectors' : 'Basic event selectors'}</Box>{(selectors.EventSelectors || []).map((selector: any, index: number) => <ResourceDetails columns={columns} key={index} title={`Selector ${index + 1}`} resource={{ read_write_type: selector.ReadWriteType, include_management_events: selector.IncludeManagementEvents, excluded_management_sources: selector.ExcludeManagementEventSources, data_resources: selector.DataResources }} />)}{(selectors.AdvancedEventSelectors || []).map((selector: any, index: number) => <ResourceDetails columns={columns} key={index} title={selector.Name || `Selector ${index + 1}`} resource={selector} />)}{!selectors.EventSelectors?.length && !selectors.AdvancedEventSelectors?.length && <Box>No event selectors returned.</Box>}<CodeSnippet language="json" code={JSON.stringify(selectors, null, 2)} /></> : !selectorError && <StatusIndicator type="loading">Loading event selectors</StatusIndicator>}</SpaceBetween></Container> },
    ]} />}
    <Modal visible={!!modal} size={modal === 'selectors' ? 'large' : 'medium'} header={{ logging: trail?.status?.IsLogging ? 'Stop logging' : 'Start logging', edit: 'Edit trail', delete: 'Delete trail', selectors: 'Edit event selectors' }[modal] || ''} onDismiss={() => { if (!saving) setModal(''); }} footer={<SpaceBetween direction="horizontal" size="s"><Button disabled={saving} onClick={() => setModal('')}>Cancel</Button><Button variant="primary" loading={saving} onClick={save}>{modal === 'delete' ? 'Delete trail' : modal === 'logging' ? 'Confirm' : 'Save changes'}</Button></SpaceBetween>}><SpaceBetween size="m">{formError && <Alert type="error">{formError}</Alert>}{modal === 'delete' && <Box>Delete trail {trailId}? Previously delivered S3 logs remain in the destination bucket.</Box>}{modal === 'logging' && <Box>{trail?.status?.IsLogging ? 'Stop collection of new events for this trail?' : 'Start collection of events selected by this trail?'} Check delivery status after changing logging.</Box>}{modal === 'edit' && <><FormField label="S3 bucket name"><Input value={bucket} onChange={({ detail }) => setBucket(detail.value)} /></FormField><Checkbox disabled={typeof trail?.is_multi_region_trail !== 'boolean'} checked={multi} onChange={({ detail }) => setMulti(detail.checked)}>Multi-Region trail</Checkbox><Checkbox disabled={typeof trail?.include_global_service_events !== 'boolean'} checked={global} onChange={({ detail }) => setGlobal(detail.checked)}>Include global service events</Checkbox></>}{modal === 'selectors' && <><Alert type="warning">Saving replaces this trail’s event selectors. Provide either EventSelectors or AdvancedEventSelectors.</Alert><FormField label="Event selector JSON"><Textarea rows={16} value={selectorText} onChange={({ detail }) => setSelectorText(detail.value)} /></FormField></>}</SpaceBetween></Modal>
  </SpaceBetween>;
}
