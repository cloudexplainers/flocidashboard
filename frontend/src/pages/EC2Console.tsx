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
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import {
  fetchServiceInventory,
  executeServiceAction,
  createEc2Vpc,
  deleteEc2Vpc,
  createEc2Subnet,
  deleteEc2Subnet,
  createEc2SecurityGroup,
  deleteEc2SecurityGroup,
  changeEc2SecurityGroupRule,
  runEc2InstanceCommand,
} from '../api/client';
import { CodeSnippet } from '../components/CodeSnippet';

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
}

interface EC2ConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const EC2Console: React.FC<EC2ConsoleProps> = ({ activeTab, onTabChange }) => {
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab === 'subnets' ? 'vpcs' : activeTab || 'instances');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab === 'subnets' ? 'vpcs' : activeTab);
      if (activeTab === 'subnets') setNetworkView('subnets');
    }
  }, [activeTab]);

  // Instances State
  const [instances, setInstances] = useState<InstanceItem[]>([]);
  const [selectedInstances, setSelectedInstances] = useState<InstanceItem[]>([]);
  const [instanceFilter, setInstanceFilter] = useState('');
  const [instanceDetailTab, setInstanceDetailTab] = useState('details');
  const [networkView, setNetworkView] = useState(activeTab === 'subnets' ? 'subnets' : 'vpcs');
  const [launchModalOpen, setLaunchModalOpen] = useState(false);
  const [instanceType, setInstanceType] = useState({ label: 't3.micro (2 vCPU, 1 GiB RAM)', value: 't3.micro' });
  const [instanceName, setInstanceName] = useState('web-server-01');
  const [launching, setLaunching] = useState(false);

  // Run Command (SSM) Modal
  const [runCommandOpen, setRunCommandOpen] = useState(false);
  const [commandScript, setCommandScript] = useState('echo "Hello from Floci EC2 instance $(hostname)"\nuname -a\nuptime');
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
  const [vpcName, setVpcName] = useState('production-vpc');
  const [creatingVpc, setCreatingVpc] = useState(false);

  const [createSubnetOpen, setCreateSubnetOpen] = useState(false);
  const [subnetCidr, setSubnetCidr] = useState('10.0.1.0/24');
  const [subnetAz, setSubnetAz] = useState('us-east-1a');
  const [subnetVpcId, setSubnetVpcId] = useState('');
  const [subnetName, setSubnetName] = useState('public-subnet-1a');
  const [creatingSubnet, setCreatingSubnet] = useState(false);

  // Security Groups State
  const [securityGroups, setSecurityGroups] = useState<any[]>([]);
  const [selectedSgs, setSelectedSgs] = useState<any[]>([]);
  const [createSgOpen, setCreateSgOpen] = useState(false);
  const [sgName, setSgName] = useState('web-dmz-sg');
  const [sgDescription, setSgDescription] = useState('Allow HTTP and HTTPS inbound traffic');
  const [sgVpcId, setSgVpcId] = useState('');
  const [creatingSg, setCreatingSg] = useState(false);

  // Add Rule Modal
  const [addRuleOpen, setAddRuleOpen] = useState(false);
  const [ruleType, setRuleType] = useState<{ label: string; value: string; port: number; proto: string }>({
    label: 'HTTP (80)',
    value: 'http',
    port: 80,
    proto: 'tcp',
  });
  const [ruleCidr, setRuleCidr] = useState('0.0.0.0/0');
  const [savingRule, setSavingRule] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const ec2Data = await fetchServiceInventory('ec2');

      // Map instances
      const instList = (ec2Data.instances || ec2Data.Reservations?.flatMap((r: any) => r.Instances) || []).map((inst: any) => ({
        InstanceId: inst.InstanceId || inst.id,
        Name: (inst.Tags || inst.tags)?.find((t: any) => t.Key === 'Name')?.Value || inst.Name || '—',
        State: typeof inst.State === 'object' ? inst.State : { Name: inst.State || inst.state || 'unknown', Code: inst.state_code },
        InstanceType: inst.InstanceType || inst.instance_type || 'Unavailable',
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
        setSubnetVpcId(vpcList[0].VpcId || vpcList[0].id);
        setSgVpcId(vpcList[0].VpcId || vpcList[0].id);
      }

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
    setLaunching(true);
    setActionMessage(null);
    try {
      await executeServiceAction('ec2', 'run_instances', {
        InstanceType: instanceType.value,
        MinCount: 1,
        MaxCount: 1,
        ImageId: 'ami-0c55b159cbfafe1f0',
        TagSpecifications: [
          {
            ResourceType: 'instance',
            Tags: [{ Key: 'Name', Value: instanceName }],
          },
        ],
      });
      setActionMessage({ type: 'success', text: `Instance "${instanceName}" launched successfully.` });
      setLaunchModalOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to launch instance' });
    } finally {
      setLaunching(false);
    }
  };

  const handleInstanceState = async (action: 'start' | 'stop' | 'terminate' | 'reboot') => {
    if (!activeInstance) return;
    try {
      if (action === 'start') await executeServiceAction('ec2', 'start_instances', { InstanceIds: [activeInstance.InstanceId] });
      if (action === 'stop') await executeServiceAction('ec2', 'stop_instances', { InstanceIds: [activeInstance.InstanceId] });
      if (action === 'terminate') await executeServiceAction('ec2', 'terminate_instances', { InstanceIds: [activeInstance.InstanceId] });
      if (action === 'reboot') await executeServiceAction('ec2', 'reboot_instances', { InstanceIds: [activeInstance.InstanceId] });

      setActionMessage({ type: 'success', text: `Instance ${activeInstance.InstanceId} action "${action}" dispatched.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || `Failed to ${action} instance` });
    }
  };

  const handleRunCommand = async () => {
    if (!activeInstance || !commandScript.trim()) return;
    setRunningCommand(true);
    setCommandOutput(null);
    try {
      const res = await runEc2InstanceCommand(activeInstance.InstanceId, commandScript.trim());
      setCommandOutput(res);
      setActionMessage({ type: 'success', text: 'Command executed on instance.' });
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

  const handleDeleteVpc = async (vpcId: string) => {
    try {
      await deleteEc2Vpc(vpcId);
      setActionMessage({ type: 'success', text: `VPC ${vpcId} deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete VPC' });
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

  const handleDeleteSubnet = async (subnetId: string) => {
    try {
      await deleteEc2Subnet(subnetId);
      setActionMessage({ type: 'success', text: `Subnet ${subnetId} deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete subnet' });
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

  const handleDeleteSg = async (groupId: string) => {
    try {
      await deleteEc2SecurityGroup(groupId);
      setActionMessage({ type: 'success', text: `Security Group ${groupId} deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete security group' });
    }
  };

  const handleAddRule = async () => {
    if (!activeSg) return;
    setSavingRule(true);
    try {
      const ruleObj = {
        IpProtocol: ruleType.proto,
        FromPort: ruleType.port,
        ToPort: ruleType.port,
        IpRanges: [{ CidrIp: ruleCidr.trim() }],
      };
      await changeEc2SecurityGroupRule(activeSg.GroupId, 'ingress', ruleObj, false);
      setActionMessage({ type: 'success', text: 'Inbound rule added to security group.' });
      setAddRuleOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to add rule' });
    } finally {
      setSavingRule(false);
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
                <Button variant="primary" iconName="add-plus" onClick={() => setLaunchModalOpen(true)}>
                  Launch Instance
                </Button>
              </SpaceBetween>
            }
          >
            Amazon EC2 & Virtual Private Cloud (VPC)
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
                      cell: (item) => <strong>{item.Name || '—'}</strong>,
                    },
                    {
                      id: 'id',
                      header: 'Instance ID',
                      cell: (item) => <Button variant="inline-link" onClick={() => setSelectedInstances([item])}>{item.InstanceId}</Button>,
                      width: 200,
                    },
                    {
                      id: 'state',
                      header: 'Instance State',
                      cell: (item) => (
                        <StatusIndicator type={item.State.Name === 'running' ? 'success' : 'stopped'}>
                          {item.State.Name}
                        </StatusIndicator>
                      ),
                      width: 150,
                    },
                    {
                      id: 'type',
                      header: 'Instance Type',
                      cell: (item) => <Badge color="blue">{item.InstanceType}</Badge>,
                      width: 140,
                    },
                    {
                      id: 'public_ip',
                      header: 'Public IPv4',
                      cell: (item) => <code>{item.PublicIpAddress}</code>,
                      width: 160,
                    },
                    {
                      id: 'private_ip',
                      header: 'Private IPv4',
                      cell: (item) => <code>{item.PrivateIpAddress}</code>,
                      width: 160,
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
                              items={[
                                { id: 'start', text: 'Start Instance', disabled: activeInstance.State.Name === 'running' },
                                { id: 'stop', text: 'Stop Instance', disabled: activeInstance.State.Name === 'stopped' },
                                { id: 'reboot', text: 'Reboot Instance' },
                                { id: 'terminate', text: 'Terminate Instance' },
                              ]}
                              onItemClick={({ detail }) => handleInstanceState(detail.id as any)}
                            >
                              Instance State
                            </ButtonDropdown>
                            <Button variant="primary" iconName="contact" onClick={() => setRunCommandOpen(true)}>
                              Execute Shell Script (SSM)
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Instance: {activeInstance.InstanceId} ({activeInstance.Name})
                      </Header>
                    }
                  >
                    <Tabs activeTabId={instanceDetailTab} onChange={({ detail }) => setInstanceDetailTab(detail.activeTabId)} tabs={[
                      { id: 'details', label: 'Details', content: (
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Instance ID', value: activeInstance.InstanceId },
                        { label: 'Instance Type', value: activeInstance.InstanceType },
                        { label: 'State', value: activeInstance.State.Name },
                        { label: 'Public IP', value: activeInstance.PublicIpAddress || 'None' },
                        { label: 'Private IP', value: activeInstance.PrivateIpAddress || 'None' },
                        { label: 'VPC ID', value: activeInstance.VpcId || 'Unavailable' },
                        { label: 'Subnet ID', value: activeInstance.SubnetId || 'Unavailable' },
                        { label: 'Key Pair Name', value: activeInstance.KeyName || 'None' },
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
                          { id: 'name', header: 'Name', cell: group => group.GroupName || '—' },
                        ]} empty="No security groups reported for this instance." />
                      </SpaceBetween> },
                      { id: 'networking', label: 'Networking', content: (
                    <div className="resource-flow" aria-label="Instance network path">
                      <section><strong>VPC</strong><p>{activeInstance.VpcId || 'Unavailable'}</p></section><span aria-hidden="true">→</span>
                      <section><strong>Subnet</strong><p>{activeInstance.SubnetId || 'Unavailable'}</p></section><span aria-hidden="true">→</span>
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
                  onChange={({ detail }) => setNetworkView(detail.selectedOption.value!)} />
                {networkView === 'vpcs' && <>
                {/* VPCs */}
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateVpcOpen(true)}>
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
                      { id: 'id', header: 'VPC ID', cell: (v: any) => <strong>{v.VpcId || v.id}</strong> },
                      { id: 'cidr', header: 'IPv4 CIDR Block', cell: (v: any) => <code>{v.CidrBlock || v.cidr_block}</code> },
                      { id: 'state', header: 'State', cell: () => <StatusIndicator type="success">available</StatusIndicator> },
                      { id: 'default', header: 'Default VPC', cell: (v: any) => (v.IsDefault ? <Badge color="blue">Default</Badge> : 'Custom') },
                      {
                        id: 'action',
                        header: 'Action',
                        cell: (v: any) => (
                          <Button onClick={() => handleDeleteVpc(v.VpcId || v.id)}>Delete</Button>
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
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateSubnetOpen(true)}>
                          Create Subnet
                        </Button>
                      }
                    >
                      Subnets ({subnets.length})
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'id', header: 'Subnet ID', cell: (s: any) => <strong>{s.SubnetId || s.id}</strong> },
                      { id: 'vpc', header: 'VPC ID', cell: (s: any) => <code>{s.VpcId || s.vpc_id}</code> },
                      { id: 'cidr', header: 'IPv4 CIDR Block', cell: (s: any) => <code>{s.CidrBlock || s.cidr_block}</code> },
                      { id: 'az', header: 'Availability Zone', cell: (s: any) => s.AvailabilityZone || s.availability_zone || 'Unavailable' },
                      {
                        id: 'action',
                        header: 'Action',
                        cell: (s: any) => (
                          <Button onClick={() => handleDeleteSubnet(s.SubnetId || s.id)}>Delete</Button>
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
                      Route Tables ({routeTables.length})
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'id', header: 'Route Table ID', cell: (rt: any) => <strong>{rt.RouteTableId || rt.id}</strong> },
                      { id: 'vpc', header: 'VPC ID', cell: (rt: any) => <code>{rt.VpcId || rt.vpc_id}</code> },
                      { id: 'routes', header: 'Routes Count', cell: (rt: any) => (rt.Routes?.length || 0) },
                      { id: 'associations', header: 'Associations', cell: (rt: any) => (rt.Associations?.length ? `${rt.Associations.length} Subnet(s)` : 'Main') },
                    ]}
                    items={routeTables}
                    empty={<Box textAlign="center" color="inherit">No route tables found</Box>}
                  />
                </Container>

                </>}
                {networkView === 'gateways' && <>
                {/* Internet Gateways & Endpoints Grid */}
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                  <Container header={<Header variant="h3">Internet Gateways ({internetGateways.length})</Header>}>
                    <Table
                      columnDefinitions={[
                        { id: 'id', header: 'IGW ID', cell: (igw: any) => <strong>{igw.InternetGatewayId || igw.id}</strong> },
                        { id: 'vpc', header: 'Attached VPC', cell: (igw: any) => <code>{igw.Attachments?.[0]?.VpcId || 'Detached'}</code> },
                        { id: 'state', header: 'State', cell: (igw: any) => <StatusIndicator type="success">{igw.Attachments?.[0]?.State || 'available'}</StatusIndicator> },
                      ]}
                      items={internetGateways}
                      empty={<Box textAlign="center" color="inherit">No internet gateways found</Box>}
                    />
                  </Container>

                  <Container header={<Header variant="h3">VPC Endpoints ({vpcEndpoints.length})</Header>}>
                    <Table
                      columnDefinitions={[
                        { id: 'id', header: 'Endpoint ID', cell: (vpce: any) => <strong>{vpce.VpcEndpointId || vpce.id}</strong> },
                        { id: 'svc', header: 'Service Name', cell: (vpce: any) => <code>{vpce.ServiceName}</code> },
                        { id: 'type', header: 'Type', cell: (vpce: any) => <Badge color="blue">{vpce.VpcEndpointType || 'Gateway'}</Badge> },
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
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateSgOpen(true)}>
                          Create Security Group
                        </Button>
                      }
                    >
                      Security Groups
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'name', header: 'Group Name', cell: (sg: any) => <strong>{sg.GroupName || sg.name}</strong> },
                      { id: 'id', header: 'Security Group ID', cell: (sg: any) => <code>{sg.GroupId || sg.id}</code> },
                      { id: 'vpc', header: 'VPC ID', cell: (sg: any) => <code>{sg.VpcId || sg.vpc_id}</code> },
                      { id: 'desc', header: 'Description', cell: (sg: any) => sg.Description || sg.description },
                      {
                        id: 'action',
                        header: 'Action',
                        cell: (sg: any) => (
                          <Button onClick={() => handleDeleteSg(sg.GroupId || sg.id)}>Delete</Button>
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
                        description={`Rules for ${activeSg.GroupName || activeSg.name}`}
                        actions={
                          <Button variant="primary" iconName="add-plus" onClick={() => setAddRuleOpen(true)}>
                            Add Inbound Rule
                          </Button>
                        }
                      >
                        Inbound & Outbound Rules: {activeSg.GroupName || activeSg.name}
                      </Header>
                    }
                  >
                    <Tabs
                      tabs={[
                        {
                          label: 'Inbound Rules (Ingress)',
                          id: 'inbound',
                          content: (
                            <Table
                              columnDefinitions={[
                                { id: 'proto', header: 'Protocol', cell: (r: any) => <Badge color="blue">{r.IpProtocol === '-1' ? 'All Traffic' : r.IpProtocol.toUpperCase()}</Badge> },
                                { id: 'port', header: 'Port Range', cell: (r: any) => (r.FromPort ? `${r.FromPort} - ${r.ToPort}` : 'All') },
                                { id: 'source', header: 'Source CIDR', cell: (r: any) => <code>{r.IpRanges?.map((i: any) => i.CidrIp).join(', ') || '0.0.0.0/0'}</code> },
                              ]}
                              items={activeSg.IpPermissions || []}
                              empty={<Box textAlign="center">No inbound rules configured.</Box>}
                            />
                          ),
                        },
                        {
                          label: 'Outbound Rules (Egress)',
                          id: 'outbound',
                          content: (
                            <Table
                              columnDefinitions={[
                                { id: 'proto', header: 'Protocol', cell: (r: any) => <Badge color="green">{r.IpProtocol === '-1' ? 'All Traffic' : r.IpProtocol.toUpperCase()}</Badge> },
                                { id: 'port', header: 'Port Range', cell: (r: any) => (r.FromPort ? `${r.FromPort} - ${r.ToPort}` : 'All') },
                                { id: 'dest', header: 'Destination CIDR', cell: (r: any) => <code>{r.IpRanges?.map((i: any) => i.CidrIp).join(', ') || '0.0.0.0/0'}</code> },
                              ]}
                              items={activeSg.IpPermissionsEgress || []}
                              empty={<Box textAlign="center">No outbound rules configured.</Box>}
                            />
                          ),
                        },
                      ]}
                    />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
        ]}
      />

      {/* Launch Instance Modal */}
      <Modal
        visible={launchModalOpen}
        onDismiss={() => setLaunchModalOpen(false)}
        header="Launch Virtual Server (EC2)"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setLaunchModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={launching} onClick={handleLaunchInstance}>
                Launch Instance
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Instance Name">
            <Input
              value={instanceName}
              onChange={({ detail }) => setInstanceName(detail.value)}
              placeholder="web-server-01"
            />
          </FormField>

          <FormField label="Instance Type">
            <Select
              selectedOption={instanceType}
              onChange={({ detail }) => setInstanceType(detail.selectedOption as any)}
              options={[
                { label: 't3.micro (2 vCPU, 1 GiB RAM)', value: 't3.micro' },
                { label: 't3.small (2 vCPU, 2 GiB RAM)', value: 't3.small' },
                { label: 't3.medium (2 vCPU, 4 GiB RAM)', value: 't3.medium' },
                { label: 'm5.large (2 vCPU, 8 GiB RAM)', value: 'm5.large' },
                { label: 'c5.large (2 vCPU, 4 GiB RAM, Compute-Optimized)', value: 'c5.large' },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Execute SSM Command Modal */}
      <Modal
        visible={runCommandOpen}
        onDismiss={() => setRunCommandOpen(false)}
        header={`Execute Shell Script on ${activeInstance?.InstanceId}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRunCommandOpen(false)}>
                Close
              </Button>
              <Button variant="primary" iconName="caret-right-filled" loading={runningCommand} onClick={handleRunCommand}>
                Execute Script
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Shell Commands (Bash)">
            <Textarea
              rows={6}
              value={commandScript}
              onChange={({ detail }) => setCommandScript(detail.value)}
            />
          </FormField>

          {commandOutput && (
            <Container header={<Header variant="h3">Execution Result</Header>}>
              <CodeSnippet language="json" code={JSON.stringify(commandOutput, null, 2)} />
            </Container>
          )}
        </SpaceBetween>
      </Modal>

      {/* Create VPC Modal */}
      <Modal
        visible={createVpcOpen}
        onDismiss={() => setCreateVpcOpen(false)}
        header="Create Virtual Private Cloud (VPC)"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateVpcOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingVpc} onClick={handleCreateVpc}>
                Create VPC
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Name Tag (Optional)">
            <Input
              value={vpcName}
              onChange={({ detail }) => setVpcName(detail.value)}
              placeholder="production-vpc"
            />
          </FormField>
          <FormField label="IPv4 CIDR Block" description="Private IP address block for this VPC.">
            <Input
              value={vpcCidr}
              onChange={({ detail }) => setVpcCidr(detail.value)}
              placeholder="10.0.0.0/16"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Subnet Modal */}
      <Modal
        visible={createSubnetOpen}
        onDismiss={() => setCreateSubnetOpen(false)}
        header="Create Subnet"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateSubnetOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingSubnet} onClick={handleCreateSubnet}>
                Create Subnet
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Subnet Name (Optional)">
            <Input value={subnetName} onChange={({ detail }) => setSubnetName(detail.value)} placeholder="public-subnet-1a" />
          </FormField>
          <FormField label="VPC ID">
            <Input value={subnetVpcId} onChange={({ detail }) => setSubnetVpcId(detail.value)} />
          </FormField>
          <FormField label="IPv4 CIDR Block">
            <Input value={subnetCidr} onChange={({ detail }) => setSubnetCidr(detail.value)} placeholder="10.0.1.0/24" />
          </FormField>
          <FormField label="Availability Zone">
            <Input value={subnetAz} onChange={({ detail }) => setSubnetAz(detail.value)} placeholder="us-east-1a" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Security Group Modal */}
      <Modal
        visible={createSgOpen}
        onDismiss={() => setCreateSgOpen(false)}
        header="Create Security Group"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateSgOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingSg} onClick={handleCreateSg}>
                Create Security Group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Security Group Name">
            <Input value={sgName} onChange={({ detail }) => setSgName(detail.value)} placeholder="web-dmz-sg" />
          </FormField>
          <FormField label="Description">
            <Input value={sgDescription} onChange={({ detail }) => setSgDescription(detail.value)} />
          </FormField>
          <FormField label="VPC ID">
            <Input value={sgVpcId} onChange={({ detail }) => setSgVpcId(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Add Inbound Rule Modal */}
      <Modal
        visible={addRuleOpen}
        onDismiss={() => setAddRuleOpen(false)}
        header={`Add Inbound Rule to ${activeSg?.GroupName || activeSg?.name}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddRuleOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingRule} onClick={handleAddRule}>
                Save Rule
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Rule Type">
            <Select
              selectedOption={ruleType}
              onChange={({ detail }) => {
                const opt = detail.selectedOption;
                const map: Record<string, { port: number; proto: string }> = {
                  http: { port: 80, proto: 'tcp' },
                  https: { port: 443, proto: 'tcp' },
                  ssh: { port: 22, proto: 'tcp' },
                  pg: { port: 5432, proto: 'tcp' },
                  mysql: { port: 3306, proto: 'tcp' },
                  custom: { port: 8080, proto: 'tcp' },
                };
                const val = opt.value || 'http';
                const meta = map[val] || { port: 80, proto: 'tcp' };
                setRuleType({
                  label: opt.label || 'HTTP (80)',
                  value: val,
                  port: meta.port,
                  proto: meta.proto,
                });
              }}
              options={[
                { label: 'HTTP (80)', value: 'http' },
                { label: 'HTTPS (443)', value: 'https' },
                { label: 'SSH (22)', value: 'ssh' },
                { label: 'PostgreSQL (5432)', value: 'pg' },
                { label: 'MySQL / Aurora (3306)', value: 'mysql' },
                { label: 'Custom TCP (8080)', value: 'custom' },
              ]}
            />
          </FormField>

          <FormField label="Source CIDR">
            <Input value={ruleCidr} onChange={({ detail }) => setRuleCidr(detail.value)} placeholder="0.0.0.0/0" />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
