import { useEffect, useRef, useState } from 'react';
import Table from '../components/ResourceTable';
import { ResourceValue } from '../components/ResourceDetails';
import { CodeSnippet } from '../components/CodeSnippet';
import { FlociNote } from '../components/FlociNote';
import { logRecordUser, logRecordResources } from './cloudtrailResources';
import Header from '@cloudscape-design/components/header';
import Container from '@cloudscape-design/components/container';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import TextFilter from '@cloudscape-design/components/text-filter';
import StatusIndicator from '@cloudscape-design/components/status-indicator';

async function get(path: string) {
  const response = await fetch(path);
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.error) throw new Error(result.error || `Request failed (${response.status})`);
  return result;
}

const formatSize = (bytes: unknown) => typeof bytes === 'number' ? (bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`) : undefined;

/** Lists the gzipped log files a trail delivered to S3 and shows the records in one of them. */
export function CloudTrailLogFiles({ trailId }: { trailId: string }) {
  const base = `/api/cloudtrail/trails/${encodeURIComponent(trailId)}/log-files/`;
  const [listing, setListing] = useState<any>(null);
  const [files, setFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [file, setFile] = useState<any>(null);
  const [records, setRecords] = useState<any[] | null>(null);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [recordsError, setRecordsError] = useState('');
  const [selected, setSelected] = useState<any[]>([]);
  const [filter, setFilter] = useState('');
  const listGeneration = useRef(0);
  const readGeneration = useRef(0);

  const load = async (before = '') => {
    const current = ++listGeneration.current; setLoading(true); setError('');
    try {
      const result = await get(`${base}${before ? `?${new URLSearchParams({ before })}` : ''}`);
      if (current !== listGeneration.current) return;
      setListing(result); setFiles(previous => before ? [...previous, ...(result.files || [])] : result.files || []);
    } catch (error: any) { if (current === listGeneration.current) { setError(error.message); if (!before) { setListing(null); setFiles([]); } } }
    finally { if (current === listGeneration.current) setLoading(false); }
  };
  useEffect(() => { load(); return () => { listGeneration.current++; readGeneration.current++; }; }, [base]);

  const open = async (item: any) => {
    const current = ++readGeneration.current;
    setFile(item); setRecords(null); setRecordsError(''); setSelected([]); setFilter(''); setRecordsLoading(true);
    try { const result = await get(`${base}read/?${new URLSearchParams({ key: item.key })}`); if (current === readGeneration.current) setRecords(result.records || []); }
    catch (error: any) { if (current === readGeneration.current) setRecordsError(error.message); }
    finally { if (current === readGeneration.current) setRecordsLoading(false); }
  };

  const shownRecords = (records || []).filter(record => !filter || JSON.stringify(record).toLowerCase().includes(filter.toLowerCase()));
  const location = listing ? `s3://${listing.bucket}/${listing.log_prefix}` : '';
  return <SpaceBetween size="m">
    <FlociNote>Floci delivers only S3 data events: object calls (PutObject, GetObject, HeadObject, DeleteObject, ListObjects, GetObjectAcl) on buckets and keys that match this trail’s event selectors, recorded while the trail is logging. It records no management events. Buffered records are written to the trail bucket as one gzipped JSON file about every 60 seconds by default; AWS delivers about every 5 minutes.</FlociNote>
    <Container header={<Header variant="h2" counter={listing ? `(${files.length}${listing.next_cursor ? '+' : ''})` : undefined} description={location ? `Newest first, from ${location}` : 'Log files the trail delivered to its S3 bucket, newest first.'} actions={<Button iconName="refresh" loading={loading} onClick={() => load()}>Refresh</Button>}>Log files</Header>}>
      <SpaceBetween size="m">
        {error && <Alert type="error" header="Log files could not be listed">{error}</Alert>}
        {(listing?.load_errors || []).map((item: any, index: number) => <Alert key={index} type="warning" header="List incomplete">{item.error}</Alert>)}
        <Table loading={loading && !files.length} loadingText="Listing log files" trackBy="key" wrapLines={false} items={files} columnDefinitions={[
          { id: 'name', header: 'Log file', cell: (item: any) => <Button variant="inline-link" onClick={() => open(item)}>{item.name}</Button> },
          { id: 'region', header: 'Region', cell: (item: any) => <ResourceValue value={item.region} /> },
          { id: 'last_modified', header: 'Delivered', cell: (item: any) => <ResourceValue value={item.last_modified} /> },
          { id: 'size', header: 'Size', cell: (item: any) => formatSize(item.size) ?? <ResourceValue value={item.size} /> },
        ]} empty={<Box textAlign="center">{error ? 'Log files unavailable.' : 'No log files delivered yet. Start logging, make S3 object calls that match the event selectors, then refresh after the next flush.'}</Box>} />
        {listing?.next_cursor && <Button loading={loading} onClick={() => load(listing.next_cursor)}>Load older log files</Button>}
      </SpaceBetween>
    </Container>
    {file && <Container header={<Header variant="h2" counter={records ? `(${records.length})` : undefined} description={file.key}>Records in {file.name}</Header>}>
      <SpaceBetween size="m">
        {recordsError && <Alert type="error" header="Log file could not be read">{recordsError}</Alert>}
        {recordsLoading && <StatusIndicator type="loading">Reading log file</StatusIndicator>}
        {records && <>
          <TextFilter filteringText={filter} onChange={({ detail }) => { setFilter(detail.filteringText); setSelected([]); }} filteringPlaceholder="Find records" countText={`${shownRecords.length} matches`} />
          <Table selectionType="single" trackBy={(record: any) => record.eventID || JSON.stringify(record)} selectedItems={selected} onSelectionChange={({ detail }) => setSelected(detail.selectedItems)} wrapLines={false} items={shownRecords} ariaLabels={{ itemSelectionLabel: (_data, record: any) => `Select ${record.eventName || 'record'} at ${record.eventTime || ''}`, selectionGroupLabel: 'Records' }} columnDefinitions={[
            { id: 'eventTime', header: 'Event time', cell: (record: any) => <ResourceValue value={record.eventTime} />, sortingField: 'eventTime' },
            { id: 'eventName', header: 'Event name', cell: (record: any) => record.eventName ?? <ResourceValue value={undefined} />, sortingField: 'eventName' },
            { id: 'eventSource', header: 'Event source', cell: (record: any) => <ResourceValue value={record.eventSource} /> },
            { id: 'user', header: 'User', cell: (record: any) => <ResourceValue value={logRecordUser(record)} /> },
            { id: 'sourceIPAddress', header: 'Source IP address', cell: (record: any) => <ResourceValue value={record.sourceIPAddress} /> },
            { id: 'resources', header: 'Resources', cell: (record: any) => <ResourceValue value={logRecordResources(record)} /> },
            { id: 'errorCode', header: 'Error code', cell: (record: any) => <ResourceValue value={record.errorCode} /> },
          ]} empty={<Box textAlign="center">{records.length ? 'No records match the filter.' : 'This log file has no records.'}</Box>} />
          {selected[0] ? <Container header={<Header variant="h3">Record: {selected[0].eventName || selected[0].eventID}</Header>}><CodeSnippet language="json" code={JSON.stringify(selected[0], null, 2)} /></Container> : <Box color="text-body-secondary">Select a record to see its raw JSON.</Box>}
        </>}
      </SpaceBetween>
    </Container>}
  </SpaceBetween>;
}
