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
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import {
  fetchServiceInventory,
  createCognitoUserPool,
  createCognitoUser,
  createCognitoAppClient,
  initiateCognitoAuth,
  deleteCognitoUser,
  fetchCognitoUserGroups,
  createCognitoUserGroup,
  adminSetCognitoUserPassword,
  adminConfirmCognitoSignUp,
  adminToggleCognitoUserState,
  fetchCognitoIdentityPools,
  createCognitoIdentityPool,
} from '../api/client';

interface CognitoConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const CognitoConsole: React.FC<CognitoConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({ user_pools: [] });
  const [loading, setLoading] = useState(true);
  const [selectedPools, setSelectedPools] = useState<any[]>([]);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'users');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Create User Pool Modal
  const [createPoolOpen, setCreatePoolOpen] = useState(false);
  const [poolName, setPoolName] = useState('');
  const [creatingPool, setCreatingPool] = useState(false);

  // Create User Modal
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [userName, setUserName] = useState('');
  const [userEmail, setUserEmail] = useState('');
  const [userPassword, setUserPassword] = useState('Password123!');
  const [creatingUser, setCreatingUser] = useState(false);

  // Reset Password Modal
  const [resetPwdOpen, setResetPwdOpen] = useState(false);
  const [targetUser, setTargetUser] = useState('');
  const [newPassword, setNewPassword] = useState('NewPassword123!');
  const [savingPwd, setSavingPwd] = useState(false);

  // Groups State
  const [userGroups, setUserGroups] = useState<any[]>([]);
  const [createGroupOpen, setCreateGroupOpen] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [groupDesc, setGroupDesc] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);

  // Identity Pools State
  const [identityPools, setIdentityPools] = useState<any[]>([]);
  const [createIdentityPoolOpen, setCreateIdentityPoolOpen] = useState(false);
  const [identityPoolName, setIdentityPoolName] = useState('');
  const [creatingIdentityPool, setCreatingIdentityPool] = useState(false);

  // Create App Client Modal
  const [createClientOpen, setCreateClientOpen] = useState(false);
  const [clientName, setClientName] = useState('');
  const [creatingClient, setCreatingClient] = useState(false);

  // Auth Sandbox State
  const [authUsername, setAuthUsername] = useState('developer-alice');
  const [authPassword, setAuthPassword] = useState('Password123!');
  const [authClientId, setAuthClientId] = useState('');
  const [authResult, setAuthResult] = useState<any | null>(null);
  const [authenticating, setAuthenticating] = useState(false);

  // JWT Decoder State
  const [jwtInput, setJwtInput] = useState('');
  const [decodedHeader, setDecodedHeader] = useState<any | null>(null);
  const [decodedPayload, setDecodedPayload] = useState<any | null>(null);

  const loadData = async () => {
    setLoading(true);
    try {
      const [res, idpRes]: any[] = await Promise.all([
        fetchServiceInventory('cognito'),
        fetchCognitoIdentityPools(),
      ]);
      setData(res || { user_pools: [] });
      setIdentityPools(idpRes?.IdentityPools || idpRes?.identity_pools || []);
      if (res?.user_pools?.length > 0 && selectedPools.length === 0) {
        setSelectedPools([res.user_pools[0]]);
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

  const activePool = selectedPools[0] || null;
  const activePoolId = activePool ? activePool.Id || activePool.id : '';

  const loadPoolGroups = async (pId: string) => {
    if (!pId) return;
    try {
      const gRes: any = await fetchCognitoUserGroups(pId);
      setUserGroups(gRes?.Groups || gRes?.groups || []);
    } catch (err) {
      console.error(err);
      setUserGroups([]);
    }
  };

  useEffect(() => {
    if (activePoolId) {
      loadPoolGroups(activePoolId);
      if (activePool?.clients?.length > 0) {
        setAuthClientId(activePool.clients[0].ClientId || activePool.clients[0].id || '');
      } else if (activePool?.id) {
        setAuthClientId(`client-${activePool.id.substring(0, 8)}`);
      }
      setAuthResult(null);
    }
  }, [activePoolId]);

  const handleCreatePool = async () => {
    if (!poolName.trim()) return;
    setCreatingPool(true);
    setActionMessage(null);
    try {
      await createCognitoUserPool(poolName.trim());
      setActionMessage({ type: 'success', text: `User Pool "${poolName.trim()}" created successfully.` });
      setCreatePoolOpen(false);
      setPoolName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create user pool' });
    } finally {
      setCreatingPool(false);
    }
  };

  const handleCreateUser = async () => {
    if (!activePoolId || !userName.trim()) return;
    setCreatingUser(true);
    try {
      await createCognitoUser(activePoolId, userName.trim(), userPassword, userEmail.trim() || undefined);
      setActionMessage({ type: 'success', text: `User "${userName.trim()}" created in pool.` });
      setCreateUserOpen(false);
      setUserName('');
      setUserEmail('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create user' });
    } finally {
      setCreatingUser(false);
    }
  };

  const handleDeleteUser = async (uName: string) => {
    if (!activePoolId || !uName) return;
    try {
      await deleteCognitoUser(activePoolId, uName);
      setActionMessage({ type: 'success', text: `User "${uName}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete user' });
    }
  };

  const handleResetPassword = async () => {
    if (!activePoolId || !targetUser || !newPassword) return;
    setSavingPwd(true);
    try {
      await adminSetCognitoUserPassword(activePoolId, targetUser, newPassword, true);
      setActionMessage({ type: 'success', text: `Password for "${targetUser}" updated.` });
      setResetPwdOpen(false);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to set password' });
    } finally {
      setSavingPwd(false);
    }
  };

  const handleConfirmUser = async (uName: string) => {
    if (!activePoolId || !uName) return;
    try {
      await adminConfirmCognitoSignUp(activePoolId, uName);
      setActionMessage({ type: 'success', text: `User "${uName}" confirmed.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to confirm user' });
    }
  };

  const handleToggleUserState = async (uName: string, enable: boolean) => {
    if (!activePoolId || !uName) return;
    try {
      await adminToggleCognitoUserState(activePoolId, uName, enable);
      setActionMessage({ type: 'success', text: `User "${uName}" ${enable ? 'enabled' : 'disabled'}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to toggle user state' });
    }
  };

  const handleCreateGroup = async () => {
    if (!activePoolId || !newGroupName.trim()) return;
    setCreatingGroup(true);
    try {
      await createCognitoUserGroup(activePoolId, newGroupName.trim(), groupDesc.trim());
      setActionMessage({ type: 'success', text: `Group "${newGroupName.trim()}" created.` });
      setCreateGroupOpen(false);
      setNewGroupName('');
      setGroupDesc('');
      await loadPoolGroups(activePoolId);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create group' });
    } finally {
      setCreatingGroup(false);
    }
  };

  const handleCreateClient = async () => {
    if (!activePoolId || !clientName.trim()) return;
    setCreatingClient(true);
    try {
      await createCognitoAppClient(activePoolId, clientName.trim());
      setActionMessage({ type: 'success', text: `App Client "${clientName.trim()}" created.` });
      setCreateClientOpen(false);
      setClientName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create client' });
    } finally {
      setCreatingClient(false);
    }
  };

  const handleCreateIdentityPool = async () => {
    if (!identityPoolName.trim()) return;
    setCreatingIdentityPool(true);
    try {
      await createCognitoIdentityPool(identityPoolName.trim(), true);
      setActionMessage({ type: 'success', text: `Identity Pool "${identityPoolName.trim()}" created.` });
      setCreateIdentityPoolOpen(false);
      setIdentityPoolName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create identity pool' });
    } finally {
      setCreatingIdentityPool(false);
    }
  };

  const handleTestAuth = async () => {
    if (!authClientId || !authUsername) return;
    setAuthenticating(true);
    setActionMessage(null);
    try {
      const res = await initiateCognitoAuth(authClientId, authUsername, authPassword);
      setAuthResult(res);
      const idTok = res.AuthenticationResult?.IdToken || res.AuthenticationResult?.AccessToken || '';
      if (idTok) {
        decodeJwt(idTok);
      }
      setActionMessage({ type: 'success', text: 'Authentication successful! JWT tokens issued.' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Authentication failed' });
    } finally {
      setAuthenticating(false);
    }
  };

  const decodeJwt = (tok: string) => {
    try {
      const parts = tok.split('.');
      if (parts.length === 3) {
        const header = JSON.parse(atob(parts[0]));
        const payload = JSON.parse(atob(parts[1]));
        setDecodedHeader(header);
        setDecodedPayload(payload);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const poolsList = (data.user_pools || []).map((p: any) => ({
    ...p,
    Name: p.Name || p.name,
    Id: p.Id || p.id,
    CreationDate: p.CreationDate || p.created || new Date().toISOString().split('T')[0],
  }));

  const filteredPools = poolsList.filter((p: any) =>
    (p.Name || '').toLowerCase().includes(filterText.toLowerCase()) ||
    (p.Id || '').toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            description="Manage user directories, identity pools, authentication workflows, user groups, and JWT tokens."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreatePoolOpen(true)}>
                  Create User Pool
                </Button>
              </SpaceBetween>
            }
          >
            Amazon Cognito
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
            <Box variant="awsui-key-label">User Pools</Box>
            <Box variant="h1" color="text-status-info">
              {poolsList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Identity Pools</Box>
            <Box variant="h1" color="text-status-info">
              {identityPools.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Auth Engine</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">OIDC / JWT Ready</StatusIndicator>
            </Box>
          </Box>
        </Grid>
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
            label: `User Pools (${poolsList.length})`,
            id: 'users',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">User Pools</Header>}>
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={filterText}
                      filteringPlaceholder="Find user pool by name or ID..."
                      onChange={({ detail }) => setFilterText(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Pool Name',
                          cell: (item: any) => (
                            <Button variant="inline-link" onClick={() => setSelectedPools([item])}>
                              <strong>{item.Name}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'id',
                          header: 'User Pool ID',
                          cell: (item: any) => <code>{item.Id}</code>,
                        },
                        {
                          id: 'users',
                          header: 'Users Count',
                          cell: (item: any) => item.users?.length ?? 0,
                          width: 130,
                        },
                        {
                          id: 'created',
                          header: 'Creation Date',
                          cell: (item: any) => item.CreationDate,
                          width: 140,
                        },
                      ]}
                      items={filteredPools}
                      selectionType="single"
                      selectedItems={selectedPools}
                      onSelectionChange={({ detail }) => setSelectedPools(detail.selectedItems)}
                      empty={<Box textAlign="center">No Cognito user pools found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activePool && (
                  <Container header={<Header variant="h2">Pool: {activePool.Name || activePool.name}</Header>}>
                    <Tabs
                      tabs={[
                        {
                          label: `Users (${activePool.users?.length ?? 0})`,
                          id: 'pool-users',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={() => setCreateUserOpen(true)}>
                                  Create User
                                </Button>
                              </Box>

                              <Table
                                columnDefinitions={[
                                  { id: 'username', header: 'Username', cell: (i: any) => <strong>{i.Username || i.username}</strong> },
                                  {
                                    id: 'status',
                                    header: 'Status',
                                    cell: (i: any) => <StatusIndicator type="success">{i.UserStatus || i.status || 'CONFIRMED'}</StatusIndicator>,
                                    width: 140,
                                  },
                                  {
                                    id: 'enabled',
                                    header: 'Enabled',
                                    cell: (i: any) => <Badge color={i.Enabled !== false ? 'green' : 'grey'}>{i.Enabled !== false ? 'True' : 'False'}</Badge>,
                                    width: 100,
                                  },
                                  {
                                    id: 'actions',
                                    header: 'Actions',
                                    cell: (i: any) => (
                                      <SpaceBetween direction="horizontal" size="xs">
                                        <Button
                                          onClick={() => {
                                            setTargetUser(i.Username || i.username);
                                            setResetPwdOpen(true);
                                          }}
                                        >
                                          Set Password
                                        </Button>
                                        <Button onClick={() => handleConfirmUser(i.Username || i.username)}>Confirm</Button>
                                        <Button onClick={() => handleToggleUserState(i.Username || i.username, i.Enabled === false)}>
                                          {i.Enabled === false ? 'Enable' : 'Disable'}
                                        </Button>
                                        <Button iconName="remove" onClick={() => handleDeleteUser(i.Username || i.username)}>Delete</Button>
                                      </SpaceBetween>
                                    ),
                                    width: 320,
                                  },
                                ]}
                                items={activePool.users || []}
                                empty={<Box textAlign="center">No users found in this pool.</Box>}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: `User Groups (${userGroups.length})`,
                          id: 'pool-groups',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={() => setCreateGroupOpen(true)}>
                                  Create Group
                                </Button>
                              </Box>

                              <Table
                                columnDefinitions={[
                                  { id: 'name', header: 'Group Name', cell: (g: any) => <strong>{g.GroupName || g.name}</strong> },
                                  { id: 'desc', header: 'Description', cell: (g: any) => g.Description || g.description || '—' },
                                ]}
                                items={userGroups}
                                empty={<Box textAlign="center">No user groups in this pool.</Box>}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: `App Clients (${activePool.clients?.length ?? 0})`,
                          id: 'pool-clients',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={() => setCreateClientOpen(true)}>
                                  Create App Client
                                </Button>
                              </Box>

                              <Table
                                columnDefinitions={[
                                  { id: 'name', header: 'Client Name', cell: (i: any) => <strong>{i.ClientName || i.name}</strong> },
                                  { id: 'id', header: 'App Client ID', cell: (i: any) => <code>{i.ClientId || i.id}</code> },
                                ]}
                                items={activePool.clients || []}
                                empty={<Box textAlign="center">No app clients configured.</Box>}
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
            label: `Identity Pools (${identityPools.length})`,
            id: 'identities',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateIdentityPoolOpen(true)}>
                        Create Identity Pool
                      </Button>
                    }
                  >
                    Federated Identity Pools
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'name', header: 'Identity Pool Name', cell: (p: any) => <strong>{p.IdentityPoolName || p.name}</strong> },
                    { id: 'id', header: 'Identity Pool ID', cell: (p: any) => <code>{p.IdentityPoolId || p.id}</code> },
                    {
                      id: 'unauth',
                      header: 'Unauthenticated Identities',
                      cell: (p: any) => <Badge color={p.AllowUnauthenticatedIdentities ? 'green' : 'grey'}>{p.AllowUnauthenticatedIdentities ? 'Allowed' : 'Disabled'}</Badge>,
                      width: 200,
                    },
                  ]}
                  items={identityPools}
                  empty={<Box textAlign="center">No Identity Pools found.</Box>}
                />
              </Container>
            ),
          },
          {
            label: 'Auth & JWT Decoder Sandbox',
            id: 'auth',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">Test Authentication (InitiateAuth)</Header>}>
                  <SpaceBetween size="m">
                    <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
                      <FormField label="App Client ID">
                        <Input value={authClientId} onChange={({ detail }) => setAuthClientId(detail.value)} placeholder="client-id" />
                      </FormField>
                      <FormField label="Username">
                        <Input value={authUsername} onChange={({ detail }) => setAuthUsername(detail.value)} placeholder="developer-alice" />
                      </FormField>
                      <FormField label="Password">
                        <Input type="password" value={authPassword} onChange={({ detail }) => setAuthPassword(detail.value)} />
                      </FormField>
                    </Grid>

                    <Button variant="primary" loading={authenticating} onClick={handleTestAuth}>
                      Authenticate User
                    </Button>

                    {authResult && (
                      <Container header={<Header variant="h3">Authentication Response</Header>}>
                        <Textarea rows={6} value={JSON.stringify(authResult, null, 2)} readOnly />
                      </Container>
                    )}
                  </SpaceBetween>
                </Container>

                <Container header={<Header variant="h2">JWT Token Inspector</Header>}>
                  <SpaceBetween size="m">
                    <FormField label="Paste Raw JWT Token">
                      <Textarea
                        rows={3}
                        value={jwtInput}
                        onChange={({ detail }) => {
                          setJwtInput(detail.value);
                          decodeJwt(detail.value);
                        }}
                        placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                      />
                    </FormField>

                    {decodedPayload && (
                      <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                        <Container header={<Header variant="h3">Decoded Header (JOSE)</Header>}>
                          <Textarea rows={6} value={JSON.stringify(decodedHeader, null, 2)} readOnly />
                        </Container>
                        <Container header={<Header variant="h3">Decoded Claims Payload</Header>}>
                          <Textarea rows={6} value={JSON.stringify(decodedPayload, null, 2)} readOnly />
                        </Container>
                      </Grid>
                    )}
                  </SpaceBetween>
                </Container>
              </SpaceBetween>
            ),
          },
        ]}
      />

      {/* Create User Pool Modal */}
      <Modal
        visible={createPoolOpen}
        onDismiss={() => setCreatePoolOpen(false)}
        header="Create User Pool"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreatePoolOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingPool} onClick={handleCreatePool}>
                Create Pool
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="User Pool Name">
          <Input value={poolName} onChange={({ detail }) => setPoolName(detail.value)} placeholder="MyAppUserPool" />
        </FormField>
      </Modal>

      {/* Create User Modal */}
      <Modal
        visible={createUserOpen}
        onDismiss={() => setCreateUserOpen(false)}
        header={`Create User in ${activePool?.Name || activePool?.name}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateUserOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingUser} onClick={handleCreateUser}>
                Create User
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Username">
            <Input value={userName} onChange={({ detail }) => setUserName(detail.value)} placeholder="developer-alice" />
          </FormField>
          <FormField label="Email Address">
            <Input value={userEmail} onChange={({ detail }) => setUserEmail(detail.value)} placeholder="alice@example.com" />
          </FormField>
          <FormField label="Initial Password">
            <Input type="password" value={userPassword} onChange={({ detail }) => setUserPassword(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Reset Password Modal */}
      <Modal
        visible={resetPwdOpen}
        onDismiss={() => setResetPwdOpen(false)}
        header={`Reset Password for ${targetUser}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setResetPwdOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingPwd} onClick={handleResetPassword}>
                Save Password
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="New Permanent Password">
          <Input type="password" value={newPassword} onChange={({ detail }) => setNewPassword(detail.value)} />
        </FormField>
      </Modal>

      {/* Create Group Modal */}
      <Modal
        visible={createGroupOpen}
        onDismiss={() => setCreateGroupOpen(false)}
        header="Create Cognito User Group"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateGroupOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingGroup} onClick={handleCreateGroup}>
                Create Group
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Group Name">
            <Input value={newGroupName} onChange={({ detail }) => setNewGroupName(detail.value)} placeholder="Admins" />
          </FormField>
          <FormField label="Description">
            <Input value={groupDesc} onChange={({ detail }) => setGroupDesc(detail.value)} placeholder="Administrator users" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create App Client Modal */}
      <Modal
        visible={createClientOpen}
        onDismiss={() => setCreateClientOpen(false)}
        header="Create App Client"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateClientOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingClient} onClick={handleCreateClient}>
                Create Client
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="App Client Name">
          <Input value={clientName} onChange={({ detail }) => setClientName(detail.value)} placeholder="WebAppClient" />
        </FormField>
      </Modal>

      {/* Create Identity Pool Modal */}
      <Modal
        visible={createIdentityPoolOpen}
        onDismiss={() => setCreateIdentityPoolOpen(false)}
        header="Create Identity Pool"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateIdentityPoolOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingIdentityPool} onClick={handleCreateIdentityPool}>
                Create Identity Pool
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Identity Pool Name">
          <Input value={identityPoolName} onChange={({ detail }) => setIdentityPoolName(detail.value)} placeholder="MyIdentityPool" />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
