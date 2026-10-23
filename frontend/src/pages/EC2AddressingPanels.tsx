import { useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import Checkbox from '@cloudscape-design/components/checkbox';
import Container from '@cloudscape-design/components/container';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Modal from '@cloudscape-design/components/modal';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Tabs from '@cloudscape-design/components/tabs';
import TextFilter from '@cloudscape-design/components/text-filter';
import Table from '../components/ResourceTable';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate } from '../format';
import {
  allocateEc2ElasticIp,
  associateEc2ElasticIp,
  createEc2NatGateway,
  deleteEc2NatGateway,
  disassociateEc2ElasticIp,
  releaseEc2ElasticIp,
} from '../api/client';

type Tag = { Key: string; Value: string };
type Message = { type: 'success' | 'error'; text: string };

const nameTag = (tags?: Tag[]) => tags?.find(tag => tag.Key === 'Name')?.Value;

export interface ElasticIpAssociation {
  kind: 'instance' | 'nat' | 'network-interface';
  /** Instance ID, NAT gateway ID or network interface ID. */
  resourceId: string;
  associationId?: string;
  privateIp?: string;
  networkInterfaceId?: string;
}

/**
 * What an Elastic IP is attached to. DescribeAddresses reports instance associations, but Floci 2.3.0 leaves
 * NAT gateway associations out of it (AWS includes them), so those come from DescribeNatGateways.
 */
export function elasticIpAssociation(address: any, natGateways: any[]): ElasticIpAssociation | null {
  if (address.InstanceId) {
    return { kind: 'instance', resourceId: address.InstanceId, associationId: address.AssociationId, privateIp: address.PrivateIpAddress, networkInterfaceId: address.NetworkInterfaceId };
  }
  for (const gateway of natGateways) {
    const match = (gateway.NatGatewayAddresses || []).find((item: any) => item.AllocationId && item.AllocationId === address.AllocationId);
    if (match) {
      return { kind: 'nat', resourceId: gateway.NatGatewayId, associationId: address.AssociationId || match.AssociationId, privateIp: address.PrivateIpAddress || match.PrivateIp, networkInterfaceId: address.NetworkInterfaceId || match.NetworkInterfaceId };
    }
  }
  if (address.NetworkInterfaceId || address.AssociationId) {
    return { kind: 'network-interface', resourceId: address.NetworkInterfaceId || '-', associationId: address.AssociationId, privateIp: address.PrivateIpAddress, networkInterfaceId: address.NetworkInterfaceId };
  }
  return null;
}

/** NAT gateway state colors as the VPC console shows them. */
export function natGatewayStateIndicator(state: string): 'success' | 'pending' | 'in-progress' | 'stopped' | 'error' | 'info' {
  if (state === 'available') return 'success';
  if (state === 'pending') return 'pending';
  if (state === 'deleting') return 'in-progress';
  if (state === 'deleted') return 'stopped';
  if (state === 'failed') return 'error';
  return 'info';
}

const capitalize = (value: string) => value ? value.charAt(0).toUpperCase() + value.slice(1) : value;

function TagsTable({ tags, empty }: { tags?: Tag[]; empty: string }) {
  return (
    <Table
      items={tags || []}
      trackBy="Key"
      columnDefinitions={[
        { id: 'Key', header: 'Key', cell: tag => tag.Key },
        { id: 'Value', header: 'Value', cell: tag => tag.Value || '-' },
      ]}
      empty={<Box textAlign="center">{empty}</Box>}
    />
  );
}

interface InstanceOption {
  InstanceId: string;
  Name?: string;
  State: { Name: string };
  PrivateIpAddress?: string;
}

interface ElasticIpsPanelProps {
  addresses: any[];
  natGateways: any[];
  instances: InstanceOption[];
  loading: boolean;
  onChanged: () => Promise<void>;
  onMessage: (message: Message) => void;
  onOpenNatGateway?: (natGatewayId: string) => void;
}

