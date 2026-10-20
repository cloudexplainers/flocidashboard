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
import Checkbox from '@cloudscape-design/components/checkbox';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import {
  fetchServiceInventory,
  createRdsInstance,
  modifyRdsInstance,
  rebootRdsInstance,
  deleteRdsInstance,
  createRdsCluster,
  deleteRdsCluster,
  createRdsParameterGroup,
  deleteRdsParameterGroup,
} from '../api/client';

interface DBInstanceItem {
  vpc_security_groups?: { VpcSecurityGroupId: string }[];
  name: string;
  arn?: string;
  engine: string;
  engine_version?: string;
  status: string;
  class: string;
  allocated_storage?: number;
  storage_type?: string;
  master_username?: string;
  connect_host?: string;
  connect_port?: number;
  db_name?: string;
  iam_authentication?: boolean;
  created?: string;
  cluster_identifier?: string;
}

interface DBClusterItem {
  name: string;
  arn?: string;
  status: string;
  engine: string;
  engine_version?: string;
  database_name?: string;
  master_username?: string;
  endpoint?: string;
  port?: number;
  created?: string;
}

interface ParameterGroupItem {
  name: string;
  arn?: string;
  family: string;
  description: string;
  parameter_count?: number;
}

const ENGINE_OPTIONS = [
  { label: 'PostgreSQL', value: 'postgres', description: 'Port 5432' },
  { label: 'MySQL', value: 'mysql', description: 'Port 3306' },
  { label: 'MariaDB', value: 'mariadb', description: 'Port 3306' },
  { label: 'Aurora PostgreSQL', value: 'aurora-postgresql', description: 'Clustered PostgreSQL' },
  { label: 'Aurora MySQL', value: 'aurora-mysql', description: 'Clustered MySQL' },
];

