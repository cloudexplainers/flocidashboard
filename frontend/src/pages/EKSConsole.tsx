import { EKSHealth, EKSNetworking, EKSNodegroupDetails } from './EKSResourceDetails';
import { eksClusterPath, eksNodegroupPath, normalizeEksClusters, parseEksRoute } from './eksResources';
import { ResourceValue } from '../components/ResourceDetails';
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
import Select from '@cloudscape-design/components/select';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import Grid from '@cloudscape-design/components/grid';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import {
  createEksCluster,
  deleteEksCluster,
  createEksNodegroup,
  deleteEksNodegroup,
  createEksFargateProfile,
  deleteEksFargateProfile,
  fetchEksKubeconfig,
} from '../api/client';
import { CodeSnippet } from '../components/CodeSnippet';

interface ClusterItem {
  [key: string]: any;
  platformVersion?: string;
  name: string;
  arn?: string;
  status: string;
  version?: string;
  endpoint?: string;
  roleArn?: string;
  createdAt?: string;
  resourcesVpcConfig?: {
    subnetIds?: string[];
    securityGroupIds?: string[];
    vpcId?: string;
  };
  nodegroups?: any[];
  fargate_profiles?: any[];
}

interface EKSConsoleProps {
  activeTab?: string;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  onTabChange?: (tabId: string) => void;
}

const K8S_VERSIONS = [
  { label: '1.30 (Local runtime)', value: '1.30' },
  { label: '1.29', value: '1.29' },
  { label: '1.28', value: '1.28' },
  { label: '1.27', value: '1.27' },
];