export function ElasticIpsPanel({ addresses, natGateways, instances, loading, onChanged, onMessage, onOpenNatGateway }: ElasticIpsPanelProps) {
  const [filter, setFilter] = useState('');
  const [selectedId, setSelectedId] = useState<string>();
  const [detailTab, setDetailTab] = useState('details');
  const [allocateOpen, setAllocateOpen] = useState(false);
  const [allocateName, setAllocateName] = useState('');
  const [allocating, setAllocating] = useState(false);
  const [associateOpen, setAssociateOpen] = useState(false);
  const [associateInstance, setAssociateInstance] = useState('');
  const [allowReassociation, setAllowReassociation] = useState(false);
  const [associating, setAssociating] = useState(false);
  const [pending, setPending] = useState<'release' | 'disassociate' | null>(null);
  const [working, setWorking] = useState(false);

  // Selection is kept by ID so it follows the refreshed row and drops when the address is gone.
  const selected = addresses.filter(item => item.AllocationId === selectedId);
  const setSelected = (items: any[]) => setSelectedId(items[0]?.AllocationId);
  const active = selected[0];
  const activeAssociation = active ? elasticIpAssociation(active, natGateways) : null;
  const filtered = addresses.filter(address => {
    const association = elasticIpAssociation(address, natGateways);
    return `${nameTag(address.Tags) || ''} ${address.PublicIp || ''} ${address.AllocationId || ''} ${association?.resourceId || ''}`.toLowerCase().includes(filter.toLowerCase());
  });
  const selectableInstances = instances.filter(instance => !['terminated', 'shutting-down'].includes(instance.State.Name));

  const handleAllocate = async () => {
    setAllocating(true);
    try {
      const result = await allocateEc2ElasticIp(allocateName.trim() || undefined);
      onMessage({ type: 'success', text: `Elastic IP address ${result.public_ip || ''} allocated (${result.allocation_id}).` });
      setAllocateOpen(false);
      setAllocateName('');
      setSelectedId(result.allocation_id);
      await onChanged();
    } catch (err: any) {
      onMessage({ type: 'error', text: err.message || 'Failed to allocate Elastic IP address' });
    } finally {
      setAllocating(false);
    }
  };

  const handleAssociate = async () => {
    if (!active || !associateInstance) return;
    setAssociating(true);
    try {
      await associateEc2ElasticIp(active.AllocationId, associateInstance, allowReassociation);
      onMessage({ type: 'success', text: `Elastic IP address ${active.PublicIp} associated with ${associateInstance}.` });
      setAssociateOpen(false);
      await onChanged();
    } catch (err: any) {
      onMessage({ type: 'error', text: err.message || 'Failed to associate Elastic IP address' });
    } finally {
      setAssociating(false);
    }
  };

  const handleConfirm = async () => {
    if (!active || !pending) return;
    setWorking(true);
    try {
      if (pending === 'release') {
        await releaseEc2ElasticIp(active.AllocationId);
        onMessage({ type: 'success', text: `Elastic IP address ${active.PublicIp} released.` });
        setSelected([]);
      } else if (activeAssociation?.associationId) {
        await disassociateEc2ElasticIp(active.AllocationId, activeAssociation.associationId);
        onMessage({ type: 'success', text: `Elastic IP address ${active.PublicIp} disassociated from ${activeAssociation.resourceId}.` });
      }
      await onChanged();
    } catch (err: any) {
      onMessage({ type: 'error', text: err.message || `Failed to ${pending} Elastic IP address` });
    } finally {
      setWorking(false);
      setPending(null);
    }
  };

  // AWS rejects re-associating an associated address unless AllowReassociation is set; Floci 2.3.0 moves it anyway.
  const reassociationBlocked = Boolean(activeAssociation) && !allowReassociation;

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            counter={`(${addresses.length})`}
            description="Static public IPv4 addresses allocated to this account. AWS charges for every public IPv4 address, whether or not it is associated."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <ButtonDropdown
                  disabled={!active}
                  items={[
                    { id: 'associate', text: 'Associate Elastic IP address', disabled: activeAssociation?.kind === 'nat' },
                    { id: 'disassociate', text: 'Disassociate Elastic IP address', disabled: !activeAssociation || activeAssociation.kind === 'nat', disabledReason: activeAssociation?.kind === 'nat' ? 'Delete the NAT gateway to free this address.' : 'The address is not associated.' },
                    { id: 'release', text: 'Release Elastic IP address', disabled: Boolean(activeAssociation), disabledReason: 'Disassociate the address first.' },
                  ]}
                  onItemClick={({ detail }) => {
                    if (detail.id === 'associate') {
                      setAssociateInstance('');
                      setAllowReassociation(false);
                      setAssociateOpen(true);
                    } else {
                      setPending(detail.id as 'release' | 'disassociate');
                    }
                  }}
                >
                  Actions
                </ButtonDropdown>
                <Button variant="primary" onClick={() => setAllocateOpen(true)}>Allocate Elastic IP address</Button>
              </SpaceBetween>
            }
          >
            Elastic IP addresses
          </Header>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info" header="How Floci 2.3.0 handles Elastic IPs">
            Allocation, association and tags are stored and returned. Floci doesn&apos;t enforce some AWS rules: it releases an
            address that is still associated, re-associates one without AllowReassociation, and leaves an instance&apos;s
            earlier Elastic IP associated when you add another. This console asks you to disassociate before releasing. While
            an address is associated with a running instance, Floci reports the instance&apos;s container IP as its public IP;
            the allocated address comes back after you disassociate it.
          </Alert>
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find Elastic IP addresses by name, address, allocation ID or resource"
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
          <Table
            ariaLabels={{ tableLabel: 'Elastic IP addresses' }}
            items={filtered}
            trackBy="AllocationId"
            selectionType="single"
            selectedItems={selected}
            onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
            loading={loading}
            loadingText="Loading Elastic IP addresses"
            columnDefinitions={[
              { id: 'name', header: 'Name', cell: (item: any) => nameTag(item.Tags) || '-' },
              { id: 'PublicIp', header: 'Allocated IPv4 address', cell: (item: any) => <Button variant="inline-link" onClick={() => setSelected([item])}>{item.PublicIp}</Button> },
              { id: 'type', header: 'Type', cell: () => 'Public IP' },
              { id: 'AllocationId', header: 'Allocation ID', minWidth: 230, cell: (item: any) => item.AllocationId },
              { id: 'instance', header: 'Associated instance ID', cell: (item: any) => {
                const association = elasticIpAssociation(item, natGateways);
                return association?.kind === 'instance' ? association.resourceId : '-';
              } },
              { id: 'associated', header: 'Associated with', cell: (item: any) => {
                const association = elasticIpAssociation(item, natGateways);
                if (!association) return '-';
                if (association.kind === 'nat') return `NAT gateway ${association.resourceId}`;
                if (association.kind === 'instance') return `Instance ${association.resourceId}`;
                return `Network interface ${association.resourceId}`;
              } },
              { id: 'private', header: 'Private IP address', cell: (item: any) => elasticIpAssociation(item, natGateways)?.privateIp || '-' },
              { id: 'association', header: 'Association ID', cell: (item: any) => elasticIpAssociation(item, natGateways)?.associationId || '-' },
              { id: 'border', header: 'Network border group', cell: (item: any) => item.NetworkBorderGroup || '-' },
            ]}
            empty={<Box textAlign="center">{filter ? 'No Elastic IP addresses match your filter.' : 'No Elastic IP addresses allocated.'}</Box>}
          />
        </SpaceBetween>
      </Container>

      {active && (
        <Container header={<Header variant="h2">{active.PublicIp}{nameTag(active.Tags) ? ` (${nameTag(active.Tags)})` : ''}</Header>}>
          <Tabs activeTabId={detailTab} onChange={({ detail }) => setDetailTab(detail.activeTabId)} tabs={[
            { id: 'details', label: 'Summary', content: (
              <KeyValuePairs columns={3} items={[
                { label: 'Allocated IPv4 address', value: active.PublicIp || '-' },
                { label: 'Type', value: 'Public IP' },
                { label: 'Allocation ID', value: active.AllocationId },
                { label: 'Scope', value: active.Domain === 'vpc' ? 'VPC' : active.Domain || '-' },
                { label: 'Associated instance ID', value: activeAssociation?.kind === 'instance' ? activeAssociation.resourceId : '-' },
                { label: 'NAT gateway ID', value: activeAssociation?.kind === 'nat'
                  ? (onOpenNatGateway ? <Button variant="inline-link" onClick={() => onOpenNatGateway(activeAssociation.resourceId)}>{activeAssociation.resourceId}</Button> : activeAssociation.resourceId)
                  : '-' },
                { label: 'Network interface ID', value: activeAssociation?.networkInterfaceId || '-' },
                { label: 'Private IP address', value: activeAssociation?.privateIp || '-' },
                { label: 'Association ID', value: activeAssociation?.associationId || '-' },
                { label: 'Public IPv4 pool', value: active.PublicIpv4Pool || 'amazon' },
                { label: 'Network border group', value: active.NetworkBorderGroup || '-' },
              ]} />
            ) },
            { id: 'tags', label: 'Tags', content: <TagsTable tags={active.Tags} empty="No tags on this Elastic IP address." /> },
          ]} />
        </Container>
      )}

      <Modal
        visible={allocateOpen}
        onDismiss={() => setAllocateOpen(false)}
        header="Allocate Elastic IP address"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAllocateOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={allocating} onClick={handleAllocate}>Allocate</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Public IPv4 address pool" description="Floci allocates from a simulated Amazon pool. Bring-your-own and customer-owned pools aren't available.">
            <RadioGroup value="amazon" items={[{ value: 'amazon', label: "Amazon's pool of IPv4 addresses" }]} />
          </FormField>
          <FormField label={<>Name tag <i>- optional</i></>} description="Stored as the address's Name tag (TagSpecifications, resource type elastic-ip).">
            <Input value={allocateName} onChange={({ detail }) => setAllocateName(detail.value)} placeholder="web-eip" />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={associateOpen}
        onDismiss={() => setAssociateOpen(false)}
        header="Associate Elastic IP address"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAssociateOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={associating} disabled={!associateInstance || reassociationBlocked} onClick={handleAssociate}>Associate</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box variant="p">Elastic IP address: <strong>{active?.PublicIp}</strong> ({active?.AllocationId})</Box>
          <FormField label="Resource type">
            <RadioGroup value="instance" items={[{ value: 'instance', label: 'Instance' }]} />
          </FormField>
          <FormField label="Instance" description="If the instance already has an Elastic IP, AWS disassociates it and associates this one.">
            <Select
              ariaLabel="Instance"
              selectedOption={associateInstance ? { value: associateInstance, label: associateInstance } : null}
              onChange={({ detail }) => setAssociateInstance(detail.selectedOption.value || '')}
              options={selectableInstances.map(instance => ({ value: instance.InstanceId, label: instance.InstanceId, description: `${instance.Name || 'No name'} · ${instance.State.Name}` }))}
              placeholder="Choose an instance"
              filteringType="auto"
              empty="No running or stopped instances"
            />
          </FormField>
          <FormField
            label="Reassociation"
            errorText={reassociationBlocked ? `This address is associated with ${activeAssociation?.resourceId}. Allow reassociation to move it, or AWS rejects the request.` : undefined}
          >
            <Checkbox checked={allowReassociation} onChange={({ detail }) => setAllowReassociation(detail.checked)}>
              Allow this Elastic IP address to be reassociated
            </Checkbox>
          </FormField>
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={pending === 'release'}
        header="Release Elastic IP addresses"
        confirmLabel="Release"
        loading={working}
        onConfirm={handleConfirm}
        onDismiss={() => setPending(null)}
      >
        Release {active?.PublicIp} ({active?.AllocationId})? The address returns to the pool and you may not get it back.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={pending === 'disassociate'}
        header="Disassociate Elastic IP address"
        confirmLabel="Disassociate"
        loading={working}
        onConfirm={handleConfirm}
        onDismiss={() => setPending(null)}
      >
        Disassociate {active?.PublicIp} from {activeAssociation?.resourceId}? The address stays allocated to your account.
      </ConfirmActionModal>
    </SpaceBetween>
  );
}