export const RDSConsole: React.FC = () => {
  const [topTabId, setTopTabId] = useState('instances');
  const [instances, setInstances] = useState<DBInstanceItem[]>([]);
  const [clusters, setClusters] = useState<DBClusterItem[]>([]);
  const [paramGroups, setParamGroups] = useState<ParameterGroupItem[]>([]);
  const [loading, setLoading] = useState(true);

  // Selected items
  const [selectedInstances, setSelectedInstances] = useState<DBInstanceItem[]>([]);
  const [selectedClusters, setSelectedClusters] = useState<DBClusterItem[]>([]);
  const [selectedParamGroups, setSelectedParamGroups] = useState<ParameterGroupItem[]>([]);
  const [filterText, setFilterText] = useState('');

  // Modals
  const [createInstanceOpen, setCreateInstanceOpen] = useState(false);
  const [modifyInstanceOpen, setModifyInstanceOpen] = useState(false);
  const [rebootInstanceOpen, setRebootInstanceOpen] = useState(false);
  const [deleteInstanceOpen, setDeleteInstanceOpen] = useState(false);
  const [createClusterOpen, setCreateClusterOpen] = useState(false);
  const [createParamGroupOpen, setCreateParamGroupOpen] = useState(false);

  // Create Instance form state
  const [newDbId, setNewDbId] = useState('');
  const [newEngine, setNewEngine] = useState(ENGINE_OPTIONS[0]);
  const [newDbClass, setNewDbClass] = useState('db.t3.micro');
  const [newStorage, setNewStorage] = useState('20');
  const [newUsername, setNewUsername] = useState('postgres');
  const [newPassword, setNewPassword] = useState('flociPassword123!');
  const [newDbName, setNewDbName] = useState('main');
  const [newEnableIam, setNewEnableIam] = useState(false);
  const [submittingInstance, setSubmittingInstance] = useState(false);

  // Modify Instance form state
  const [modifyClass, setModifyClass] = useState('');
  const [modifyStorage, setModifyStorage] = useState('');
  const [modifyPassword, setModifyPassword] = useState('');
  const [submittingModify, setSubmittingModify] = useState(false);

  // Create Cluster form state
  const [clusterId, setClusterId] = useState('');
  const [clusterEngine, setClusterEngine] = useState(ENGINE_OPTIONS[3]);
  const [clusterUsername, setClusterUsername] = useState('postgres');
  const [clusterPassword, setClusterPassword] = useState('flociClusterPass123!');
  const [clusterDbName, setClusterDbName] = useState('clusterdb');
  const [submittingCluster, setSubmittingCluster] = useState(false);

  // Create Parameter Group form state
  const [pgName, setPgName] = useState('');
  const [pgFamily, setPgFamily] = useState('postgres15');
  const [pgDesc, setPgDesc] = useState('Custom parameter group');
  const [submittingPg, setSubmittingPg] = useState(false);

  // Action status / alerts
  const [actionAlert, setActionAlert] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [rebooting, setRebooting] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const activeInstance = selectedInstances[0] || null;

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchServiceInventory('rds');
      const rawInstances = data.instances || data.DBInstances || data.databases || [];
      const instList: DBInstanceItem[] = rawInstances.map((db: any) => ({
        name: db.name || db.DBInstanceIdentifier || db.identifier || 'db-1',
        arn: db.arn || db.DBInstanceArn,
        engine: db.engine || db.Engine || 'postgres',
        engine_version: db.engine_version || db.EngineVersion || '',
        status: db.status || db.DBInstanceStatus || 'available',
        class: db.class || db.DBInstanceClass || 'db.t3.micro',
        allocated_storage: db.allocated_storage || db.AllocatedStorage || 20,
        storage_type: db.storage_type || db.StorageType || 'gp2',
        master_username: db.master_username || db.MasterUsername || 'postgres',
        connect_host: db.connect_host || db.Endpoint?.Address || 'localhost',
        connect_port: db.connect_port || db.Endpoint?.Port || (String(db.engine).includes('my') ? 3306 : 5432),
        db_name: db.db_name || db.DBName || '',
        iam_authentication: db.iam_authentication || db.IAMDatabaseAuthenticationEnabled || false,
        created: db.created || db.InstanceCreateTime,
        cluster_identifier: db.cluster_identifier || db.DBClusterIdentifier,
      }));
      setInstances(instList);

      const clList: DBClusterItem[] = (data.clusters || data.DBClusters || []).map((c: any) => ({
        name: c.name || c.DBClusterIdentifier || 'cluster-1',
        arn: c.arn || c.DBClusterArn,
        status: c.status || c.Status || 'available',
        engine: c.engine || c.Engine || 'aurora-postgresql',
        engine_version: c.engine_version || c.EngineVersion,
        database_name: c.database_name || c.DatabaseName,
        master_username: c.master_username || c.MasterUsername || 'postgres',
        endpoint: c.endpoint || c.Endpoint || 'localhost',
        port: c.port || c.Port || 5432,
        created: c.created || c.ClusterCreateTime,
      }));
      setClusters(clList);

      const pgList: ParameterGroupItem[] = (data.parameter_groups || data.DBParameterGroups || []).map((pg: any) => ({
        name: pg.name || pg.DBParameterGroupName || 'default',
        arn: pg.arn || pg.DBParameterGroupArn,
        family: pg.family || pg.DBParameterGroupFamily || 'postgres15',
        description: pg.description || pg.Description || 'Default parameter group',
        parameter_count: pg.parameter_count || 0,
      }));
      setParamGroups(pgList);

      if (instList.length > 0 && !selectedInstances.length) {
        setSelectedInstances([instList[0]]);
      } else if (selectedInstances.length > 0) {
        const refreshed = instList.find((i) => i.name === selectedInstances[0].name);
        if (refreshed) setSelectedInstances([refreshed]);
      }
    } catch (err: any) {
      console.error(err);
      setActionAlert({ type: 'error', message: err.message || 'Failed to load RDS databases.' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateInstance = async () => {
    if (!newDbId.trim() || !newUsername.trim() || !newPassword.trim()) return;
    setSubmittingInstance(true);
    setActionAlert(null);
    try {
      await createRdsInstance(
        newDbId.trim(),
        newEngine.value,
        newUsername.trim(),
        newPassword.trim(),
        newDbClass,
        parseInt(newStorage, 10) || 20,
        newDbName.trim() || undefined,
        undefined,
        newEnableIam
      );
      setActionAlert({ type: 'success', message: `Database instance "${newDbId}" created successfully.` });
      setCreateInstanceOpen(false);
      setNewDbId('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create database instance.' });
    } finally {
      setSubmittingInstance(false);
    }
  };

  const handleModifyInstance = async () => {
    if (!activeInstance) return;
    setSubmittingModify(true);
    setActionAlert(null);
    try {
      await modifyRdsInstance(
        activeInstance.name,
        modifyClass || undefined,
        modifyStorage ? parseInt(modifyStorage, 10) : undefined,
        modifyPassword || undefined,
        true
      );
      setActionAlert({ type: 'success', message: `Database "${activeInstance.name}" modified successfully.` });
      setModifyInstanceOpen(false);
      setModifyClass('');
      setModifyStorage('');
      setModifyPassword('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to modify database instance.' });
    } finally {
      setSubmittingModify(false);
    }
  };

  const handleRebootInstance = async () => {
    if (!activeInstance) return;
    setRebooting(true);
    setActionAlert(null);
    try {
      await rebootRdsInstance(activeInstance.name);
      setActionAlert({ type: 'success', message: `Rebooting database "${activeInstance.name}"...` });
      setRebootInstanceOpen(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to reboot database.' });
    } finally {
      setRebooting(false);
    }
  };

  const handleDeleteInstance = async () => {
    if (!activeInstance) return;
    setDeleting(true);
    setActionAlert(null);
    try {
      await deleteRdsInstance(activeInstance.name, true);
      setActionAlert({ type: 'success', message: `Database "${activeInstance.name}" deleted.` });
      setDeleteInstanceOpen(false);
      setSelectedInstances([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete database.' });
    } finally {
      setDeleting(false);
    }
  };

  const handleCreateCluster = async () => {
    if (!clusterId.trim()) return;
    setSubmittingCluster(true);
    setActionAlert(null);
    try {
      await createRdsCluster(
        clusterId.trim(),
        clusterEngine.value,
        clusterUsername.trim(),
        clusterPassword.trim(),
        clusterDbName.trim() || undefined
      );
      setActionAlert({ type: 'success', message: `DB Cluster "${clusterId}" created.` });
      setCreateClusterOpen(false);
      setClusterId('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create DB cluster.' });
    } finally {
      setSubmittingCluster(false);
    }
  };

  const handleCreateParamGroup = async () => {
    if (!pgName.trim()) return;
    setSubmittingPg(true);
    setActionAlert(null);
    try {
      await createRdsParameterGroup(pgName.trim(), pgFamily.trim(), pgDesc.trim());
      setActionAlert({ type: 'success', message: `Parameter group "${pgName}" created.` });
      setCreateParamGroupOpen(false);
      setPgName('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create parameter group.' });
    } finally {
      setSubmittingPg(false);
    }
  };

  const handleDeleteCluster = async (cName: string) => {
    try {
      await deleteRdsCluster(cName, true);
      setActionAlert({ type: 'success', message: `DB Cluster "${cName}" deleted.` });
      setSelectedClusters([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete DB cluster.' });
    }
  };

  const handleDeleteParamGroup = async (groupName: string) => {
    try {
      await deleteRdsParameterGroup(groupName);
      setActionAlert({ type: 'success', message: `Parameter group "${groupName}" deleted.` });
      setSelectedParamGroups([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete parameter group.' });
    }
  };

  const filteredInstances = instances.filter((i) =>
    i.name.toLowerCase().includes(filterText.toLowerCase()) ||
    i.engine.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {actionAlert && (
        <Alert
          type={actionAlert.type}
          dismissible
          onDismiss={() => setActionAlert(null)}
          header={actionAlert.type === 'error' ? 'RDS Operation Error' : 'RDS Notification'}
        >
          {actionAlert.message}
        </Alert>
      )}

      <Tabs
        activeTabId={topTabId}
        onChange={({ detail }) => setTopTabId(detail.activeTabId)}
        tabs={[
          {
            id: 'instances',
            label: `Databases (${instances.length})`,
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h1"
                      counter={`(${instances.length})`}
                      description="Relational database instances (PostgreSQL, MySQL, MariaDB, Aurora) running in Floci."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button iconName="refresh" onClick={loadData} loading={loading}>
                            Refresh
                          </Button>
                          <Button
                            disabled={!activeInstance}
                            iconName="status-positive"
                            onClick={() => setRebootInstanceOpen(true)}
                          >
                            Reboot
                          </Button>
                          <Button
                            disabled={!activeInstance}
                            iconName="edit"
                            onClick={() => setModifyInstanceOpen(true)}
                          >
                            Modify
                          </Button>
                          <Button
                            disabled={!activeInstance}
                            iconName="remove"
                            onClick={() => setDeleteInstanceOpen(true)}
                          >
                            Delete
                          </Button>
                          <Button
                            variant="primary"
                            iconName="add-plus"
                            onClick={() => setCreateInstanceOpen(true)}
                          >
                            Create database
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      Databases
                    </Header>
                  }
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'DB identifier',
                      cell: (item) => (
                        <Button variant="inline-link" onClick={() => setSelectedInstances([item])}>
                          <strong>{item.name}</strong>
                        </Button>
                      ),
                      sortingField: 'name',
                      isRowHeader: true,
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      cell: (item) => (
                        <StatusIndicator type={item.status === 'available' ? 'success' : 'pending'}>
                          {item.status}
                        </StatusIndicator>
                      ),
                    },
                    {
                      id: 'engine',
                      header: 'Engine',
                      cell: (item) => <Badge color="blue">{item.engine}</Badge>,
                    },
                    {
                      id: 'class',
                      header: 'Size',
                      cell: (item) => item.class,
                    },
                    {
                      id: 'endpoint',
                      header: 'Endpoint & Port',
                      cell: (item) => (
                        <code>{item.connect_host}:{item.connect_port}</code>
                      ),
                    },
                    {
                      id: 'storage',
                      header: 'Storage',
                      cell: (item) => `${item.allocated_storage || 20} GiB (${item.storage_type || 'gp2'})`,
                    },
                  ]}
                  items={filteredInstances}
                  loading={loading}
                  loadingText="Loading RDS databases..."
                  selectionType="single"
                  selectedItems={selectedInstances}
                  onSelectionChange={({ detail }) => setSelectedInstances(detail.selectedItems)}
                  filter={
                    <TextFilter
                      filteringText={filterText}
                      filteringPlaceholder="Filter databases by identifier or engine..."
                      onChange={({ detail }) => setFilterText(detail.filteringText)}
                    />
                  }
                  empty={
                    <Box textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
                      <SpaceBetween size="m">
                        <b>No databases found</b>
                        <p>Launch a PostgreSQL, MySQL, or MariaDB database instance in Floci.</p>
                        <Button variant="primary" onClick={() => setCreateInstanceOpen(true)}>
                          Create database
                        </Button>
                      </SpaceBetween>
                    </Box>
                  }
                />

                {activeInstance && (
                  <Container
                    header={
                      <Header
                        variant="h2"
                        description={`Engine: ${activeInstance.engine} | Host: ${activeInstance.connect_host}:${activeInstance.connect_port}`}
                        actions={
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button iconName="status-positive" onClick={() => setRebootInstanceOpen(true)}>
                              Reboot DB
                            </Button>
                            <Button iconName="edit" onClick={() => setModifyInstanceOpen(true)}>
                              Modify DB
                            </Button>
                          </SpaceBetween>
                        }
                      >
                        Database: {activeInstance.name}
                      </Header>
                    }
                  >
                    <Tabs
                      tabs={[
                        {
                          id: 'connectivity',
                          label: 'Connectivity & Client Commands',
                          content: (
                            <SpaceBetween size="l">
                              <KeyValuePairs
                                columns={3}
                                items={[
                                  { label: 'Endpoint Host', value: activeInstance.connect_host || 'localhost' },
                                  { label: 'Port', value: String(activeInstance.connect_port || 5432) },
                                  { label: 'Database Name', value: activeInstance.db_name || 'postgres' },
                                  { label: 'Master Username', value: activeInstance.master_username || 'postgres' },
                                  { label: 'VPC Security Groups', value: activeInstance.vpc_security_groups?.map((group: any) => group.VpcSecurityGroupId).join(', ') || 'Unavailable locally' },
                                  { label: 'Publicly Accessible', value: 'Local endpoint; AWS public accessibility unavailable' },
                                ]}
                              />

                              <Header variant="h3">Copyable Connection Snippets</Header>
                              <ColumnLayout columns={2}>
                                <FormField label="CLI Connection Command">
                                  <textarea
                                    readOnly
                                    rows={2}
                                    value={
                                      activeInstance.engine.includes('my') || activeInstance.engine.includes('maria')
                                        ? `mysql -h ${activeInstance.connect_host} -P ${activeInstance.connect_port} -u ${activeInstance.master_username} -p`
                                        : `PGPASSWORD='password' psql -h ${activeInstance.connect_host} -p ${activeInstance.connect_port} -U ${activeInstance.master_username} -d ${activeInstance.db_name || 'postgres'}`
                                    }
                                    style={{ width: '100%', fontFamily: 'monospace', fontSize: '11px', background: '#1b2a3a', color: '#4af', padding: '8px', borderRadius: '4px' }}
                                  />
                                </FormField>
                                <FormField label="Python Connection Snippet">
                                  <textarea
                                    readOnly
                                    rows={2}
                                    value={
                                      activeInstance.engine.includes('my')
                                        ? `import pymysql\nconn = pymysql.connect(host='${activeInstance.connect_host}', port=${activeInstance.connect_port}, user='${activeInstance.master_username}', password='...')`
                                        : `import psycopg2\nconn = psycopg2.connect(host='${activeInstance.connect_host}', port=${activeInstance.connect_port}, user='${activeInstance.master_username}', dbname='${activeInstance.db_name || 'postgres'}')`
                                    }
                                    style={{ width: '100%', fontFamily: 'monospace', fontSize: '11px', background: '#1b2a3a', color: '#4af', padding: '8px', borderRadius: '4px' }}
                                  />
                                </FormField>
                              </ColumnLayout>
                            </SpaceBetween>
                          ),
                        },
                        {
                          id: 'config',
                          label: 'Configuration & Storage',
                          content: (
                            <SpaceBetween size="l">
                              <KeyValuePairs
                                columns={3}
                                items={[
                                  { label: 'DB Instance Identifier', value: activeInstance.name },
                                  { label: 'Engine & Version', value: `${activeInstance.engine} ${activeInstance.engine_version || ''}` },
                                  { label: 'DB Instance Class', value: activeInstance.class },
                                  { label: 'Allocated Storage', value: activeInstance.allocated_storage == null ? 'Not reported' : `${activeInstance.allocated_storage} GiB` },
                                  { label: 'Storage Type', value: activeInstance.storage_type || 'Not reported' },
                                  { label: 'IAM Database Authentication', value: activeInstance.iam_authentication == null ? 'Not reported' : activeInstance.iam_authentication ? 'Enabled' : 'Disabled' },
                                  { label: 'Cluster Identifier', value: activeInstance.cluster_identifier || 'Single-Instance' },
                                  { label: 'Created Time', value: activeInstance.created || 'N/A' },
                                ]}
                              />
                            </SpaceBetween>
                          ),
                        },
                      ]}
                    />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            id: 'clusters',
            label: `DB Clusters (${clusters.length})`,
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${clusters.length})`}
                      description="Multi-AZ Aurora and clustered database topologies."
                      actions={
                        <Button
                          variant="primary"
                          iconName="add-plus"
                          onClick={() => setCreateClusterOpen(true)}
                        >
                          Create DB cluster
                        </Button>
                      }
                    >
                      Aurora & Multi-AZ DB Clusters
                    </Header>
                  }
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'Cluster identifier',
                      cell: (c) => <strong>{c.name}</strong>,
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      cell: (c) => <StatusIndicator type="success">{c.status}</StatusIndicator>,
                    },
                    {
                      id: 'engine',
                      header: 'Engine',
                      cell: (c) => <Badge color="blue">{c.engine}</Badge>,
                    },
                    {
                      id: 'endpoint',
                      header: 'Cluster Endpoint',
                      cell: (c) => <code>{c.endpoint}:{c.port}</code>,
                    },
                    {
                      id: 'actions',
                      header: 'Action',
                      cell: (c) => (
                        <Button iconName="remove" onClick={() => handleDeleteCluster(c.name)}>
                          Delete
                        </Button>
                      ),
                    },
                  ]}
                  items={clusters}
                  selectionType="single"
                  selectedItems={selectedClusters}
                  onSelectionChange={({ detail }) => setSelectedClusters(detail.selectedItems)}
                  empty={<Box textAlign="center" padding={{ vertical: 'm' }}>No DB clusters configured.</Box>}
                />
              </SpaceBetween>
            ),
          },
          {
            id: 'parameter-groups',
            label: `Parameter Groups (${paramGroups.length})`,
            content: (
              <SpaceBetween size="l">
                <Table
                  header={
                    <Header
                      variant="h2"
                      counter={`(${paramGroups.length})`}
                      description="Manage database engine configuration parameters and server flags."
                      actions={
                        <Button
                          variant="primary"
                          iconName="add-plus"
                          onClick={() => setCreateParamGroupOpen(true)}
                        >
                          Create parameter group
                        </Button>
                      }
                    >
                      DB Parameter Groups
                    </Header>
                  }
                  columnDefinitions={[
                    {
                      id: 'name',
                      header: 'Group name',
                      cell: (pg) => <strong>{pg.name}</strong>,
                    },
                    {
                      id: 'family',
                      header: 'Family',
                      cell: (pg) => <Badge color="grey">{pg.family}</Badge>,
                    },
                    {
                      id: 'desc',
                      header: 'Description',
                      cell: (pg) => pg.description,
                    },
                    {
                      id: 'count',
                      header: 'Parameters',
                      cell: (pg) => `${pg.parameter_count || 0} parameters`,
                    },
                    {
                      id: 'actions',
                      header: 'Action',
                      cell: (pg) => (
                        <Button iconName="remove" onClick={() => handleDeleteParamGroup(pg.name)}>
                          Delete
                        </Button>
                      ),
                    },
                  ]}
                  items={paramGroups}
                  selectionType="single"
                  selectedItems={selectedParamGroups}
                  onSelectionChange={({ detail }) => setSelectedParamGroups(detail.selectedItems)}
                  empty={<Box textAlign="center" padding={{ vertical: 'm' }}>No custom parameter groups found.</Box>}
                />
              </SpaceBetween>
            ),
          },
        ]}
      />

      {/* Create Instance Modal */}
      <Modal
        visible={createInstanceOpen}
        onDismiss={() => setCreateInstanceOpen(false)}
        header="Create RDS Database Instance"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateInstanceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateInstance} loading={submittingInstance}>
                Create database
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="DB instance identifier" description="Unique alphanumeric identifier.">
            <Input
              value={newDbId}
              onChange={({ detail }) => setNewDbId(detail.value)}
              placeholder="e.g. analytics-db"
            />
          </FormField>
          <FormField label="Database engine">
            <Select
              selectedOption={newEngine}
              onChange={({ detail }) => setNewEngine(detail.selectedOption as any)}
              options={ENGINE_OPTIONS}
            />
          </FormField>
          <ColumnLayout columns={2}>
            <FormField label="DB instance class">
              <Input
                value={newDbClass}
                onChange={({ detail }) => setNewDbClass(detail.value)}
                placeholder="e.g. db.t3.micro"
              />
            </FormField>
            <FormField label="Allocated storage (GiB)">
              <Input
                value={newStorage}
                onChange={({ detail }) => setNewStorage(detail.value)}
                type="number"
              />
            </FormField>
          </ColumnLayout>
          <ColumnLayout columns={2}>
            <FormField label="Master username">
              <Input
                value={newUsername}
                onChange={({ detail }) => setNewUsername(detail.value)}
              />
            </FormField>
            <FormField label="Master password">
              <Input
                value={newPassword}
                onChange={({ detail }) => setNewPassword(detail.value)}
                type="password"
              />
            </FormField>
          </ColumnLayout>
          <FormField label="Initial database name (optional)">
            <Input
              value={newDbName}
              onChange={({ detail }) => setNewDbName(detail.value)}
              placeholder="e.g. production"
            />
          </FormField>
          <Checkbox
            checked={newEnableIam}
            onChange={({ detail }) => setNewEnableIam(detail.checked)}
          >
            Enable IAM database authentication
          </Checkbox>
        </SpaceBetween>
      </Modal>

      {/* Modify Instance Modal */}
      <Modal
        visible={modifyInstanceOpen}
        onDismiss={() => setModifyInstanceOpen(false)}
        header={`Modify Database: ${activeInstance?.name}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setModifyInstanceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleModifyInstance} loading={submittingModify}>
                Save modifications
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="New DB instance class (optional)" description="Leave blank to keep current class.">
            <Input
              value={modifyClass}
              onChange={({ detail }) => setModifyClass(detail.value)}
              placeholder={activeInstance?.class || 'e.g. db.m5.large'}
            />
          </FormField>
          <FormField label="New allocated storage in GiB (optional)">
            <Input
              value={modifyStorage}
              onChange={({ detail }) => setModifyStorage(detail.value)}
              type="number"
              placeholder={String(activeInstance?.allocated_storage || 20)}
            />
          </FormField>
          <FormField label="New master user password (optional)">
            <Input
              value={modifyPassword}
              onChange={({ detail }) => setModifyPassword(detail.value)}
              type="password"
              placeholder="Enter new password..."
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Reboot Instance Modal */}
      <Modal
        visible={rebootInstanceOpen}
        onDismiss={() => setRebootInstanceOpen(false)}
        header={`Reboot Database: ${activeInstance?.name}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRebootInstanceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleRebootInstance} loading={rebooting}>
                Reboot instance
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <p>
            Are you sure you want to reboot database instance <strong>{activeInstance?.name}</strong>? Connections may experience a momentary restart.
          </p>
        </SpaceBetween>
      </Modal>

      {/* Delete Instance Modal */}
      <Modal
        visible={deleteInstanceOpen}
        onDismiss={() => setDeleteInstanceOpen(false)}
        header={`Delete Database: ${activeInstance?.name}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteInstanceOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDeleteInstance} loading={deleting}>
                Delete database
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="error" header="Permanent Deletion">
            Are you sure you want to delete database instance <strong>{activeInstance?.name}</strong>? Storage and data will be removed.
          </Alert>
        </SpaceBetween>
      </Modal>

      {/* Create Cluster Modal */}
      <Modal
        visible={createClusterOpen}
        onDismiss={() => setCreateClusterOpen(false)}
        header="Create DB Cluster"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateClusterOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateCluster} loading={submittingCluster}>
                Create cluster
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="DB cluster identifier">
            <Input
              value={clusterId}
              onChange={({ detail }) => setClusterId(detail.value)}
              placeholder="e.g. aurora-main-cluster"
            />
          </FormField>
          <FormField label="Engine">
            <Select
              selectedOption={clusterEngine}
              onChange={({ detail }) => setClusterEngine(detail.selectedOption as any)}
              options={[ENGINE_OPTIONS[3], ENGINE_OPTIONS[4]]}
            />
          </FormField>
          <ColumnLayout columns={2}>
            <FormField label="Master username">
              <Input
                value={clusterUsername}
                onChange={({ detail }) => setClusterUsername(detail.value)}
              />
            </FormField>
            <FormField label="Master password">
              <Input
                value={clusterPassword}
                onChange={({ detail }) => setClusterPassword(detail.value)}
                type="password"
              />
            </FormField>
          </ColumnLayout>
          <FormField label="Initial database name (optional)">
            <Input
              value={clusterDbName}
              onChange={({ detail }) => setClusterDbName(detail.value)}
              placeholder="clusterdb"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Parameter Group Modal */}
      <Modal
        visible={createParamGroupOpen}
        onDismiss={() => setCreateParamGroupOpen(false)}
        header="Create DB Parameter Group"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateParamGroupOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateParamGroup} loading={submittingPg}>
                Create group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Parameter group name">
            <Input
              value={pgName}
              onChange={({ detail }) => setPgName(detail.value)}
              placeholder="e.g. pg15-optimized"
            />
          </FormField>
          <FormField label="DB parameter group family">
            <Input
              value={pgFamily}
              onChange={({ detail }) => setPgFamily(detail.value)}
              placeholder="e.g. postgres15, mysql8.0"
            />
          </FormField>
          <FormField label="Description">
            <Input
              value={pgDesc}
              onChange={({ detail }) => setPgDesc(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};