export const EKSConsole: React.FC<EKSConsoleProps> = ({ activeTab, onTabChange, resourcePath = 'eks', onNavigate }) => {
  const route = parseEksRoute(resourcePath);
  const navigate = (path: string) => onNavigate?.(path);
  const [loadError, setLoadError] = useState('');
  const [kubeconfigError, setKubeconfigError] = useState('');
  const [clusters, setClusters] = useState<ClusterItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedClusters, setSelectedClusters] = useState<ClusterItem[]>([]);
  const [filterText, setFilterText] = useState('');
  const [selectedTabId, setSelectedTabId] = useState(activeTab === 'clusters' ? 'overview' : activeTab || 'overview');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error' | 'info'; text: string } | null>(null);

  useEffect(() => {
    const tab = new URLSearchParams(resourcePath.split('?')[1] || '').get('tab');
    setSelectedTabId(route.cluster ? tab || 'overview' : activeTab === 'clusters' ? 'overview' : activeTab || 'overview');
  }, [activeTab, resourcePath]);

  // Create Cluster Modal
  const [createClusterOpen, setCreateClusterOpen] = useState(false);
  const [clusterName, setClusterName] = useState('');
  const [clusterRoleArn, setClusterRoleArn] = useState('arn:aws:iam::000000000000:role/eks-cluster-role');
  const [clusterVersion, setClusterVersion] = useState(K8S_VERSIONS[0]);
  const [subnetIds, setSubnetIds] = useState('subnet-01234567,subnet-89abcdef');
  const [securityGroupIds, setSecurityGroupIds] = useState('sg-01234567');
  const [creatingCluster, setCreatingCluster] = useState(false);

  // Delete Cluster Modal
  const [deleteClusterOpen, setDeleteClusterOpen] = useState(false);
  const [deletingCluster, setDeletingCluster] = useState(false);

  // Create Node Group Modal
  const [createNodegroupOpen, setCreateNodegroupOpen] = useState(false);
  const [nodegroupName, setNodegroupName] = useState('');
  const [nodeRoleArn, setNodeRoleArn] = useState('arn:aws:iam::000000000000:role/eks-node-role');
  const [ngSubnets, setNgSubnets] = useState('subnet-01234567,subnet-89abcdef');
  const [minSize, setMinSize] = useState('1');
  const [maxSize, setMaxSize] = useState('4');
  const [desiredSize, setDesiredSize] = useState('2');
  const [instanceType, setInstanceType] = useState('t3.medium');
  const [creatingNodegroup, setCreatingNodegroup] = useState(false);

  // Create Fargate Profile Modal
  const [createFargateOpen, setCreateFargateOpen] = useState(false);
  const [fargateProfileName, setFargateProfileName] = useState('');
  const [fargatePodRole, setFargatePodRole] = useState('arn:aws:iam::000000000000:role/eks-fargate-pod-role');
  const [fargateNamespace, setFargateNamespace] = useState('default');
  const [fargateSubnets, setFargateSubnets] = useState('subnet-01234567');
  const [creatingFargate, setCreatingFargate] = useState(false);

  // Kubeconfig Modal / Inspector State
  const [kubeconfigData, setKubeconfigData] = useState<any | null>(null);

  const activeCluster = clusters.find(cluster => cluster.name === (route.cluster || selectedClusters[0]?.name)) || null;

  useEffect(() => {
    setCreateNodegroupOpen(false); setDeleteClusterOpen(false); setCreateFargateOpen(false);
    setNgSubnets(activeCluster?.resourcesVpcConfig?.subnetIds?.join(', ') || '');
    setFargateSubnets(activeCluster?.resourcesVpcConfig?.subnetIds?.join(', ') || '');
  }, [activeCluster?.name, resourcePath]);

  const loadData = async () => {
    setLoading(true); setLoadError('');
    try {
      const response = await fetch('/api/eks/');
      const payload = await response.json();
      if (!response.ok || payload.error) throw new Error(payload.error || 'Unable to load EKS inventory');
      const list: ClusterItem[] = normalizeEksClusters(payload);
      setClusters(list);
      setSelectedClusters(previous => previous.length ? list.filter(cluster => cluster.name === previous[0].name) : list.slice(0, 1));
    } catch (err: any) {
      setLoadError(err.message || 'Unable to load EKS inventory');
      setClusters([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  useEffect(() => {
    let alive = true;
    setKubeconfigData(null); setKubeconfigError('');
    if (activeCluster?.name && selectedTabId === 'kubeconfig') {
      fetchEksKubeconfig(activeCluster.name).then(data => { if (alive) setKubeconfigData(data); })
        .catch(error => { if (alive) setKubeconfigError(error.message || 'Unable to load kubeconfig'); });
    }
    return () => { alive = false; };
  }, [activeCluster?.name, selectedTabId]);

  const handleCreateCluster = async () => {
    if (!clusterName.trim()) return;
    setCreatingCluster(true);
    setActionMessage(null);
    try {
      const subnets = subnetIds.split(',').map((s) => s.trim()).filter(Boolean);
      const sgs = securityGroupIds.split(',').map((s) => s.trim()).filter(Boolean);
      await createEksCluster(
        clusterName.trim(),
        clusterRoleArn.trim(),
        clusterVersion.value,
        subnets,
        sgs,
        { Environment: 'LocalDev', ManagedBy: 'Floci' }
      );
      setActionMessage({ type: 'success', text: `EKS Cluster "${clusterName.trim()}" created successfully.` });
      setCreateClusterOpen(false);
      setClusterName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create cluster' });
    } finally {
      setCreatingCluster(false);
    }
  };

  const handleDeleteCluster = async () => {
    if (!activeCluster) return;
    setDeletingCluster(true);
    setActionMessage(null);
    try {
      await deleteEksCluster(activeCluster.name);
      setActionMessage({ type: 'info', text: `EKS Cluster "${activeCluster.name}" scheduled for deletion.` });
      setDeleteClusterOpen(false);
      setSelectedClusters([]);
      if (route.cluster) navigate('eks/clusters');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete cluster' });
    } finally {
      setDeletingCluster(false);
    }
  };

  const handleCreateNodegroup = async () => {
    if (!activeCluster || !nodegroupName.trim()) return;
    setCreatingNodegroup(true);
    setActionMessage(null);
    try {
      const subnets = ngSubnets.split(',').map((s) => s.trim()).filter(Boolean);
      await createEksNodegroup(
        activeCluster.name,
        nodegroupName.trim(),
        nodeRoleArn.trim(),
        subnets,
        {
          minSize: Number(minSize),
          maxSize: Number(maxSize),
          desiredSize: Number(desiredSize),
        },
        [instanceType],
        'AL2_x86_64',
        'ON_DEMAND',
        20,
        { role: 'worker-node' }
      );
      setActionMessage({ type: 'success', text: `Node Group "${nodegroupName.trim()}" created in cluster ${activeCluster.name}.` });
      setCreateNodegroupOpen(false);
      setNodegroupName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create node group' });
    } finally {
      setCreatingNodegroup(false);
    }
  };

  const handleDeleteNodegroup = async (ngName: string) => {
    if (!activeCluster || !ngName) return;
    setActionMessage(null);
    try {
      await deleteEksNodegroup(activeCluster.name, ngName);
      if (route.nodegroup) navigate(`${eksClusterPath(activeCluster.name)}?tab=nodegroups`);
      setActionMessage({ type: 'info', text: `Node Group "${ngName}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete node group' });
    }
  };

  const handleCreateFargateProfile = async () => {
    if (!activeCluster || !fargateProfileName.trim()) return;
    setCreatingFargate(true);
    setActionMessage(null);
    try {
      const subnets = fargateSubnets.split(',').map((s) => s.trim()).filter(Boolean);
      await createEksFargateProfile(
        activeCluster.name,
        fargateProfileName.trim(),
        fargatePodRole.trim(),
        subnets,
        [{ namespace: fargateNamespace.trim() || 'default' }]
      );
      setActionMessage({ type: 'success', text: `Fargate Profile "${fargateProfileName.trim()}" created.` });
      setCreateFargateOpen(false);
      setFargateProfileName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create Fargate profile' });
    } finally {
      setCreatingFargate(false);
    }
  };

  const handleDeleteFargateProfile = async (profileName: string) => {
    if (!activeCluster || !profileName) return;
    setActionMessage(null);
    try {
      await deleteEksFargateProfile(activeCluster.name, profileName);
      setActionMessage({ type: 'info', text: `Fargate Profile "${profileName}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete Fargate profile' });
    }
  };

  const filteredClusters = clusters.filter(
    (c) =>
      c.name.toLowerCase().includes(filterText.toLowerCase()) ||
      (c.version && c.version.toLowerCase().includes(filterText.toLowerCase())) ||
      (c.status && c.status.toLowerCase().includes(filterText.toLowerCase()))
  );

  return (
    <SpaceBetween size="l">
      {actionMessage && (
        <Alert
          type={actionMessage.type}
          dismissible
          onDismiss={() => setActionMessage(null)}
        >
          {actionMessage.text}
        </Alert>
      )}

      {!route.cluster && !route.invalid && <Table
        header={
          <Header
            variant="h1"
            description="Manage Kubernetes control planes, managed node groups, Fargate profiles, and local kubeconfig integration."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading} />
                <Button
                  variant="normal"
                  disabled={!activeCluster}
                  onClick={() => setDeleteClusterOpen(true)}
                >
                  Delete cluster
                </Button>
                <Button
                  variant="primary"
                  iconName="add-plus"
                  onClick={() => setCreateClusterOpen(true)}
                >
                  Create cluster
                </Button>
              </SpaceBetween>
            }
          >
            EKS Clusters
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Cluster name',
            cell: (c) => <Button variant="inline-link" onClick={() => { setSelectedClusters([c]); navigate(eksClusterPath(c.name)); }}>{c.name}</Button>,
            sortingField: 'name',
          },
          {
            id: 'status',
            header: 'Status',
            cell: (c) => (
              <ResourceValue name="status" value={c.status} />
            ),
          },
          {
            id: 'version',
            header: 'Kubernetes version',
            cell: (c) => <span>{c.version || 'Not reported'}</span>,
          },
          {
            id: 'endpoint',
            header: 'API Server Endpoint',
            cell: (c) => <code>{c.endpoint}</code>,
          },
          {
            id: 'role',
            header: 'Cluster Role ARN',
            cell: (c) => <code style={{ fontSize: '0.85em' }}>{c.roleArn}</code>,
          },
        ]}
        items={filteredClusters}
        loading={loading}
        loadingText="Loading EKS clusters..."
        selectionType="single"
        selectedItems={selectedClusters}
        onSelectionChange={({ detail }) => setSelectedClusters(detail.selectedItems as ClusterItem[])}
        filter={
          <TextFilter
            filteringText={filterText}
            onChange={({ detail }) => setFilterText(detail.filteringText)}
            filteringPlaceholder="Find clusters by name or version"
          />
        }
        empty={
          <Box textAlign="center" color="inherit">
            <b>No EKS clusters</b>
            <Box variant="p" color="inherit">
              Create an EKS cluster to get started with Kubernetes workloads on Floci.
            </Box>
            <Button onClick={() => setCreateClusterOpen(true)}>Create cluster</Button>
          </Box>
        }
      />}

      {(route.cluster || route.invalid) && <Header variant="h1" actions={<Button iconName="refresh" loading={loading} onClick={loadData}>Refresh</Button>}>Amazon EKS</Header>}
      {loadError && <Alert type="error" header="Unable to load EKS resources">{loadError}</Alert>}
      {(route.cluster || route.invalid) && loading && <StatusIndicator type="loading">Loading EKS resource</StatusIndicator>}
      {(route.cluster || route.invalid) && !loading && !loadError && (route.invalid || !activeCluster || (route.nodegroup && !activeCluster.nodegroups?.some(ng => ng.name === route.nodegroup))) && <Alert type="warning" header="EKS resource not found" action={<Button onClick={() => navigate('eks/clusters')}>View clusters</Button>}>This resource is not reported in the current account and Region. Check the URL or refresh the inventory.</Alert>}
      {route.nodegroup && activeCluster && !loading && !loadError && activeCluster.nodegroups?.find(ng => ng.name === route.nodegroup) && <EKSNodegroupDetails cluster={activeCluster} nodegroup={activeCluster.nodegroups.find(ng => ng.name === route.nodegroup)} navigate={navigate} onDelete={() => { if (confirm(`Delete node group "${route.nodegroup}"?`)) handleDeleteNodegroup(route.nodegroup!); }} />}
      {activeCluster && !route.nodegroup && !route.invalid && !loadError && (!route.cluster || !loading) && (
        <Container
          header={
            <Header
              variant="h2"
              description={`Cluster ARN: ${activeCluster.arn || 'Not reported'}`}
              actions={<SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setCreateNodegroupOpen(true)}>Add node group</Button><Button onClick={() => setDeleteClusterOpen(true)}>Delete cluster</Button></SpaceBetween>}
            >
              Cluster: {activeCluster.name}
            </Header>
          }
        >
          <Tabs
            activeTabId={selectedTabId}
            onChange={({ detail }) => {
              setSelectedTabId(detail.activeTabId);
              if (route.cluster) navigate(`${eksClusterPath(route.cluster)}?tab=${detail.activeTabId}`);
              else onTabChange?.(detail.activeTabId);
            }}
            tabs={[
              {
                id: 'overview',
                label: 'Overview & Config',
                content: (
                  <SpaceBetween size="m">
                    <ColumnLayout columns={3} variant="text-grid">
                      <KeyValuePairs
                        items={[
                          { label: 'Cluster Name', value: activeCluster.name },
                          { label: 'Status', value: <StatusIndicator type={activeCluster.status === 'ACTIVE' ? 'success' : activeCluster.status === 'FAILED' ? 'error' : 'in-progress'}>{activeCluster.status}</StatusIndicator> },
                          { label: 'Kubernetes Version', value: activeCluster.version || 'Not reported' },
                        ]}
                      />
                      <KeyValuePairs
                        items={[
                          { label: 'Role ARN', value: <code>{activeCluster.roleArn}</code> },
                          { label: 'Created At', value: activeCluster.createdAt },
                          { label: 'Platform Version', value: activeCluster.platformVersion || 'Unavailable locally' },
                        ]}
                      />
                      <KeyValuePairs
                        items={[
                          { label: 'API Endpoint', value: <code>{activeCluster.endpoint}</code> },
                          { label: 'Subnets', value: activeCluster.resourcesVpcConfig?.subnetIds?.join(', ') || 'Not reported' },
                          { label: 'Security Groups', value: activeCluster.resourcesVpcConfig?.securityGroupIds?.join(', ') || 'Not reported' },
                        ]}
                      />
                    </ColumnLayout>
                    {activeCluster.details?.error && <Alert type="warning">{activeCluster.details.error}</Alert>}
                    <EKSHealth health={activeCluster.health} />
                    <EKSNetworking config={activeCluster.resourcesVpcConfig} navigate={navigate} />
                  </SpaceBetween>
                ),
              },
              {
                id: 'nodegroups',
                label: `Node Groups (${activeCluster.nodegroups?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={
                        <Header
                          variant="h3"
                          description="EC2 managed compute instances running container pods for this EKS cluster."
                          actions={
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => setCreateNodegroupOpen(true)}
                            >
                              Add node group
                            </Button>
                          }
                        >
                          Managed Node Groups
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Node Group Name',
                          cell: (ng: any) => <Button variant="inline-link" onClick={() => navigate(eksNodegroupPath(activeCluster.name, ng.name))}>{ng.name}</Button>,
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: (ng: any) => (
                            <ResourceValue name="status" value={ng.status} />
                          ),
                        },
                        {
                          id: 'instances',
                          header: 'Instance Type',
                          cell: (ng: any) => ng.instanceTypes?.join(', ') || 'Not reported',
                        },
                        {
                          id: 'scaling',
                          header: 'Scaling (Min / Desired / Max)',
                          cell: (ng: any) =>
                            ng.scalingConfig
                              ? `${ng.scalingConfig.minSize} / ${ng.scalingConfig.desiredSize} / ${ng.scalingConfig.maxSize}`
                              : 'Not reported',
                        },
                        {
                          id: 'action',
                          header: 'Actions',
                          cell: (ng: any) => (
                            <Button
                              variant="link"
                              onClick={() => handleDeleteNodegroup(ng.nodegroupName || ng.name || ng)}
                            >
                              Delete
                            </Button>
                          ),
                        },
                      ]}
                      items={activeCluster.nodegroups || []}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <b>No node groups found</b>
                          <Box variant="p" color="inherit">
                            Add a managed node group to provide EC2 worker capacity.
                          </Box>
                          <Button onClick={() => setCreateNodegroupOpen(true)}>Add node group</Button>
                        </Box>
                      }
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'fargate',
                label: `Fargate Profiles (${activeCluster.fargate_profiles?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={
                        <Header
                          variant="h3"
                          description="Serverless compute engine allowing Kubernetes pods to run without managing EC2 instances."
                          actions={
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => setCreateFargateOpen(true)}
                            >
                              Create Fargate profile
                            </Button>
                          }
                        >
                          Fargate Profiles
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Profile Name',
                          cell: (fp: any) => <strong>{fp.fargateProfileName || fp.name || fp}</strong>,
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: (fp: any) => (
                            <ResourceValue name="status" value={fp.status} />
                          ),
                        },
                        {
                          id: 'role',
                          header: 'Pod Execution Role',
                          cell: (fp: any) => <code style={{ fontSize: '0.85em' }}>{fp.podExecutionRoleArn || 'Not reported'}</code>,
                        },
                        {
                          id: 'selectors',
                          header: 'Selectors (Namespace)',
                          cell: (fp: any) =>
                            fp.selectors?.map((s: any) => s.namespace).join(', ') || 'default',
                        },
                        {
                          id: 'action',
                          header: 'Actions',
                          cell: (fp: any) => (
                            <Button
                              variant="link"
                              onClick={() => handleDeleteFargateProfile(fp.fargateProfileName || fp.name || fp)}
                            >
                              Delete
                            </Button>
                          ),
                        },
                      ]}
                      items={activeCluster.fargate_profiles || []}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <b>No Fargate profiles found</b>
                          <Box variant="p" color="inherit">
                            Run serverless Kubernetes pods by attaching a Fargate profile.
                          </Box>
                          <Button onClick={() => setCreateFargateOpen(true)}>Create Fargate profile</Button>
                        </Box>
                      }
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'kubeconfig',
                label: 'Kubeconfig & CLI Setup',
                content: (
                  <SpaceBetween size="m">
                    <Alert header="Configure Local kubectl Access">
                      Run the AWS CLI command below or copy the kubeconfig YAML into <code>~/.kube/config</code> to interact with this cluster via <code>kubectl</code>.
                    </Alert>

                    {kubeconfigError && <Alert type="error">{kubeconfigError}</Alert>}
                    {kubeconfigData && (
                      <SpaceBetween size="m">
                        <FormField label="AWS CLI Update Command">
                          <CodeSnippet code={kubeconfigData.aws_cli_command} language="cli" />
                        </FormField>

                        <FormField label="Kubeconfig Configuration (~/.kube/config)">
                          <CodeSnippet code={kubeconfigData.kubeconfig_yaml} language="cli" />
                        </FormField>
                      </SpaceBetween>
                    )}
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </Container>
      )}

      {/* Create Cluster Modal */}
      <Modal
        visible={createClusterOpen}
        onDismiss={() => setCreateClusterOpen(false)}
        header="Create EKS Kubernetes Cluster"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateClusterOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingCluster} onClick={handleCreateCluster}>
                Create Cluster
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Cluster Name" description="Unique alphanumeric identifier for the Kubernetes cluster.">
            <Input
              value={clusterName}
              onChange={({ detail }) => setClusterName(detail.value)}
              placeholder="production-k8s-cluster"
            />
          </FormField>

          <FormField label="Kubernetes Version">
            <Select
              selectedOption={clusterVersion}
              onChange={({ detail }) => setClusterVersion(detail.selectedOption as any)}
              options={K8S_VERSIONS}
            />
          </FormField>

          <FormField label="Cluster Service IAM Role ARN">
            <Input
              value={clusterRoleArn}
              onChange={({ detail }) => setClusterRoleArn(detail.value)}
              placeholder="arn:aws:iam::000000000000:role/eks-cluster-role"
            />
          </FormField>

          <FormField label="VPC Subnet IDs (Comma separated)">
            <Input
              value={subnetIds}
              onChange={({ detail }) => setSubnetIds(detail.value)}
              placeholder="subnet-01234567, subnet-89abcdef"
            />
          </FormField>

          <FormField label="Security Group IDs (Comma separated)">
            <Input
              value={securityGroupIds}
              onChange={({ detail }) => setSecurityGroupIds(detail.value)}
              placeholder="sg-01234567"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Delete Cluster Modal */}
      <Modal
        visible={deleteClusterOpen}
        onDismiss={() => setDeleteClusterOpen(false)}
        header="Delete EKS Cluster"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteClusterOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={deletingCluster} onClick={handleDeleteCluster}>
                Confirm Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            Are you sure you want to delete cluster <strong>{activeCluster?.name}</strong>?
          </Alert>
          <Box variant="p">
            This will terminate the Kubernetes control plane and deregister all associated node groups.
          </Box>
        </SpaceBetween>
      </Modal>

      {/* Add Node Group Modal */}
      <Modal
        visible={createNodegroupOpen}
        onDismiss={() => setCreateNodegroupOpen(false)}
        header={`Add Managed Node Group to ${activeCluster?.name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateNodegroupOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingNodegroup} onClick={handleCreateNodegroup}>
                Create Node Group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Node Group Name">
            <Input
              value={nodegroupName}
              onChange={({ detail }) => setNodegroupName(detail.value)}
              placeholder="worker-pool-01"
            />
          </FormField>

          <FormField label="Node Instance IAM Role ARN">
            <Input
              value={nodeRoleArn}
              onChange={({ detail }) => setNodeRoleArn(detail.value)}
              placeholder="arn:aws:iam::000000000000:role/eks-node-role"
            />
          </FormField>

          <FormField label="Subnet IDs (Comma separated)">
            <Input
              value={ngSubnets}
              onChange={({ detail }) => setNgSubnets(detail.value)}
              placeholder="subnet-01234567, subnet-89abcdef"
            />
          </FormField>

          <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 4 }]}>
            <FormField label="Min Size">
              <Input type="number" value={minSize} onChange={({ detail }) => setMinSize(detail.value)} />
            </FormField>
            <FormField label="Desired Size">
              <Input type="number" value={desiredSize} onChange={({ detail }) => setDesiredSize(detail.value)} />
            </FormField>
            <FormField label="Max Size">
              <Input type="number" value={maxSize} onChange={({ detail }) => setMaxSize(detail.value)} />
            </FormField>
          </Grid>

          <FormField label="Instance Type">
            <Input
              value={instanceType}
              onChange={({ detail }) => setInstanceType(detail.value)}
              placeholder="t3.medium"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Add Fargate Profile Modal */}
      <Modal
        visible={createFargateOpen}
        onDismiss={() => setCreateFargateOpen(false)}
        header={`Create Fargate Profile on ${activeCluster?.name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateFargateOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingFargate} onClick={handleCreateFargateProfile}>
                Create Profile
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Profile Name">
            <Input
              value={fargateProfileName}
              onChange={({ detail }) => setFargateProfileName(detail.value)}
              placeholder="serverless-apps-profile"
            />
          </FormField>

          <FormField label="Pod Execution Role ARN">
            <Input
              value={fargatePodRole}
              onChange={({ detail }) => setFargatePodRole(detail.value)}
              placeholder="arn:aws:iam::000000000000:role/eks-fargate-pod-role"
            />
          </FormField>

          <FormField label="Target Kubernetes Namespace">
            <Input
              value={fargateNamespace}
              onChange={({ detail }) => setFargateNamespace(detail.value)}
              placeholder="default"
            />
          </FormField>

          <FormField label="Subnet IDs (Comma separated)">
            <Input
              value={fargateSubnets}
              onChange={({ detail }) => setFargateSubnets(detail.value)}
              placeholder="subnet-01234567, subnet-89abcdef"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