interface NatGatewaysPanelProps {
  natGateways: any[];
  addresses: any[];
  subnets: any[];
  routeTables: any[];
  loading: boolean;
  /** Gateway to select when the panel opens, e.g. from the Elastic IP details. */
  focusId?: string;
  onChanged: () => Promise<void>;
  onMessage: (message: Message) => void;
}

export function NatGatewaysPanel({ natGateways, addresses, subnets, routeTables, loading, focusId, onChanged, onMessage }: NatGatewaysPanelProps) {
  const [filter, setFilter] = useState('');
  const [selectedId, setSelectedId] = useState<string | undefined>(focusId);
  const [detailTab, setDetailTab] = useState('details');
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState('');
  const [subnetId, setSubnetId] = useState('');
  const [connectivity, setConnectivity] = useState<'public' | 'private'>('public');
  const [allocationId, setAllocationId] = useState('');
  const [creating, setCreating] = useState(false);
  const [allocating, setAllocating] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (focusId) setSelectedId(focusId);
  }, [focusId]);

  const selected = natGateways.filter(item => item.NatGatewayId === selectedId);
  const setSelected = (items: any[]) => setSelectedId(items[0]?.NatGatewayId);
  const active = selected[0];
  const primary = (gateway: any) => (gateway.NatGatewayAddresses || []).find((item: any) => item.IsPrimary) || (gateway.NatGatewayAddresses || [])[0] || {};
  const filtered = natGateways.filter(gateway =>
    `${nameTag(gateway.Tags) || ''} ${gateway.NatGatewayId} ${gateway.SubnetId || ''} ${gateway.VpcId || ''} ${primary(gateway).PublicIp || ''}`.toLowerCase().includes(filter.toLowerCase()));
  // Addresses a new public gateway can use: not associated with an instance or another NAT gateway.
  const freeAddresses = addresses.filter(address => !elasticIpAssociation(address, natGateways));
  const routesTo = (gatewayId: string) => routeTables.flatMap(table => (table.Routes || [])
    .filter((route: any) => route.NatGatewayId === gatewayId)
    .map((route: any) => ({ id: `${table.RouteTableId}-${route.DestinationCidrBlock || route.DestinationIpv6CidrBlock}`, routeTableId: table.RouteTableId, name: nameTag(table.Tags), destination: route.DestinationCidrBlock || route.DestinationIpv6CidrBlock || route.DestinationPrefixListId, state: route.State })));

  const openCreate = () => {
    setName('');
    setConnectivity('public');
    setSubnetId(current => current || subnets[0]?.SubnetId || '');
    setAllocationId(freeAddresses[0]?.AllocationId || '');
    setCreateOpen(true);
  };

  const handleAllocate = async () => {
    setAllocating(true);
    try {
      const result = await allocateEc2ElasticIp();
      setAllocationId(result.allocation_id);
      onMessage({ type: 'success', text: `Elastic IP address ${result.public_ip} allocated (${result.allocation_id}).` });
      await onChanged();
    } catch (err: any) {
      onMessage({ type: 'error', text: err.message || 'Failed to allocate Elastic IP address' });
    } finally {
      setAllocating(false);
    }
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const result = await createEc2NatGateway({
        name: name.trim() || undefined,
        subnet_id: subnetId,
        connectivity_type: connectivity,
        allocation_id: connectivity === 'public' ? allocationId : undefined,
      });
      onMessage({ type: 'success', text: `NAT gateway ${result.nat_gateway_id} created.` });
      setCreateOpen(false);
      setSelectedId(result.nat_gateway_id);
      await onChanged();
    } catch (err: any) {
      onMessage({ type: 'error', text: err.message || 'Failed to create NAT gateway' });
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!active) return;
    setDeleting(true);
    try {
      await deleteEc2NatGateway(active.NatGatewayId);
      onMessage({ type: 'success', text: `NAT gateway ${active.NatGatewayId} deleted. Its Elastic IP address stays allocated; release it from Elastic IPs if you don't need it.` });
      setSelected([]);
      await onChanged();
    } catch (err: any) {
      onMessage({ type: 'error', text: err.message || 'Failed to delete NAT gateway' });
    } finally {
      setDeleting(false);
      setPendingDelete(false);
    }
  };

  const activeRoutes = active ? routesTo(active.NatGatewayId) : [];
  const activePrimary = active ? primary(active) : {};
  const canCreate = Boolean(subnetId) && (connectivity === 'private' || Boolean(allocationId));

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            counter={`(${natGateways.length})`}
            description="Managed network address translation for instances in private subnets."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!active || active.State === 'deleted' || active.State === 'deleting'} onClick={() => setPendingDelete(true)}>Delete NAT gateway</Button>
                <Button variant="primary" onClick={openCreate}>Create NAT gateway</Button>
              </SpaceBetween>
            }
          >
            NAT gateways
          </Header>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning" header="Floci 2.3.0 stores NAT gateways but routes no traffic through them">
            A NAT gateway is reported Available as soon as it is created, and route tables can point at it, but packets
            never pass through it: instances (Docker containers) reach the network through Docker networking whatever the
            route table says. Floci also accepts a public NAT gateway with no Elastic IP, and one Elastic IP shared by several
            gateways; AWS rejects both, so this console requires an unused Elastic IP. A deleted gateway disappears at once
            (AWS lists it as Deleted for about an hour), and routes to it stay Active instead of turning Blackhole.
          </Alert>
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Find NAT gateways by name, ID, subnet, VPC or address"
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
          <Table
            ariaLabels={{ tableLabel: 'NAT gateways' }}
            items={filtered}
            trackBy="NatGatewayId"
            selectionType="single"
            selectedItems={selected}
            onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
            loading={loading}
            loadingText="Loading NAT gateways"
            columnDefinitions={[
              { id: 'name', header: 'Name', cell: (item: any) => nameTag(item.Tags) || '-' },
              { id: 'NatGatewayId', header: 'NAT gateway ID', minWidth: 230, cell: (item: any) => <Button variant="inline-link" onClick={() => setSelected([item])}>{item.NatGatewayId}</Button> },
              { id: 'connectivity', header: 'Connectivity type', cell: (item: any) => capitalize(item.ConnectivityType || 'public') },
              { id: 'state', header: 'State', cell: (item: any) => <StatusIndicator type={natGatewayStateIndicator(item.State)}>{capitalize(item.State || 'unknown')}</StatusIndicator> },
              { id: 'message', header: 'State message', cell: (item: any) => item.FailureMessage || '-' },
              { id: 'public', header: 'Primary public IPv4 address', cell: (item: any) => primary(item).PublicIp || '-' },
              { id: 'private', header: 'Primary private IPv4 address', cell: (item: any) => primary(item).PrivateIp || '-' },
              { id: 'eni', header: 'Primary network interface ID', cell: (item: any) => primary(item).NetworkInterfaceId || '-' },
              { id: 'VpcId', header: 'VPC', cell: (item: any) => item.VpcId || '-' },
              { id: 'SubnetId', header: 'Subnet', cell: (item: any) => item.SubnetId || '-' },
              { id: 'created', header: 'Created', cell: (item: any) => formatAwsDate(item.CreateTime) },
            ]}
            empty={<Box textAlign="center">{filter ? 'No NAT gateways match your filter.' : 'No NAT gateways found.'}</Box>}
          />
        </SpaceBetween>
      </Container>

      {active && (
        <Container header={<Header variant="h2">{active.NatGatewayId}{nameTag(active.Tags) ? ` / ${nameTag(active.Tags)}` : ''}</Header>}>
          <Tabs activeTabId={detailTab} onChange={({ detail }) => setDetailTab(detail.activeTabId)} tabs={[
            { id: 'details', label: 'Details', content: (
              <KeyValuePairs columns={3} items={[
                { label: 'NAT gateway ID', value: active.NatGatewayId },
                { label: 'Connectivity type', value: capitalize(active.ConnectivityType || 'public') },
                { label: 'State', value: <StatusIndicator type={natGatewayStateIndicator(active.State)}>{capitalize(active.State || 'unknown')}</StatusIndicator> },
                { label: 'State message', value: active.FailureMessage || '-' },
                { label: 'Primary public IPv4 address', value: activePrimary.PublicIp || '-' },
                { label: 'Primary private IPv4 address', value: activePrimary.PrivateIp || '-' },
                { label: 'Primary network interface ID', value: activePrimary.NetworkInterfaceId || '-' },
                { label: 'Elastic IP allocation ID', value: activePrimary.AllocationId || '-' },
                { label: 'VPC', value: active.VpcId || '-' },
                { label: 'Subnet', value: active.SubnetId || '-' },
                { label: 'Created', value: formatAwsDate(active.CreateTime) },
                { label: 'Deleted', value: active.DeleteTime ? formatAwsDate(active.DeleteTime) : '-' },
              ]} />
            ) },
            { id: 'routes', label: `Route tables (${activeRoutes.length})`, content: (
              <Table
                items={activeRoutes}
                trackBy="id"
                columnDefinitions={[
                  { id: 'routeTableId', header: 'Route table ID', cell: (route: any) => route.routeTableId },
                  { id: 'name', header: 'Name', cell: (route: any) => route.name || '-' },
                  { id: 'destination', header: 'Destination', cell: (route: any) => route.destination || '-' },
                  { id: 'state', header: 'Route status', cell: (route: any) => route.state ? <StatusIndicator type={route.state === 'active' ? 'success' : 'error'}>{capitalize(route.state)}</StatusIndicator> : '-' },
                ]}
                empty={<Box textAlign="center">No route table sends traffic to this NAT gateway. Add a route such as 0.0.0.0/0 in a private subnet&apos;s route table.</Box>}
              />
            ) },
            { id: 'tags', label: 'Tags', content: <TagsTable tags={active.Tags} empty="No tags on this NAT gateway." /> },
          ]} />
        </Container>
      )}

      <Modal
        visible={createOpen}
        onDismiss={() => setCreateOpen(false)}
        header="Create NAT gateway"
        size="medium"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creating} disabled={!canCreate} onClick={handleCreate}>Create NAT gateway</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label={<>Name <i>- optional</i></>} description="Stored as the gateway's Name tag.">
            <Input value={name} onChange={({ detail }) => setName(detail.value)} placeholder="my-nat-gateway" />
          </FormField>
          <FormField label="Subnet" description="A public NAT gateway goes in a public subnet, one whose route table has a route to an internet gateway.">
            <Select
              ariaLabel="Subnet"
              selectedOption={subnetId ? { value: subnetId, label: subnetId } : null}
              onChange={({ detail }) => setSubnetId(detail.selectedOption.value || '')}
              options={subnets.map(subnet => ({ value: subnet.SubnetId, label: subnet.SubnetId, description: [nameTag(subnet.Tags), subnet.VpcId, subnet.AvailabilityZone, subnet.CidrBlock].filter(Boolean).join(' · ') }))}
              placeholder="Select a subnet"
              filteringType="auto"
              empty="No subnets found"
            />
          </FormField>
          <FormField label="Connectivity type">
            <RadioGroup
              value={connectivity}
              onChange={({ detail }) => setConnectivity(detail.value as 'public' | 'private')}
              items={[
                { value: 'public', label: 'Public', description: 'Instances in private subnets reach the internet through the gateway. Needs an Elastic IP.' },
                { value: 'private', label: 'Private', description: 'Instances reach other VPCs or your on-premises network. No Elastic IP.' },
              ]}
            />
          </FormField>
          {connectivity === 'public' && (
            <FormField
              label="Elastic IP allocation ID"
              description="Only addresses that aren't associated with an instance or another NAT gateway are listed."
              errorText={!allocationId ? 'A public NAT gateway needs an Elastic IP.' : undefined}
              secondaryControl={<Button loading={allocating} onClick={handleAllocate}>Allocate Elastic IP</Button>}
            >
              <Select
                ariaLabel="Elastic IP allocation ID"
                selectedOption={allocationId ? { value: allocationId, label: allocationId, description: addresses.find(item => item.AllocationId === allocationId)?.PublicIp } : null}
                onChange={({ detail }) => setAllocationId(detail.selectedOption.value || '')}
                options={freeAddresses.map(address => ({ value: address.AllocationId, label: address.AllocationId, description: [address.PublicIp, nameTag(address.Tags)].filter(Boolean).join(' · ') }))}
                placeholder="Choose an Elastic IP"
                empty="No unassociated Elastic IP addresses. Allocate one."
              />
            </FormField>
          )}
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={pendingDelete}
        header="Delete NAT gateway"
        confirmLabel="Delete"
        confirmationText="delete"
        loading={deleting}
        onConfirm={handleDelete}
        onDismiss={() => setPendingDelete(false)}
      >
        Delete {active?.NatGatewayId}? Its Elastic IP address is disassociated but stays allocated, and AWS marks routes that
        point at the gateway as Blackhole.
        {activeRoutes.length > 0 && ` ${activeRoutes.length} route${activeRoutes.length === 1 ? '' : 's'} point at it: ${activeRoutes.map(route => `${route.routeTableId} ${route.destination}`).join(', ')}.`}
      </ConfirmActionModal>
    </SpaceBetween>
  );
}
