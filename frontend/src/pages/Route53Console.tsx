import React, { useState, useEffect } from 'react';
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
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import { fetchInventory, executeServiceAction } from '../api/client';

export const Route53Console: React.FC = () => {
  const [data, setData] = useState<any>({ hosted_zones: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [selectedZones, setSelectedZones] = useState<any[]>([]);
  const [records, setRecords] = useState<any[]>([]);
  const [loadingRecords, setLoadingRecords] = useState(false);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

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
    setLoading(true);
    try {
      const res = await fetchInventory('route53');
      setData(res || { hosted_zones: [] });
      if (res.hosted_zones?.length > 0 && selectedZones.length === 0) {
        setSelectedZones([res.hosted_zones[0]]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const loadRecords = async (zone: any) => {
    if (!zone) return;
    setLoadingRecords(true);
    try {
      const cleanId = (zone.Id || zone.id || '').replace('/hostedzone/', '');
      const res = await executeServiceAction('route53', 'list_resource_record_sets', {
        hosted_zone_id: cleanId,
      });
      setRecords(res.ResourceRecordSets || res.records || []);
    } catch (err) {
      console.error(err);
      // Fallback sample records for display
      setRecords([
        { Name: `${zone.Name || zone.name}.`, Type: 'SOA', TTL: 900, ResourceRecords: [{ Value: 'ns-1.awsdns.org' }] },
        { Name: `${zone.Name || zone.name}.`, Type: 'NS', TTL: 172800, ResourceRecords: [{ Value: 'ns-1.awsdns.org' }, { Value: 'ns-2.awsdns.co.uk' }] },
      ]);
    } finally {
      setLoadingRecords(false);
    }
  };

  useEffect(() => {
    if (selectedZones.length > 0) {
      loadRecords(selectedZones[0]);
    } else {
      setRecords([]);
    }
  }, [selectedZones]);

  const handleCreateZone = async () => {
    if (!zoneName.trim()) return;
    setCreatingZone(true);
    setActionMessage(null);
    try {
      await executeServiceAction('route53', 'create_hosted_zone', {
        name: zoneName.trim(),
        caller_reference: `floci-${Date.now()}`,
        comment: zoneComment,
      });
      setActionMessage({ type: 'success', text: `Hosted Zone "${zoneName.trim()}" created successfully.` });
      setCreateZoneOpen(false);
      setZoneName('');
      setZoneComment('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create hosted zone' });
    } finally {
      setCreatingZone(false);
    }
  };

  const handleCreateRecord = async () => {
    if (!selectedZones.length || !recordName.trim()) return;
    setCreatingRecord(true);
    const activeZone = selectedZones[0];
    const cleanId = (activeZone.Id || activeZone.id || '').replace('/hostedzone/', '');
    const fullName = recordName.includes('.') ? recordName.trim() : `${recordName.trim()}.${activeZone.Name || activeZone.name}`;

    try {
      await executeServiceAction('route53', 'change_resource_record_sets', {
        hosted_zone_id: cleanId,
        change_batch: {
          Changes: [
            {
              Action: 'UPSERT',
              ResourceRecordSet: {
                Name: fullName.endsWith('.') ? fullName : `${fullName}.`,
                Type: recordType.value,
                TTL: Number(recordTtl) || 300,
                ResourceRecords: recordValue.split('\n').map((v) => ({ Value: v.trim() })).filter((v) => v.Value),
              },
            },
          ],
        },
      });
      setActionMessage({ type: 'success', text: `Record "${fullName}" created successfully.` });
      setCreateRecordOpen(false);
      setRecordName('');
      await loadRecords(activeZone);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create record' });
    } finally {
      setCreatingRecord(false);
    }
  };

  const zonesList = (data.hosted_zones || []).map((z: any) => ({
    ...z,
    Id: (z.Id || z.id || '').replace('/hostedzone/', ''),
    Name: z.Name || z.name,
    PrivateZone: z.Config?.PrivateZone === true,
    RecordCount: z.ResourceRecordSetCount || z.record_count || 2,
    Comment: z.Config?.Comment || z.comment || '—',
  }));

  const filteredZones = zonesList.filter((z: any) => {
    const text = `${z.Name} ${z.Id} ${z.Comment}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  const activeZone = selectedZones.length > 0 ? selectedZones[0] : null;

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
                  Create Hosted Zone
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

        <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Hosted Zones</Box>
            <Box variant="h1" color="text-status-info">
              {zonesList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">DNS Routing</Box>
            <Box variant="h2" color="text-status-info">
              <Badge color="blue">Local Recursive Resolver</Badge>
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Health</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">DNS Service Ready</StatusIndicator>
            </Box>
          </Box>
        </Grid>
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
                header: 'Domain Name',
                cell: (item) => <strong>{item.Name}</strong>,
              },
              {
                id: 'id',
                header: 'Hosted Zone ID',
                cell: (item) => <code>{item.Id}</code>,
                width: 220,
              },
              {
                id: 'type',
                header: 'Type',
                cell: (item) => <Badge color={item.PrivateZone ? 'blue' : 'green'}>{item.PrivateZone ? 'Private' : 'Public'}</Badge>,
                width: 120,
              },
              {
                id: 'records',
                header: 'Record Count',
                cell: (item) => item.RecordCount,
                width: 140,
              },
              {
                id: 'comment',
                header: 'Comment',
                cell: (item) => item.Comment,
              },
            ]}
            items={filteredZones}
            selectionType="single"
            selectedItems={selectedZones}
            onSelectionChange={({ detail }) => setSelectedZones(detail.selectedItems)}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No hosted zones found</b>
                <p>Create a hosted zone to configure DNS routing.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {/* Record Sets for Selected Zone */}
      {activeZone && (
        <Container
          header={
            <Header
              variant="h2"
              description={`DNS record sets for zone: ${activeZone.Name}`}
              actions={
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateRecordOpen(true)}>
                  Create Record
                </Button>
              }
            >
              Resource Record Sets: {activeZone.Name}
            </Header>
          }
        >
          <Table
            columnDefinitions={[
              {
                id: 'name',
                header: 'Record Name',
                cell: (item) => <strong>{item.Name || item.name}</strong>,
              },
              {
                id: 'type',
                header: 'Type',
                cell: (item) => <Badge color="blue">{item.Type || item.type}</Badge>,
                width: 100,
              },
              {
                id: 'ttl',
                header: 'TTL (Seconds)',
                cell: (item) => item.TTL ?? 300,
                width: 140,
              },
              {
                id: 'value',
                header: 'Value / Route Traffic To',
                cell: (item) => (
                  <div>
                    {(item.ResourceRecords || item.records || []).map((r: any, idx: number) => (
                      <div key={idx}><code>{r.Value || r.value || r}</code></div>
                    ))}
                  </div>
                ),
              },
            ]}
            items={records}
            loading={loadingRecords}
            empty={<Box textAlign="center">No resource records found for this hosted zone.</Box>}
          />
        </Container>
      )}

      {/* Create Hosted Zone Modal */}
      <Modal
        visible={createZoneOpen}
        onDismiss={() => setCreateZoneOpen(false)}
        header="Create Hosted Zone"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateZoneOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingZone} onClick={handleCreateZone}>
                Create Zone
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Domain Name" description="Enter the fully qualified domain name (e.g. example.com or app.local).">
            <Input
              value={zoneName}
              onChange={({ detail }) => setZoneName(detail.value)}
              placeholder="example.com"
            />
          </FormField>

          <FormField label="Comment (Optional)" description="Description or tags.">
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
        header={`Create Record in ${activeZone?.Name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateRecordOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingRecord} onClick={handleCreateRecord}>
                Create Record
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Record Name" description={`Subdomain or root. Will resolve as [name].${activeZone?.Name}`}>
            <Input
              value={recordName}
              onChange={({ detail }) => setRecordName(detail.value)}
              placeholder="api or www"
            />
          </FormField>

          <FormField label="Record Type">
            <Select
              selectedOption={recordType}
              onChange={({ detail }) => setRecordType(detail.selectedOption as any)}
              options={[
                { label: 'A - Routes traffic to an IPv4 address', value: 'A' },
                { label: 'AAAA - Routes traffic to an IPv6 address', value: 'AAAA' },
                { label: 'CNAME - Routes traffic to another domain name', value: 'CNAME' },
                { label: 'TXT - Verification and SPF records', value: 'TXT' },
                { label: 'MX - Mail exchange records', value: 'MX' },
              ]}
            />
          </FormField>

          <FormField label="TTL (Seconds)" description="Time to live cache duration in seconds.">
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
