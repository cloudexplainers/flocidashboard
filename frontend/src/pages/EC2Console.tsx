import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import ButtonDropdown from '@cloudscape-design/components/button-dropdown';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import Multiselect from '@cloudscape-design/components/multiselect';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate } from '../format';
import {
  fetchServiceInventory,
  launchEc2Instance,
  changeEc2InstanceState,
  fetchEc2CommandInvocation,
  createEc2Vpc,
  deleteEc2Vpc,
  createEc2Subnet,
  deleteEc2Subnet,
  createEc2SecurityGroup,
  deleteEc2SecurityGroup,
  changeEc2SecurityGroupRule,
  runEc2InstanceCommand,
} from '../api/client';

type StatusType = 'success' | 'stopped' | 'pending' | 'in-progress' | 'error' | 'warning' | 'info';

/** Instance state colors as the EC2 console shows them. */
export function instanceStateIndicator(state: string): StatusType {
  if (state === 'running') return 'success';
  if (state === 'pending') return 'pending';
  if (state === 'stopping' || state === 'shutting-down') return 'in-progress';
  if (state === 'terminated') return 'stopped';
  if (state === 'stopped') return 'stopped';
  return 'info';
}

const tagName = (tags?: Array<{ Key: string; Value: string }>) => tags?.find(tag => tag.Key === 'Name')?.Value;

interface SgRule {
  IpProtocol: string;
  FromPort?: number;
  ToPort?: number;
  IpRanges?: Array<{ CidrIp: string; Description?: string }>;
  Ipv6Ranges?: Array<{ CidrIpv6: string; Description?: string }>;
  UserIdGroupPairs?: Array<{ GroupId: string; Description?: string }>;
  PrefixListIds?: Array<{ PrefixListId: string; Description?: string }>;
}

const PORT_TYPES: Record<string, string> = { '22': 'SSH', '80': 'HTTP', '443': 'HTTPS', '3306': 'MYSQL/Aurora', '5432': 'PostgreSQL', '3389': 'RDP', '53': 'DNS', '6379': 'Custom TCP', '2049': 'NFS' };

/** One row per source (CIDR, IPv6 CIDR, security group or prefix list), as the console lists rules. */
export function expandSgRules(rules: SgRule[]) {
  return rules.flatMap((rule, index) => {
    const protocol = rule.IpProtocol === '-1' ? 'All' : rule.IpProtocol.toUpperCase();
    const allPorts = rule.IpProtocol === '-1' || rule.FromPort == null || (rule.FromPort === 0 && rule.ToPort === 65535) || rule.FromPort === -1;
    const portRange = allPorts ? 'All' : rule.FromPort === rule.ToPort ? String(rule.FromPort) : `${rule.FromPort} - ${rule.ToPort}`;
    const type = rule.IpProtocol === '-1' ? 'All traffic' : rule.IpProtocol === 'icmp' ? 'All ICMP - IPv4' : (rule.FromPort === rule.ToPort && PORT_TYPES[String(rule.FromPort)]) || `Custom ${protocol}`;
    const peers = [
      ...(rule.IpRanges || []).map(range => ({ peer: range.CidrIp, cidr: range.CidrIp, description: range.Description })),
      ...(rule.Ipv6Ranges || []).map(range => ({ peer: range.CidrIpv6, cidr: undefined, description: range.Description })),
      ...(rule.UserIdGroupPairs || []).map(pair => ({ peer: pair.GroupId, cidr: undefined, description: pair.Description })),
      ...(rule.PrefixListIds || []).map(list => ({ peer: list.PrefixListId, cidr: undefined, description: list.Description })),
    ];
    return peers.map((peer, peerIndex) => ({ id: `${index}-${peerIndex}`, type, protocol, portRange, rule, ...peer }));
  });
}

interface InstanceItem {
  InstanceId: string;
  Name?: string;
  State: { Name: string; Code: number };
  InstanceType: string;
  PublicIpAddress?: string;
  PrivateIpAddress?: string;
  KeyName?: string;
  LaunchTime?: string;
  VpcId?: string;
  SubnetId?: string;
  SecurityGroups?: Array<{ GroupId: string; GroupName: string }>;
  Tags?: Array<{ Key: string; Value: string }>;
  ImageId?: string;
  AvailabilityZone?: string;
  PrivateDnsName?: string;
  PublicDnsName?: string;
  Architecture?: string;
  IamInstanceProfile?: string;
}

