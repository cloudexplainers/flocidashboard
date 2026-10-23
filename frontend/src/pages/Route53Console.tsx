import React, { useState, useEffect, useRef } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Select from '@cloudscape-design/components/select';
import Textarea from '@cloudscape-design/components/textarea';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Tabs from '@cloudscape-design/components/tabs';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import { deleteConsoleResource, fetchConsoleInventory, postConsoleAction } from '../api/client';
import ConfirmActionModal from '../components/ConfirmActionModal';

interface Route53ConsoleProps { resourcePath?: string; onNavigate?: (path: string) => void; }
const zoneId = (zone: any) => String(zone.Id || zone.id || '').replace('/hostedzone/', '');
const routingPolicy = (record: any) => record.Failover ? `Failover · ${record.Failover}` : record.Weight != null ? `Weighted · ${record.Weight}` : record.Region ? `Latency · ${record.Region}` : record.GeoLocation ? 'Geolocation' : record.GeoProximityLocation ? 'Geoproximity' : record.CidrRoutingConfig ? 'IP-based' : record.MultiValueAnswer ? 'Multivalue answer' : 'Simple';
export const Route53Console: React.FC<Route53ConsoleProps> = ({ resourcePath = 'route53', onNavigate }) => {
  const [data, setData] = useState<any>({ hosted_zones: [] });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [filterText, setFilterText] = useState('');
  const [recordFilter, setRecordFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [localZone, setLocalZone] = useState('');
  const [localTab, setLocalTab] = useState('records');
  const generation = useRef(0);
  const route = new URLSearchParams(resourcePath.split('?')[1] || '');
  const selectedId = onNavigate ? route.get('zone') || '' : localZone;
  const tab = onNavigate ? route.get('tab') || 'records' : localTab;
  const selectZone = (id: string, panel = 'records') => {
    setLocalZone(id); setLocalTab(panel);
    onNavigate?.(`route53?${new URLSearchParams({ zone: id, tab: panel })}`);
  };
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [recordFormError, setRecordFormError] = useState('');
  const [editingRecord, setEditingRecord] = useState<any>(null);

  const [pendingDelete, setPendingDelete] = useState<{ kind: 'zone' } | { kind: 'record'; record: any } | null>(null);
  const [deleting, setDeleting] = useState(false);
  // Create Zone Modal
  const [createZoneOpen, setCreateZoneOpen] = useState(false);
  const [zoneName, setZoneName] = useState('');
  const [zoneComment, setZoneComment] = useState('');
  const [creatingZone, setCreatingZone] = useState(false);

  // Create Record Modal
  const [createRecordOpen, setCreateRecordOpen] = useState(false);
  const [recordName, setRecordName] = useState('');
  const [recordType, setRecordType] = useState({ label: 'A - Routes traffic to an IPv4 address', value: 'A' });
  const [recordTtl, setRecordTtl] = useState('300');
  const [recordValue, setRecordValue] = useState('192.0.2.1');
  const [creatingRecord, setCreatingRecord] = useState(false);

  const loadData = async () => {
    const request = ++generation.current;
    setLoading(true); setLoadError('');
    try {
      const res = await fetchConsoleInventory('route53');
      if (request === generation.current) setData(res);
    } catch (err) {
      if (request === generation.current) { setLoadError(err instanceof Error ? err.message : 'Unable to load hosted zones'); setData({ hosted_zones: [] }); }
    } finally {
      if (request === generation.current) setLoading(false);
    }
  };
  useEffect(() => { loadData(); return () => { generation.current += 1; }; }, []);
  useEffect(() => { setRecordFilter(''); setTypeFilter('all'); setCreateRecordOpen(false); setEditingRecord(null); }, [selectedId]);

  const handleCreateZone = async () => {
    if (!zoneName.trim()) return;
    setCreatingZone(true);
    setActionMessage(null);
    try {
      const result = await postConsoleAction('/api/route53/hosted-zones/', {
        name: zoneName.trim(),
        caller_reference: `floci-${Date.now()}`,
        comment: zoneComment,
      });
      setActionMessage({ type: 'success', text: `Hosted zone "${zoneName.trim()}" created.` });
      setCreateZoneOpen(false);
      setZoneName('');
      setZoneComment('');
      await loadData();
      if (result.hosted_zone) selectZone(zoneId(result.hosted_zone));
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create hosted zone' });
    } finally {
      setCreatingZone(false);
    }
  };

  const handleDelete = async () => {
    if (!activeZone || !pendingDelete) return;
    setDeleting(true);
    try {
      if (pendingDelete.kind === 'zone') {
        await deleteConsoleResource(`/api/route53/hosted-zones/${encodeURIComponent(activeZone.Id)}/`, 'Failed to delete hosted zone');
        setActionMessage({ type: 'success', text: `Hosted zone "${activeZone.Name}" deleted.` });
        selectZone('');
      } else {
        const result = await postConsoleAction(`/api/route53/hosted-zones/${encodeURIComponent(activeZone.Id)}/record-sets/`, { action: 'DELETE', record_set: pendingDelete.record });
        setActionMessage({ type: 'success', text: `Record "${pendingDelete.record.Name}" (${pendingDelete.record.Type}) deleted. Change status: ${result.change_info?.Status || 'Not reported'}.` });
      }
      setPendingDelete(null);
      await loadData();
    } catch (err: any) {
      setPendingDelete(null);
      setActionMessage({ type: 'error', text: err.message || 'Delete failed' });
    } finally {
      setDeleting(false);
    }
  };

  const handleCreateRecord = async () => {
    if (!activeZone || !recordName.trim()) return;
    const ttl = Number(recordTtl);
    const values = recordValue.split('\n').map(value => value.trim()).filter(Boolean);
    if (!editingRecord?.AliasTarget && (!Number.isInteger(ttl) || ttl < 0 || recordTtl.trim() === '' || !values.length)) {
      setRecordFormError('Enter a non-negative integer TTL and at least one record value.'); return;
    }
    const root = activeZone.Name.replace(/\.$/, '');
    const entered = recordName.trim();
    const name = entered === '@' ? root : entered.endsWith('.') || entered === root || entered.endsWith(`.${root}`) ? entered : `${entered}.${root}`;
    const fullName = name.endsWith('.') ? name : `${name}.`;
    setCreatingRecord(true);
    try {
      const record = editingRecord?.AliasTarget ? editingRecord : {
        ...(editingRecord || {}), Name: fullName, Type: recordType.value, TTL: ttl,
        ResourceRecords: values.map(Value => ({ Value })),
      };
      const result = await postConsoleAction(`/api/route53/hosted-zones/${encodeURIComponent(activeZone.Id)}/record-sets/`, {
        action: editingRecord ? 'UPSERT' : 'CREATE', record_set: record,
      });
      setActionMessage({ type: 'success', text: `Record "${fullName}" ${editingRecord ? 'updated' : 'created'}. Change status: ${result.change_info?.Status || 'Not reported'}.` });
      setCreateRecordOpen(false);
      setRecordName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create record' });
    } finally {
      setCreatingRecord(false);
    }
  };

  const zonesList = (data.hosted_zones || []).map((z: any) => ({
    ...z, Id: zoneId(z), Name: z.Name || z.name,
    PrivateZone: z.Config?.PrivateZone ?? z.private_zone,
    RecordCount: z.ResourceRecordSetCount ?? z.resource_record_set_count ?? z.record_count,
    Comment: z.Config?.Comment || z.comment || '—',
  }));
  const filteredZones = zonesList.filter((z: any) => `${z.Name} ${z.Id} ${z.Comment}`.toLowerCase().includes(filterText.toLowerCase()));
  const activeZone = zonesList.find((zone: any) => zone.Id === selectedId);
  const records: any[] = activeZone?.records || [];
  const filteredRecords = records.filter(record => (typeFilter === 'all' || record.Type === typeFilter) && `${routingPolicy(record)} ${JSON.stringify(record)}`.toLowerCase().includes(recordFilter.toLowerCase()));
  const openRecord = (record?: any) => {
    setRecordFormError('');
    setEditingRecord(record || null); setRecordName(record?.Name || '');
    setRecordType({ label: record?.Type || 'A', value: record?.Type || 'A' });
    setRecordTtl(String(record?.TTL ?? 300));
    setRecordValue((record?.ResourceRecords || []).map((value: any) => value.Value).join('\n'));
    setCreateRecordOpen(true);
  };

  return (
    <SpaceBetween size="l">
      {/* Header */}
      <Container
        header={
          <Header
            variant="h1"
            description="Highly available and scalable Domain Name System (DNS) web service simulated locally."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateZoneOpen(true)}>
                  Create hosted zone
                </Button>
              </SpaceBetween>
            }
          >
            Amazon Route 53
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
          <Box padding="m"><Box variant="awsui-key-label">Hosted zones</Box><Box variant="h2">{zonesList.length}</Box></Box>
          <Box padding="m"><Box variant="awsui-key-label">Scope</Box><Box variant="h2">Global · local endpoint</Box></Box>
          <Box padding="m"><Box variant="awsui-key-label">Inventory</Box><StatusIndicator type={loading ? 'loading' : loadError ? 'error' : 'info'}>{loading ? 'Loading zones' : loadError ? 'Unavailable' : 'Retrieved from local endpoint'}</StatusIndicator></Box>
        </div>
        {loadError && <Alert type="error">{loadError}</Alert>}
      </Container>

      {/* Hosted Zones Table */}
      <Container
        header={
          <Header
            variant="h2"
            description="Public and private DNS hosted zones."
          >
            Hosted Zones ({zonesList.length})
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter hosted zones by domain..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />

          <Table
            columnDefinitions={[
              {
                id: 'name',
                header: 'Hosted zone name',
                cell: (item) => <Button variant="inline-link" onClick={() => selectZone(item.Id)}>{item.Name}</Button>,
              },
              {
                id: 'id',
                header: 'Hosted zone ID',
                cell: (item) => <code>{item.Id}</code>,
                width: 220,
              },
              {
                id: 'type',
                header: 'Type',
                cell: (item) => <Badge color={item.PrivateZone === true ? 'blue' : 'grey'}>{item.PrivateZone === true ? 'Private' : item.PrivateZone === false ? 'Public' : 'Not reported'}</Badge>,
                width: 120,
              },
              {
                id: 'records',
                header: 'Record count',
                cell: (item) => item.RecordCount ?? 'Not reported',
                width: 140,
              },
              {
                id: 'comment',
                header: 'Description',
                cell: (item) => item.Comment,
              },
            ]}
            items={filteredZones}
            selectionType="single"
            loading={loading}
            selectedItems={activeZone ? [activeZone] : []}
            onSelectionChange={({ detail }) => selectZone(detail.selectedItems[0]?.Id || '')}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No hosted zones found</b>
                <p>Create a hosted zone to configure DNS routing.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {selectedId && !loading && !loadError && !activeZone && <Alert type="warning">Requested hosted zone was not found. Select an available zone.</Alert>}
      {activeZone && <Container header={<Header variant="h2" description={`Hosted zone · ${activeZone.Id}`} actions={<SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setPendingDelete({ kind: 'zone' })}>Delete zone</Button><Button onClick={() => selectZone('')}>Close zone</Button></SpaceBetween>}>{activeZone.Name}</Header>}>
        <SpaceBetween size="l">
          <KeyValuePairs columns={3} items={[
            { label: 'Zone type', value: activeZone.PrivateZone === true ? 'Private' : activeZone.PrivateZone === false ? 'Public' : 'Not reported' },
            { label: 'Record count', value: activeZone.RecordCount ?? 'Not reported' },
            { label: 'Description', value: activeZone.Comment },
          ]} />
          <Tabs activeTabId={tab} onChange={({ detail }) => selectZone(activeZone.Id, detail.activeTabId)} tabs={[
            { id: 'records', label: `Records (${records.length})`, content: <SpaceBetween size="m">
              <Header variant="h3" actions={<Button variant="primary" onClick={() => openRecord()}>Create record</Button>}>DNS records</Header>
              <TextFilter filteringText={recordFilter} filteringPlaceholder="Find records by name, value, or routing policy…" onChange={({ detail }) => setRecordFilter(detail.filteringText)} />
              <Select ariaLabel="Filter record type" selectedOption={{ value: typeFilter, label: typeFilter === 'all' ? 'All record types' : typeFilter }} options={[{ value: 'all', label: 'All record types' }, ...[...new Set(records.map(record => record.Type))].map(value => ({ value, label: value }))]} onChange={({ detail }) => setTypeFilter(detail.selectedOption.value!)} />
              <Table items={filteredRecords} columnDefinitions={[
                { id: 'Name', header: 'Record name', cell: (record: any) => <strong>{record.Name}</strong> },
                { id: 'Type', header: 'Type', cell: (record: any) => record.Type },
                { id: 'alias', header: 'Alias', cell: (record: any) => record.AliasTarget ? 'Yes' : 'No' },
                { id: 'TTL', header: 'TTL (seconds)', cell: (record: any) => record.AliasTarget ? 'Managed by target' : record.TTL ?? 'Not reported' },
                { id: 'routing', header: 'Routing policy', cell: routingPolicy },
                { id: 'value', header: 'Value / route traffic to', cell: (record: any) => <div style={{ overflowWrap: 'anywhere' }}>{record.AliasTarget?.DNSName || (record.ResourceRecords || []).map((value: any) => value.Value).join('\n') || 'Not reported'}</div> },
                { id: 'edit', header: 'Actions', cell: (record: any) => {
                  // The zone apex NS and SOA records are managed by Route 53 and can't be deleted.
                  const apexRecord = ['NS', 'SOA'].includes(record.Type) && record.Name === activeZone.Name;
                  return <SpaceBetween direction="horizontal" size="xs">
                    <Button disabled={Boolean(record.AliasTarget)} onClick={() => openRecord(record)}>Edit</Button>
                    <Button disabled={apexRecord} onClick={() => setPendingDelete({ kind: 'record', record })}>Delete</Button>
                  </SpaceBetween>;
                } },
              ]} empty={<Box textAlign="center">{recordFilter || typeFilter !== 'all' ? 'No records match your filters.' : 'No records reported for this hosted zone.'}</Box>} />
              <Box variant="small">Alias targets and routing policies are shown as returned by the endpoint. Editing alias records is unavailable here.</Box>
            </SpaceBetween> },
            { id: 'details', label: 'Zone details', content: <KeyValuePairs columns={2} items={[
              { label: 'Hosted zone ID', value: activeZone.Id },
              { label: 'Caller reference', value: activeZone.caller_reference || 'Not reported' },
              { label: 'Name servers', value: activeZone.delegation_set?.NameServers?.join(' · ') || 'Not reported' },
              { label: 'Associated VPCs', value: activeZone.vpcs?.map((vpc: any) => `${vpc.VPCId} (${vpc.VPCRegion})`).join(' · ') || 'None reported' },
            ]} /> },
          ]} />
        </SpaceBetween>
      </Container>}

      <ConfirmActionModal
        visible={Boolean(pendingDelete)}
        onDismiss={() => !deleting && setPendingDelete(null)}
        header={pendingDelete?.kind === 'zone' ? 'Delete hosted zone' : 'Delete record'}
        confirmLabel="Delete"
        confirmationText={pendingDelete?.kind === 'zone' ? 'delete' : undefined}
        loading={deleting}
        onConfirm={handleDelete}
      >
        {pendingDelete?.kind === 'zone'
          ? <>Delete <strong>{activeZone?.Name}</strong>? Route 53 deletes a hosted zone only after you delete every record except the default NS and SOA records.</>
          : pendingDelete?.kind === 'record'
            ? <>Delete the {pendingDelete.record.Type} record <strong>{pendingDelete.record.Name}</strong>{pendingDelete.record.SetIdentifier ? ` (${pendingDelete.record.SetIdentifier})` : ''}? DNS queries stop resolving it once the change propagates.</>
            : null}
      </ConfirmActionModal>

      {/* Create Hosted Zone Modal */}
      <Modal
        visible={createZoneOpen}
        onDismiss={() => setCreateZoneOpen(false)}
        header="Create hosted zone"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateZoneOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!zoneName.trim()} loading={creatingZone} onClick={handleCreateZone}>
                Create hosted zone
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Domain name" description="The domain name you want to route traffic for, such as example.com.">
            <Input
              value={zoneName}
              onChange={({ detail }) => setZoneName(detail.value)}
              placeholder="example.com"
            />
          </FormField>

          <FormField label="Description - optional" description="This value lets you distinguish hosted zones that have the same name.">
            <Input
              value={zoneComment}
              onChange={({ detail }) => setZoneComment(detail.value)}
              placeholder="Primary production DNS zone"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Record Set Modal */}
      <Modal
        visible={createRecordOpen}
        onDismiss={() => setCreateRecordOpen(false)}
        header={`${editingRecord ? 'Edit record' : 'Create record'} in ${activeZone?.Name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateRecordOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!recordName.trim()} loading={creatingRecord} onClick={handleCreateRecord}>
                {editingRecord ? 'Save' : 'Create records'}
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {recordFormError && <Alert type="error">{recordFormError}</Alert>}
          <FormField label="Record name" description={`Use @ for the zone apex, a relative name, or a fully qualified name ending in a dot.`}>
            <Input
              disabled={Boolean(editingRecord)}
              value={recordName}
              onChange={({ detail }) => setRecordName(detail.value)}
              placeholder="api or www"
            />
          </FormField>

          <FormField label="Record type">
            <Select
              disabled={Boolean(editingRecord)}
              selectedOption={recordType}
              onChange={({ detail }) => setRecordType(detail.selectedOption as any)}
              options={[
                { label: 'A - Routes traffic to an IPv4 address', value: 'A' },
                { label: 'AAAA - Routes traffic to an IPv6 address', value: 'AAAA' },
                { label: 'CNAME - Routes traffic to another domain name', value: 'CNAME' },
                { label: 'TXT - Verification and SPF records', value: 'TXT' },
                { label: 'MX - Specifies mail servers', value: 'MX' },
                { label: 'NS - Name servers for a hosted zone', value: 'NS' },
                { label: 'PTR - Maps an IP address to a domain name', value: 'PTR' },
                { label: 'SRV - Application server host and port', value: 'SRV' },
                { label: 'CAA - Restricts which CAs can issue certificates', value: 'CAA' },
                { label: 'NAPTR - Used by DDDS applications', value: 'NAPTR' },
                { label: 'SPF - Lists authorized email senders', value: 'SPF' },
                { label: 'DS - Delegation signer for DNSSEC', value: 'DS' },
              ]}
            />
          </FormField>

          <FormField label="TTL (seconds)" description="How long resolvers cache this record.">
            <Input
              type="number"
              value={recordTtl}
              onChange={({ detail }) => setRecordTtl(detail.value)}
              placeholder="300"
            />
          </FormField>

          <FormField label="Value" description="Enter IP addresses or domain names (one per line).">
            <Textarea
              rows={3}
              value={recordValue}
              onChange={({ detail }) => setRecordValue(detail.value)}
              placeholder="192.0.2.1"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