interface EC2ConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const EC2Console: React.FC<EC2ConsoleProps> = ({ activeTab, onTabChange }) => {
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(['vpcs', 'subnets', 'routes', 'gateways'].includes(activeTab || '') ? 'vpcs' : activeTab || 'instances');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(['vpcs', 'subnets', 'routes', 'gateways'].includes(activeTab) ? 'vpcs' : activeTab);
      if (['vpcs', 'subnets', 'routes', 'gateways'].includes(activeTab)) setNetworkView(activeTab);
    }
  }, [activeTab]);

  // Instances State
  const [instances, setInstances] = useState<InstanceItem[]>([]);
  const [selectedInstances, setSelectedInstances] = useState<InstanceItem[]>([]);
  const [instanceFilter, setInstanceFilter] = useState('');
  const [instanceDetailTab, setInstanceDetailTab] = useState('details');
  const [networkView, setNetworkView] = useState(['subnets', 'routes', 'gateways'].includes(activeTab || '') ? activeTab! : 'vpcs');
  const [launchModalOpen, setLaunchModalOpen] = useState(false);
  const [images, setImages] = useState<any[]>([]);
  const [instanceTypes, setInstanceTypes] = useState<any[]>([]);
  const [availabilityZones, setAvailabilityZones] = useState<string[]>([]);
  const [imageId, setImageId] = useState('');
  const [instanceType, setInstanceType] = useState('t2.micro');
  const [instanceName, setInstanceName] = useState('');
  const [launchSubnetId, setLaunchSubnetId] = useState('');
  const [launchSgIds, setLaunchSgIds] = useState<string[]>([]);
  const [launching, setLaunching] = useState(false);
  const [pendingStateAction, setPendingStateAction] = useState<'stop' | 'reboot' | 'terminate' | null>(null);
  const [changingState, setChangingState] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<{ kind: 'VPC' | 'subnet' | 'security group'; id: string } | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Run Command (SSM) Modal
  const [runCommandOpen, setRunCommandOpen] = useState(false);
  const [commandScript, setCommandScript] = useState('hostname\nuname -a\nuptime');
  const [commandOutput, setCommandOutput] = useState<any | null>(null);
  const [runningCommand, setRunningCommand] = useState(false);

  // VPCs & Subnets State
  const [vpcs, setVpcs] = useState<any[]>([]);
  const [subnets, setSubnets] = useState<any[]>([]);
  const [routeTables, setRouteTables] = useState<any[]>([]);
  const [internetGateways, setInternetGateways] = useState<any[]>([]);
  const [vpcEndpoints, setVpcEndpoints] = useState<any[]>([]);
  const [createVpcOpen, setCreateVpcOpen] = useState(false);
  const [vpcCidr, setVpcCidr] = useState('10.0.0.0/16');
  const [vpcName, setVpcName] = useState('');
  const [creatingVpc, setCreatingVpc] = useState(false);

  const [createSubnetOpen, setCreateSubnetOpen] = useState(false);
  const [subnetCidr, setSubnetCidr] = useState('10.0.1.0/24');
  const [subnetAz, setSubnetAz] = useState('');
  const [subnetVpcId, setSubnetVpcId] = useState('');
  const [subnetName, setSubnetName] = useState('');
  const [creatingSubnet, setCreatingSubnet] = useState(false);

  // Security Groups State
  const [securityGroups, setSecurityGroups] = useState<any[]>([]);
  const [selectedSgs, setSelectedSgs] = useState<any[]>([]);
  const [createSgOpen, setCreateSgOpen] = useState(false);
  const [sgName, setSgName] = useState('');
  const [sgDescription, setSgDescription] = useState('');
  const [sgVpcId, setSgVpcId] = useState('');
  const [creatingSg, setCreatingSg] = useState(false);

  // Add Rule Modal
  const [addRuleOpen, setAddRuleOpen] = useState(false);
  const [ruleDirection, setRuleDirection] = useState<'ingress' | 'egress'>('ingress');
  const [ruleType, setRuleType] = useState<{ label: string; value: string; port: string; proto: string }>({ label: 'HTTP', value: 'http', port: '80', proto: 'tcp' });
  const [rulePort, setRulePort] = useState('80');
  const [ruleCidr, setRuleCidr] = useState('');
  const [ruleDescription, setRuleDescription] = useState('');
  const [savingRule, setSavingRule] = useState(false);
  const [ruleToRemove, setRuleToRemove] = useState<{ direction: 'ingress' | 'egress'; rule: SgRule; cidr: string } | null>(null);
  const [selectedSgRuleTab, setSelectedSgRuleTab] = useState('inbound');

  const loadData = async () => {
    setLoading(true);
    try {
      const ec2Data = await fetchServiceInventory('ec2');

      // Map instances
      const instList = (ec2Data.instances || ec2Data.Reservations?.flatMap((r: any) => r.Instances) || []).map((inst: any) => ({
        InstanceId: inst.InstanceId || inst.id,
        Name: (inst.Tags || inst.tags)?.find((t: any) => t.Key === 'Name')?.Value || inst.Name || undefined,
        State: typeof inst.State === 'object' ? inst.State : { Name: inst.State || inst.state || 'unknown', Code: inst.state_code },
        InstanceType: inst.InstanceType || inst.instance_type || '-',
        ImageId: inst.ImageId || inst.image_id,
        AvailabilityZone: inst.Placement?.AvailabilityZone || inst.placement?.AvailabilityZone,
        PrivateDnsName: inst.PrivateDnsName || inst.private_dns,
        PublicDnsName: inst.PublicDnsName || inst.public_dns,
        Architecture: inst.Architecture || inst.architecture,
        IamInstanceProfile: (inst.IamInstanceProfile || inst.iam_instance_profile)?.Arn,
        PublicIpAddress: inst.PublicIpAddress || inst.public_ip,
        PrivateIpAddress: inst.PrivateIpAddress || inst.private_ip,
        KeyName: inst.KeyName || inst.key_name,
        LaunchTime: inst.LaunchTime || inst.launch_time,
        VpcId: inst.VpcId || inst.vpc_id,
        SubnetId: inst.SubnetId || inst.subnet_id,
        SecurityGroups: inst.SecurityGroups || inst.security_groups || [],
        Tags: inst.Tags || inst.tags || [],
      }));
      setInstances(instList);
      setSelectedInstances(previous => previous.length
        ? instList.filter((item: InstanceItem) => item.InstanceId === previous[0].InstanceId)
        : instList.slice(0, 1));

      // Map VPCs and Subnets
      const vpcList = ec2Data.vpcs || ec2Data.Vpcs || [];
      setVpcs(vpcList);
      if (vpcList.length > 0) {
        const preferred = vpcList.find((vpc: any) => vpc.IsDefault) || vpcList[0];
        setSubnetVpcId(current => current || preferred.VpcId || preferred.id);
        setSgVpcId(current => current || preferred.VpcId || preferred.id);
      }
      const imageList = ec2Data.images || [];
      setImages(imageList);
      setImageId(current => current || imageList[0]?.ImageId || '');
      setInstanceTypes(ec2Data.instance_types || []);
      const zones = (ec2Data.availability_zones || []).map((zone: any) => zone.ZoneName).filter(Boolean);
      setAvailabilityZones(zones);
      setSubnetAz(current => current || zones[0] || '');

      const subnetList = ec2Data.subnets || ec2Data.Subnets || [];
      setSubnets(subnetList);

      setRouteTables(ec2Data.route_tables || ec2Data.RouteTables || []);
      setInternetGateways(ec2Data.internet_gateways || ec2Data.InternetGateways || []);
      setVpcEndpoints(ec2Data.vpc_endpoints || ec2Data.VpcEndpoints || []);

      // Map Security Groups
      const sgList = ec2Data.security_groups || ec2Data.SecurityGroups || [];
      setSecurityGroups(sgList);
      setSelectedSgs(previous => previous.length
        ? sgList.filter((item: any) => (item.GroupId || item.id) === (previous[0].GroupId || previous[0].id))
        : sgList.slice(0, 1));
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeInstance = selectedInstances[0];
  const activeSg = selectedSgs[0];

  const handleLaunchInstance = async () => {
    if (!imageId || !instanceType) return;
    setLaunching(true);
    setActionMessage(null);
    try {
      const result = await launchEc2Instance({
        image_id: imageId,
        instance_type: instanceType,
        subnet_id: launchSubnetId || undefined,
        security_group_ids: launchSgIds.length ? launchSgIds : undefined,
        tags: instanceName.trim() ? { Name: instanceName.trim() } : undefined,
      });
      setActionMessage({ type: 'success', text: `Successfully initiated launch of instance ${result.instance_id || ''}`.trim() });
      setLaunchModalOpen(false);
      setInstanceName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to launch instance' });
    } finally {
      setLaunching(false);
    }
  };

  const handleInstanceState = async (action: 'start' | 'stop' | 'terminate' | 'reboot') => {
    if (!activeInstance) return;
    setChangingState(true);
    try {
      await changeEc2InstanceState(activeInstance.InstanceId, action);
      const progress = { start: 'starting', stop: 'stopping', reboot: 'reboot', terminate: 'terminating' }[action];
      setActionMessage({ type: 'success', text: `Successfully initiated ${progress} of ${activeInstance.InstanceId}` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || `Failed to ${action} instance` });
    } finally {
      setChangingState(false);
      setPendingStateAction(null);
    }
  };

  const handleRunCommand = async () => {
    if (!activeInstance || !commandScript.trim()) return;
    setRunningCommand(true);
    setCommandOutput(null);
    try {
      const sent = await runEc2InstanceCommand(activeInstance.InstanceId, commandScript.trim());
      setCommandOutput({ command_id: sent.command_id, status: sent.status });
      // GetCommandInvocation until the command leaves Pending/InProgress, as the Run Command console does.
      for (let attempt = 0; attempt < 20 && sent.command_id; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        const invocation = await fetchEc2CommandInvocation(activeInstance.InstanceId, sent.command_id);
        setCommandOutput({ command_id: sent.command_id, ...invocation });
        if (!['Pending', 'InProgress', 'Delayed'].includes(invocation.status)) break;
      }
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to execute command' });
    } finally {
      setRunningCommand(false);
    }
  };

  const handleCreateVpc = async () => {
    if (!vpcCidr.trim()) return;
    setCreatingVpc(true);
    try {
      await createEc2Vpc(vpcCidr.trim(), vpcName.trim() || undefined);
      setActionMessage({ type: 'success', text: `VPC created with CIDR ${vpcCidr.trim()}.` });
      setCreateVpcOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create VPC' });
    } finally {
      setCreatingVpc(false);
    }
  };

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      if (pendingDelete.kind === 'VPC') await deleteEc2Vpc(pendingDelete.id);
      if (pendingDelete.kind === 'subnet') await deleteEc2Subnet(pendingDelete.id);
      if (pendingDelete.kind === 'security group') await deleteEc2SecurityGroup(pendingDelete.id);
      setActionMessage({ type: 'success', text: `Successfully deleted ${pendingDelete.kind} ${pendingDelete.id}.` });
      if (pendingDelete.kind === 'security group') setSelectedSgs([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || `Failed to delete ${pendingDelete.kind}` });
    } finally {
      setDeleting(false);
      setPendingDelete(null);
    }
  };

  const handleCreateSubnet = async () => {
    if (!subnetVpcId || !subnetCidr.trim()) return;
    setCreatingSubnet(true);
    try {
      await createEc2Subnet(subnetVpcId, subnetCidr.trim(), subnetAz, subnetName.trim() || undefined);
      setActionMessage({ type: 'success', text: `Subnet created with CIDR ${subnetCidr.trim()}.` });
      setCreateSubnetOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create subnet' });
    } finally {
      setCreatingSubnet(false);
    }
  };

  const handleCreateSg = async () => {
    if (!sgName.trim() || !sgVpcId) return;
    setCreatingSg(true);
    try {
      await createEc2SecurityGroup(sgName.trim(), sgDescription.trim(), sgVpcId);
      setActionMessage({ type: 'success', text: `Security Group "${sgName.trim()}" created.` });
      setCreateSgOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create security group' });
    } finally {
      setCreatingSg(false);
    }
  };

  const handleAddRule = async () => {
    if (!activeSg) return;
    const allTraffic = ruleType.proto === '-1';
    const port = Number(rulePort);
    if (!allTraffic && (!Number.isInteger(port) || port < 0 || port > 65535)) {
      setActionMessage({ type: 'error', text: 'Port range must be a number from 0 to 65535.' });
      return;
    }
    setSavingRule(true);
    try {
      await changeEc2SecurityGroupRule(activeSg.GroupId || activeSg.id, ruleDirection, {
        protocol: ruleType.proto,
        ...(allTraffic ? {} : { from_port: port, to_port: port }),
        cidr: ruleCidr.trim(),
        description: ruleDescription.trim() || undefined,
      });
      setActionMessage({ type: 'success', text: `${ruleDirection === 'ingress' ? 'Inbound' : 'Outbound'} security group rule successfully modified on security group (${activeSg.GroupId || activeSg.id}).` });
      setAddRuleOpen(false);
      setRuleDescription('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to add rule' });
    } finally {
      setSavingRule(false);
    }
  };

  const handleRemoveRule = async () => {
    if (!activeSg || !ruleToRemove) return;
    const { rule, cidr, direction } = ruleToRemove;
    setSavingRule(true);
    try {
      await changeEc2SecurityGroupRule(activeSg.GroupId || activeSg.id, direction, {
        protocol: rule.IpProtocol,
        ...(rule.IpProtocol === '-1' ? {} : { from_port: rule.FromPort, to_port: rule.ToPort }),
        cidr,
      }, true);
      setActionMessage({ type: 'success', text: `Removed ${direction === 'ingress' ? 'inbound' : 'outbound'} rule for ${cidr}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to remove rule' });
    } finally {
      setSavingRule(false);
      setRuleToRemove(null);
    }
  };

  const filteredInstances = instances.filter((i) =>
    `${i.Name} ${i.InstanceId} ${i.InstanceType}`.toLowerCase().includes(instanceFilter.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {/* Header */}
      <Container
        header={
          <Header
            variant="h1"
            description="Virtual servers, VPC networking, and security groups in the cloud."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button variant="primary" onClick={() => setLaunchModalOpen(true)}>
                  Launch instances
                </Button>
              </SpaceBetween>
            }
          >
            Amazon EC2 & Amazon VPC
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

      </Container>

      <Container header={<Header variant="h2">Resource summary</Header>}>
        <KeyValuePairs columns={4} items={[
          { label: 'Instances', value: instances.length },
          { label: 'Running instances', value: instances.filter(instance => instance.State.Name === 'running').length },
          { label: 'VPCs', value: vpcs.length },
          { label: 'Security groups', value: securityGroups.length },
        ]} />
      </Container>
      {/* Main Tabs */}
      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          onTabChange?.(detail.activeTabId);
        }}
        tabs={[
          {
            label: `Instances (${instances.length})`,
            id: 'instances',
            content: (
              <SpaceBetween size="m">
                <TextFilter
                  filteringText={instanceFilter}
                  filteringPlaceholder="Find instances by ID, name..."
                  onChange={({ detail }) => { setInstanceFilter(detail.filteringText); setSelectedInstances([]); }}
                />

                <Table
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'Name',
                      cell: (item) => item.Name || '-',
                    },
                    {
                      id: 'id',
                      header: 'Instance ID',
                      cell: (item) => <Button variant="inline-link" onClick={() => setSelectedInstances([item])}>{item.InstanceId}</Button>,
                      width: 200,
                    },
                    {
                      id: 'state',
                      header: 'Instance state',
                      cell: (item) => (
                        <StatusIndicator type={instanceStateIndicator(item.State.Name)}>
                          {item.State.Name.charAt(0).toUpperCase() + item.State.Name.slice(1)}
                        </StatusIndicator>
                      ),
                      width: 150,
                    },
                    {
                      id: 'type',
                      header: 'Instance type',
                      cell: (item) => item.InstanceType,
                      width: 140,
                    },
                    {
                      id: 'az',
                      header: 'Availability Zone',
                      cell: (item) => item.AvailabilityZone || '-',
                      width: 160,
                    },
                    {
                      id: 'public_ip',
                      header: 'Public IPv4 address',
                      cell: (item) => item.PublicIpAddress || '-',
                      width: 170,
                    },
                    {
                      id: 'private_ip',
                      header: 'Private IPv4 addresses',
                      cell: (item) => item.PrivateIpAddress || '-',
                      width: 180,
                    },
                  ]}
                  items={filteredInstances}
                  selectionType="single"
                  selectedItems={selectedInstances}
                  onSelectionChange={({ detail }) => setSelectedInstances(detail.selectedItems)}
                  loading={loading}
                  loadingText="Loading instances"
                  empty={<Box textAlign="center">{instanceFilter ? 'No instances match your filter.' : 'No instances found.'}</Box>}
                />

                {activeInstance && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <ButtonDropdown
                              loading={changingState}
                              disabled={activeInstance.State.Name === 'terminated'}
                              items={[
                                { id: 'stop', text: 'Stop instance', disabled: activeInstance.State.Name !== 'running' },
                                { id: 'start', text: 'Start instance', disabled: activeInstance.State.Name !== 'stopped' },
                                { id: 'reboot', text: 'Reboot instance', disabled: activeInstance.State.Name !== 'running' },
                                { id: 'terminate', text: 'Terminate (delete) instance' },
                              ]}
                              onItemClick={({ detail }) => (detail.id === 'start' ? handleInstanceState('start') : setPendingStateAction(detail.id as 'stop' | 'reboot' | 'terminate'))}
                            >
                              Instance state
                            </ButtonDropdown>
                            <Button disabled={activeInstance.State.Name !== 'running'} onClick={() => { setCommandOutput(null); setRunCommandOpen(true); }}>
                              Run command
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Instance: {activeInstance.InstanceId}{activeInstance.Name ? ` (${activeInstance.Name})` : ''}
                      </Header>
                    }
                  >
                    <Tabs activeTabId={instanceDetailTab} onChange={({ detail }) => setInstanceDetailTab(detail.activeTabId)} tabs={[
                      { id: 'details', label: 'Details', content: (
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Instance ID', value: activeInstance.InstanceId },
                        { label: 'Instance state', value: <StatusIndicator type={instanceStateIndicator(activeInstance.State.Name)}>{activeInstance.State.Name}</StatusIndicator> },
                        { label: 'Instance type', value: activeInstance.InstanceType },
                        { label: 'AMI ID', value: activeInstance.ImageId || '-' },
                        { label: 'Public IPv4 address', value: activeInstance.PublicIpAddress || '-' },
                        { label: 'Private IPv4 addresses', value: activeInstance.PrivateIpAddress || '-' },
                        { label: 'Public DNS', value: activeInstance.PublicDnsName || '-' },
                        { label: 'Private IP DNS name (IPv4 only)', value: activeInstance.PrivateDnsName || '-' },
                        { label: 'VPC ID', value: activeInstance.VpcId || '-' },
                        { label: 'Subnet ID', value: activeInstance.SubnetId || '-' },
                        { label: 'Availability Zone', value: activeInstance.AvailabilityZone || '-' },
                        { label: 'Key pair assigned at launch', value: activeInstance.KeyName || '-' },
                        { label: 'IAM Role', value: activeInstance.IamInstanceProfile ? activeInstance.IamInstanceProfile.split('/').pop() : '-' },
                        { label: 'Architecture', value: activeInstance.Architecture || '-' },
                        { label: 'Launch time', value: formatAwsDate(activeInstance.LaunchTime) },
                      ]}
                    />
                      ) },
                      { id: 'security', label: 'Security', content: <SpaceBetween size="m">
                        <Header variant="h3">Attached security groups</Header>
                        <Table items={activeInstance.SecurityGroups || []} columnDefinitions={[
                          { id: 'id', header: 'Security group ID', cell: group => <Button variant="inline-link" onClick={() => {
                            const match = securityGroups.find(item => (item.GroupId || item.id) === group.GroupId);
                            setSelectedSgs(match ? [match] : []); setSelectedTabId('security_groups'); onTabChange?.('security_groups');
                          }}>{group.GroupId}</Button> },
                          { id: 'name', header: 'Security group name', cell: group => group.GroupName || '-' },
                        ]} empty="No security groups reported for this instance." />
                      </SpaceBetween> },
                      { id: 'networking', label: 'Networking', content: (
                    <div className="resource-flow" aria-label="Instance network path">
                      <section><strong>VPC</strong><p>{activeInstance.VpcId || '-'}</p></section><span aria-hidden="true">→</span>
                      <section><strong>Subnet</strong><p>{activeInstance.SubnetId || '-'}</p></section><span aria-hidden="true">→</span>
                      <section><strong>Instance</strong><p>{activeInstance.InstanceId}</p><p>{activeInstance.SecurityGroups?.map(group => group.GroupId).join(', ') || 'No security groups reported'}</p></section>
                    </div>
                      ) },
                      { id: 'tags', label: 'Tags', content: <Table items={activeInstance.Tags || []} columnDefinitions={[
                        { id: 'Key', header: 'Key', cell: tag => tag.Key },
                        { id: 'Value', header: 'Value', cell: tag => tag.Value },
                      ]} empty="No tags reported for this instance." /> },
                    ]} />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            label: `VPCs & Subnets (${vpcs.length} VPCs, ${subnets.length} Subnets)`,
            id: 'vpcs',
            content: (
              <SpaceBetween size="l">
                <Select ariaLabel="Network resource type" selectedOption={{ value: networkView, label: ({ vpcs: 'VPCs', subnets: 'Subnets', routes: 'Route tables', gateways: 'Gateways and endpoints' } as Record<string, string>)[networkView] }}
                  options={[{ value: 'vpcs', label: 'VPCs' }, { value: 'subnets', label: 'Subnets' }, { value: 'routes', label: 'Route tables' }, { value: 'gateways', label: 'Gateways and endpoints' }]}
                  onChange={({ detail }) => { setNetworkView(detail.selectedOption.value!); onTabChange?.(detail.selectedOption.value!); }} />
                {networkView === 'vpcs' && <>
                {/* VPCs */}
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" onClick={() => setCreateVpcOpen(true)}>
                          Create VPC
                        </Button>
                      }
                    >
                      Virtual Private Clouds ({vpcs.length})
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'name', header: 'Name', cell: (v: any) => tagName(v.Tags) || '-' },
                      { id: 'id', header: 'VPC ID', cell: (v: any) => v.VpcId || v.id },
                      { id: 'state', header: 'State', cell: (v: any) => v.State || v.state ? <StatusIndicator type={(v.State || v.state) === 'available' ? 'success' : 'pending'}>{(v.State || v.state).replace(/^./, (c: string) => c.toUpperCase())}</StatusIndicator> : '-' },
                      { id: 'cidr', header: 'IPv4 CIDR', cell: (v: any) => v.CidrBlock || v.cidr_block || '-' },
                      { id: 'default', header: 'Default VPC', cell: (v: any) => (v.IsDefault ? 'Yes' : 'No') },
                      {
                        id: 'action',
                        header: 'Actions',
                        cell: (v: any) => (
                          <Button variant="inline-link" disabled={v.IsDefault} onClick={() => setPendingDelete({ kind: 'VPC', id: v.VpcId || v.id })}>Delete VPC</Button>
                        ),
                      },
                    ]}
                    items={vpcs}
                  />
                </Container>

                </>}
                {networkView === 'subnets' && <>
                {/* Subnets */}
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" onClick={() => setCreateSubnetOpen(true)}>
                          Create subnet
                        </Button>
                      }
                    >
                      Subnets ({subnets.length})
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'name', header: 'Name', cell: (s: any) => tagName(s.Tags) || '-' },
                      { id: 'id', header: 'Subnet ID', cell: (s: any) => s.SubnetId || s.id },
                      { id: 'state', header: 'State', cell: (s: any) => s.State ? <StatusIndicator type={s.State === 'available' ? 'success' : 'pending'}>{s.State.replace(/^./, (c: string) => c.toUpperCase())}</StatusIndicator> : '-' },
                      { id: 'vpc', header: 'VPC', cell: (s: any) => s.VpcId || s.vpc_id || '-' },
                      { id: 'cidr', header: 'IPv4 CIDR', cell: (s: any) => s.CidrBlock || s.cidr_block || '-' },
                      { id: 'ips', header: 'Available IPv4 addresses', cell: (s: any) => s.AvailableIpAddressCount ?? '-' },
                      { id: 'az', header: 'Availability Zone', cell: (s: any) => s.AvailabilityZone || s.availability_zone || '-' },
                      { id: 'public', header: 'Auto-assign public IPv4 address', cell: (s: any) => s.MapPublicIpOnLaunch == null ? '-' : s.MapPublicIpOnLaunch ? 'Yes' : 'No' },
                      {
                        id: 'action',
                        header: 'Actions',
                        cell: (s: any) => (
                          <Button variant="inline-link" onClick={() => setPendingDelete({ kind: 'subnet', id: s.SubnetId || s.id })}>Delete subnet</Button>
                        ),
                      },
                    ]}
                    items={subnets}
                  />
                </Container>

                </>}
                {networkView === 'routes' && <>
                {/* Route Tables */}
                <Container
                  header={
                    <Header variant="h2">
                      Route tables ({routeTables.length})
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'name', header: 'Name', cell: (rt: any) => tagName(rt.Tags) || '-' },
                      { id: 'id', header: 'Route table ID', cell: (rt: any) => rt.RouteTableId || rt.id },
                      { id: 'associations', header: 'Explicit subnet associations', cell: (rt: any) => {
                        const subnetsAssociated = (rt.Associations || []).filter((a: any) => a.SubnetId).map((a: any) => a.SubnetId);
                        return subnetsAssociated.length ? subnetsAssociated.join(', ') : '-';
                      } },
                      { id: 'main', header: 'Main', cell: (rt: any) => ((rt.Associations || []).some((a: any) => a.Main) ? 'Yes' : 'No') },
                      { id: 'vpc', header: 'VPC', cell: (rt: any) => rt.VpcId || rt.vpc_id || '-' },
                      { id: 'routes', header: 'Routes', cell: (rt: any) => (rt.Routes || []).map((r: any) => `${r.DestinationCidrBlock || r.DestinationPrefixListId || r.DestinationIpv6CidrBlock} → ${r.GatewayId || r.NatGatewayId || r.NetworkInterfaceId || r.InstanceId || r.VpcPeeringConnectionId || r.TransitGatewayId || '-'}`).join('; ') || '-' },
                    ]}
                    items={routeTables}
                    empty={<Box textAlign="center" color="inherit">No route tables found</Box>}
                  />
                </Container>

                </>}
                {networkView === 'gateways' && <>
                {/* Internet Gateways & Endpoints Grid */}
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                  <Container header={<Header variant="h3">Internet gateways ({internetGateways.length})</Header>}>
                    <Table
                      columnDefinitions={[
                        { id: 'id', header: 'Internet gateway ID', cell: (igw: any) => igw.InternetGatewayId || igw.id },
                        // DescribeInternetGateways reports an attachment state of "available"; the console shows it as Attached.
                        { id: 'state', header: 'State', cell: (igw: any) => (igw.Attachments?.length ? <StatusIndicator type="success">Attached</StatusIndicator> : <StatusIndicator type="stopped">Detached</StatusIndicator>) },
                        { id: 'vpc', header: 'VPC ID', cell: (igw: any) => igw.Attachments?.[0]?.VpcId || '-' },
                      ]}
                      items={internetGateways}
                      empty={<Box textAlign="center" color="inherit">No internet gateways found</Box>}
                    />
                  </Container>

                  <Container header={<Header variant="h3">Endpoints ({vpcEndpoints.length})</Header>}>
                    <Table
                      columnDefinitions={[
                        { id: 'id', header: 'VPC endpoint ID', cell: (vpce: any) => vpce.VpcEndpointId || vpce.id },
                        { id: 'svc', header: 'Service name', cell: (vpce: any) => vpce.ServiceName || '-' },
                        { id: 'type', header: 'Endpoint type', cell: (vpce: any) => vpce.VpcEndpointType || '-' },
                        { id: 'state', header: 'Status', cell: (vpce: any) => vpce.State ? <StatusIndicator type={vpce.State.toLowerCase() === 'available' ? 'success' : 'pending'}>{vpce.State}</StatusIndicator> : '-' },
                      ]}
                      items={vpcEndpoints}
                      empty={<Box textAlign="center" color="inherit">No VPC endpoints found</Box>}
                    />
                  </Container>
                </Grid>
                </>}
              </SpaceBetween>
            ),
          },
          {
            label: `Security Groups (${securityGroups.length})`,
            id: 'security_groups',
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" onClick={() => setCreateSgOpen(true)}>
                          Create security group
                        </Button>
                      }
                    >
                      Security Groups
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'id', header: 'Security group ID', cell: (sg: any) => sg.GroupId || sg.id },
                      { id: 'name', header: 'Security group name', cell: (sg: any) => sg.GroupName || sg.name },
                      { id: 'vpc', header: 'VPC ID', cell: (sg: any) => sg.VpcId || sg.vpc_id || '-' },
                      { id: 'desc', header: 'Description', cell: (sg: any) => sg.Description || sg.description || '-' },
                      { id: 'inbound', header: 'Inbound rules count', cell: (sg: any) => `${expandSgRules(sg.IpPermissions || []).length} Permission entries` },
                      { id: 'outbound', header: 'Outbound rules count', cell: (sg: any) => `${expandSgRules(sg.IpPermissionsEgress || []).length} Permission entries` },
                      {
                        id: 'action',
                        header: 'Actions',
                        cell: (sg: any) => (
                          <Button variant="inline-link" disabled={(sg.GroupName || sg.name) === 'default'} onClick={() => setPendingDelete({ kind: 'security group', id: sg.GroupId || sg.id })}>Delete security group</Button>
                        ),
                      },
                    ]}
                    items={securityGroups}
                    selectionType="single"
                    selectedItems={selectedSgs}
                    onSelectionChange={({ detail }) => setSelectedSgs(detail.selectedItems)}
                  />
                </Container>

                {activeSg && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        description={activeSg.Description || activeSg.description}
                        actions={
                          <Button onClick={() => { setRuleDirection(selectedSgRuleTab === 'outbound' ? 'egress' : 'ingress'); setRuleCidr(selectedSgRuleTab === 'outbound' ? '0.0.0.0/0' : ''); setAddRuleOpen(true); }}>
                            {selectedSgRuleTab === 'outbound' ? 'Add outbound rule' : 'Add inbound rule'}
                          </Button>
                        }
                      >
                        {activeSg.GroupId || activeSg.id} - {activeSg.GroupName || activeSg.name}
                      </Header>
                    }
                  >
                    <Tabs
                      activeTabId={selectedSgRuleTab}
                      onChange={({ detail }) => setSelectedSgRuleTab(detail.activeTabId)}
                      tabs={(['inbound', 'outbound'] as const).map(kind => {
                        const direction = kind === 'inbound' ? 'ingress' : 'egress';
                        const rows = expandSgRules((kind === 'inbound' ? activeSg.IpPermissions : activeSg.IpPermissionsEgress) || []);
                        return {
                          label: `${kind === 'inbound' ? 'Inbound' : 'Outbound'} rules (${rows.length})`,
                          id: kind,
                          content: (
                            <Table
                              columnDefinitions={[
                                { id: 'type', header: 'Type', cell: (r: any) => r.type },
                                { id: 'proto', header: 'Protocol', cell: (r: any) => r.protocol },
                                { id: 'port', header: 'Port range', cell: (r: any) => r.portRange },
                                { id: 'peer', header: kind === 'inbound' ? 'Source' : 'Destination', cell: (r: any) => r.peer },
                                { id: 'desc', header: 'Description', cell: (r: any) => r.description || '-' },
                                { id: 'remove', header: 'Actions', cell: (r: any) => r.cidr
                                  ? <Button variant="inline-link" onClick={() => setRuleToRemove({ direction, rule: r.rule, cidr: r.cidr })}>Delete</Button>
                                  : '-' },
                              ]}
                              items={rows}
                              trackBy="id"
                              empty={<Box textAlign="center">No security group rules found.</Box>}
                            />
                          ),
                        };
                      })}
                    />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
        ]}
      />

      <ConfirmActionModal
        visible={Boolean(pendingStateAction)}
        header={pendingStateAction === 'terminate' ? 'Terminate (delete) instance?' : pendingStateAction === 'stop' ? 'Stop instance?' : 'Reboot instance?'}
        confirmLabel={pendingStateAction === 'terminate' ? 'Terminate (delete)' : pendingStateAction === 'stop' ? 'Stop' : 'Reboot'}
        loading={changingState}
        onConfirm={() => pendingStateAction && handleInstanceState(pendingStateAction)}
        onDismiss={() => setPendingStateAction(null)}
      >
        {pendingStateAction === 'terminate'
          ? <>Terminating {activeInstance?.InstanceId} deletes it. EBS volumes with DeleteOnTermination set are deleted too. This can't be undone.</>
          : pendingStateAction === 'stop'
            ? <>Stopping {activeInstance?.InstanceId} shuts it down. Instance store data is lost, and an auto-assigned public IPv4 address is released.</>
            : <>Rebooting {activeInstance?.InstanceId} restarts the operating system. The instance keeps its IP addresses and EBS data.</>}
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={Boolean(pendingDelete)}
        header={`Delete ${pendingDelete?.kind}`}
        confirmLabel="Delete"
        confirmationText="delete"
        loading={deleting}
        onConfirm={handleConfirmDelete}
        onDismiss={() => setPendingDelete(null)}
      >
        Delete {pendingDelete?.kind} {pendingDelete?.id}? This can't be undone.
        {pendingDelete?.kind === 'VPC' && ' The VPC must not have subnets, attached gateways, or non-default security groups, or AWS rejects the request with DependencyViolation.'}
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={Boolean(ruleToRemove)}
        header="Delete security group rule"
        confirmLabel="Delete"
        loading={savingRule}
        onConfirm={handleRemoveRule}
        onDismiss={() => setRuleToRemove(null)}
      >
        Remove the {ruleToRemove?.direction === 'ingress' ? 'inbound' : 'outbound'} rule for {ruleToRemove?.cidr} from {activeSg?.GroupId || activeSg?.id}?
      </ConfirmActionModal>

      <Modal
        visible={launchModalOpen}
        onDismiss={() => setLaunchModalOpen(false)}
        header="Launch an instance"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setLaunchModalOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={launching} disabled={!imageId || !instanceType} onClick={handleLaunchInstance}>Launch instance</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Name" description="Stored as the instance's Name tag.">
            <Input value={instanceName} onChange={({ detail }) => setInstanceName(detail.value)} placeholder="e.g. My Web Server" />
          </FormField>
          <FormField label="Amazon Machine Image (AMI)" description="Images returned by DescribeImages. Floci runs instances as local containers.">
            <Select
              selectedOption={imageId ? { value: imageId, label: images.find(image => image.ImageId === imageId)?.Name || imageId, description: imageId } : null}
              onChange={({ detail }) => setImageId(detail.selectedOption.value || '')}
              options={images.map(image => ({ value: image.ImageId, label: image.Name || image.ImageId, description: `${image.ImageId} · ${image.Architecture || ''}`, tags: image.Description ? [image.Description] : undefined }))}
              placeholder="Choose an AMI"
              filteringType="auto"
              empty="No images returned by DescribeImages"
            />
          </FormField>
          <FormField label="Instance type">
            <Select
              selectedOption={{ value: instanceType, label: instanceType }}
              onChange={({ detail }) => setInstanceType(detail.selectedOption.value || 't2.micro')}
              options={(instanceTypes.length ? instanceTypes : [{ InstanceType: 't2.micro' }]).map(type => ({
                value: type.InstanceType,
                label: type.InstanceType,
                description: type.VCpuInfo ? `${type.VCpuInfo.DefaultVCpus} vCPU · ${(type.MemoryInfo?.SizeInMiB || 0) / 1024} GiB Memory` : undefined,
              }))}
              filteringType="auto"
            />
          </FormField>
          <FormField label={<>Subnet <i>- optional</i></>} description="Without a subnet, the instance launches in the default VPC.">
            <Select
              selectedOption={launchSubnetId ? { value: launchSubnetId, label: launchSubnetId } : { value: '', label: 'No preference (default subnet)' }}
              onChange={({ detail }) => setLaunchSubnetId(detail.selectedOption.value || '')}
              options={[{ value: '', label: 'No preference (default subnet)' }, ...subnets.map(subnet => ({ value: subnet.SubnetId || subnet.id, label: subnet.SubnetId || subnet.id, description: `${subnet.VpcId || subnet.vpc_id} · ${subnet.AvailabilityZone || ''} · ${subnet.CidrBlock || subnet.cidr_block}` }))]}
              filteringType="auto"
            />
          </FormField>
          <FormField label={<>Security groups <i>- optional</i></>} description="Without a choice, the VPC's default security group is used.">
            <Multiselect
              selectedOptions={launchSgIds.map(id => ({ value: id, label: id }))}
              onChange={({ detail }) => setLaunchSgIds(detail.selectedOptions.map(option => option.value!))}
              options={securityGroups
                .filter(group => !launchSubnetId || (group.VpcId || group.vpc_id) === (subnets.find(subnet => (subnet.SubnetId || subnet.id) === launchSubnetId)?.VpcId))
                .map(group => ({ value: group.GroupId || group.id, label: group.GroupId || group.id, description: group.GroupName }))}
              placeholder="Choose security groups"
              filteringType="auto"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={runCommandOpen}
        onDismiss={() => setRunCommandOpen(false)}
        header={`Run command on ${activeInstance?.InstanceId}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRunCommandOpen(false)}>Close</Button>
              <Button variant="primary" loading={runningCommand} onClick={handleRunCommand}>Run</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box variant="small">Sends the AWS-RunShellScript document with Systems Manager SendCommand, then reads the output with GetCommandInvocation.</Box>
          <FormField label="Commands">
            <Textarea rows={6} value={commandScript} onChange={({ detail }) => setCommandScript(detail.value)} />
          </FormField>
          {commandOutput && (
            <Container header={<Header variant="h3">Output</Header>}>
              <SpaceBetween size="s">
                <KeyValuePairs columns={3} items={[
                  { label: 'Command ID', value: commandOutput.command_id || '-' },
                  { label: 'Status', value: <StatusIndicator type={commandOutput.status === 'Success' ? 'success' : ['Pending', 'InProgress', 'Delayed'].includes(commandOutput.status) ? 'in-progress' : 'error'}>{commandOutput.status || '-'}</StatusIndicator> },
                  { label: 'Response code', value: commandOutput.response_code ?? '-' },
                ]} />
                {commandOutput.stdout && <Box variant="code"><pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{commandOutput.stdout}</pre></Box>}
                {commandOutput.stderr && <Alert type="error" header="Standard error">{commandOutput.stderr}</Alert>}
              </SpaceBetween>
            </Container>
          )}
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createVpcOpen}
        onDismiss={() => setCreateVpcOpen(false)}
        header="Create VPC"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateVpcOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingVpc} onClick={handleCreateVpc}>Create VPC</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label={<>Name tag <i>- optional</i></>}>
            <Input value={vpcName} onChange={({ detail }) => setVpcName(detail.value)} placeholder="my-vpc-01" />
          </FormField>
          <FormField label="IPv4 CIDR" constraintText="CIDR block size must be between /16 and /28.">
            <Input value={vpcCidr} onChange={({ detail }) => setVpcCidr(detail.value)} placeholder="10.0.0.0/16" />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createSubnetOpen}
        onDismiss={() => setCreateSubnetOpen(false)}
        header="Create subnet"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateSubnetOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingSubnet} disabled={!subnetVpcId} onClick={handleCreateSubnet}>Create subnet</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="VPC ID">
            <Select
              selectedOption={subnetVpcId ? { value: subnetVpcId, label: subnetVpcId } : null}
              onChange={({ detail }) => setSubnetVpcId(detail.selectedOption.value || '')}
              options={vpcs.map(vpc => ({ value: vpc.VpcId || vpc.id, label: vpc.VpcId || vpc.id, description: `${tagName(vpc.Tags) || ''} ${vpc.CidrBlock || vpc.cidr_block || ''}`.trim() }))}
              placeholder="Select a VPC"
            />
          </FormField>
          <FormField label="Subnet name">
            <Input value={subnetName} onChange={({ detail }) => setSubnetName(detail.value)} placeholder="my-subnet-01" />
          </FormField>
          <FormField label="Availability Zone">
            <Select
              selectedOption={subnetAz ? { value: subnetAz, label: subnetAz } : { value: '', label: 'No preference' }}
              onChange={({ detail }) => setSubnetAz(detail.selectedOption.value || '')}
              options={[{ value: '', label: 'No preference' }, ...availabilityZones.map(zone => ({ value: zone, label: zone }))]}
            />
          </FormField>
          <FormField label="IPv4 subnet CIDR block" constraintText="Must be within the VPC CIDR, between /16 and /28.">
            <Input value={subnetCidr} onChange={({ detail }) => setSubnetCidr(detail.value)} placeholder="10.0.1.0/24" />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={createSgOpen}
        onDismiss={() => setCreateSgOpen(false)}
        header="Create security group"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateSgOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creatingSg} disabled={!sgName.trim() || !sgDescription.trim() || !sgVpcId} onClick={handleCreateSg}>Create security group</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Security group name" constraintText="Up to 255 characters. Can't start with sg-.">
            <Input value={sgName} onChange={({ detail }) => setSgName(detail.value)} placeholder="MyWebServerGroup" />
          </FormField>
          <FormField label="Description" constraintText="Required by CreateSecurityGroup.">
            <Input value={sgDescription} onChange={({ detail }) => setSgDescription(detail.value)} placeholder="Allows SSH access to developers" />
          </FormField>
          <FormField label="VPC">
            <Select
              selectedOption={sgVpcId ? { value: sgVpcId, label: sgVpcId } : null}
              onChange={({ detail }) => setSgVpcId(detail.selectedOption.value || '')}
              options={vpcs.map(vpc => ({ value: vpc.VpcId || vpc.id, label: vpc.VpcId || vpc.id, description: `${tagName(vpc.Tags) || ''} ${vpc.CidrBlock || vpc.cidr_block || ''}`.trim() }))}
              placeholder="Select a VPC"
            />
          </FormField>
          <Box variant="small">New security groups have no inbound rules and one outbound rule that allows all traffic.</Box>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={addRuleOpen}
        onDismiss={() => setAddRuleOpen(false)}
        header={`Add ${ruleDirection === 'ingress' ? 'inbound' : 'outbound'} rule`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddRuleOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={savingRule} disabled={!ruleCidr.trim()} onClick={handleAddRule}>Save rules</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Type">
            <Select
              selectedOption={ruleType}
              onChange={({ detail }) => {
                const option = detail.selectedOption as any;
                setRuleType(option);
                if (option.port) setRulePort(option.port);
              }}
              options={[
                { label: 'Custom TCP', value: 'custom-tcp', port: '', proto: 'tcp' },
                { label: 'Custom UDP', value: 'custom-udp', port: '', proto: 'udp' },
                { label: 'All traffic', value: 'all', port: '', proto: '-1' },
                { label: 'SSH', value: 'ssh', port: '22', proto: 'tcp' },
                { label: 'HTTP', value: 'http', port: '80', proto: 'tcp' },
                { label: 'HTTPS', value: 'https', port: '443', proto: 'tcp' },
                { label: 'RDP', value: 'rdp', port: '3389', proto: 'tcp' },
                { label: 'MYSQL/Aurora', value: 'mysql', port: '3306', proto: 'tcp' },
                { label: 'PostgreSQL', value: 'pg', port: '5432', proto: 'tcp' },
              ] as any}
            />
          </FormField>
          <FormField label="Port range" description={ruleType.proto === '-1' ? 'All traffic covers every protocol and port.' : undefined}>
            <Input
              value={ruleType.proto === '-1' ? 'All' : rulePort}
              disabled={ruleType.proto === '-1' || !ruleType.value.startsWith('custom')}
              inputMode="numeric"
              onChange={({ detail }) => setRulePort(detail.value)}
              placeholder="8080"
            />
          </FormField>
          <FormField label={ruleDirection === 'ingress' ? 'Source (CIDR)' : 'Destination (CIDR)'} description="An IPv4 CIDR block, for example 203.0.113.0/24. 0.0.0.0/0 allows any IPv4 address.">
            <Input value={ruleCidr} onChange={({ detail }) => setRuleCidr(detail.value)} placeholder="0.0.0.0/0" />
          </FormField>
          <FormField label={<>Description <i>- optional</i></>}>
            <Input value={ruleDescription} onChange={({ detail }) => setRuleDescription(detail.value)} placeholder="e.g. SSH for admin desktop" />
          </FormField>
          <Box variant="small">Floci stores and returns security group rules, but doesn&apos;t enforce them unless it runs with FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true.</Box>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
