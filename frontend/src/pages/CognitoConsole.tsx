import { normalizeCognitoPool } from './cognitoResources';
import { CognitoPoolOverview } from '../components/CognitoPoolOverview';
import React, { useState, useEffect, useRef } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Modal from '@cloudscape-design/components/modal';
import Checkbox from '@cloudscape-design/components/checkbox';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import {
  fetchConsoleInventory,
  createCognitoUserPool,
  createCognitoUser,
  createCognitoAppClient,
  initiateCognitoAuth,
  deleteCognitoUser,
  createCognitoUserGroup,
  adminSetCognitoUserPassword,
  adminConfirmCognitoSignUp,
  adminToggleCognitoUserState,
  createCognitoIdentityPool,
} from '../api/client';

interface CognitoConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const CognitoConsole: React.FC<CognitoConsoleProps> = ({ activeTab, onTabChange, resourcePath = 'cognito', onNavigate }) => {
  const requestGeneration = useRef(0);
  const authGeneration = useRef(0);
  const [inventoryError, setInventoryError] = useState('');
  const query = new URLSearchParams(resourcePath.split('?')[1] || '');
  const requestedPool = query.get('pool');
  const panel = query.get('panel') || 'overview';
  const requestedClient = query.get('client');
  const navigatePool = (id: string, nextPanel = 'overview', client = '', tab = 'users') => {
    const params = new URLSearchParams({ pool: id, panel: nextPanel });
    if (client) params.set('client', client);
    onNavigate?.(`cognito/${tab}?${params}`);
    setSelectedTabId(tab);
    if (!onNavigate) onTabChange?.(tab);
  };
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
  const [identityError, setIdentityError] = useState('');
  const [identityPools, setIdentityPools] = useState<any[]>([]);
  const [createIdentityPoolOpen, setCreateIdentityPoolOpen] = useState(false);
  const [identityPoolName, setIdentityPoolName] = useState('');
  const [creatingIdentityPool, setCreatingIdentityPool] = useState(false);

  // Create App Client Modal
  const [createClientOpen, setCreateClientOpen] = useState(false);
  const [enablePasswordAuth, setEnablePasswordAuth] = useState(true);
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
    const generation = ++requestGeneration.current;
    setLoading(true); setInventoryError('');
    try {
      const res = await fetchConsoleInventory('cognito');
      if (generation !== requestGeneration.current) return;
      setData(res);
      setSelectedPools(previous => {
        const id = previous[0]?.Id || previous[0]?.id;
        return (res.user_pools || []).filter((pool: any) => (pool.Id || pool.id) === id).map(normalizeCognitoPool);
      });
      setIdentityPools(Array.isArray(res.identity_pools) ? res.identity_pools : []);
      setIdentityError(Array.isArray(res.identity_pools) ? '' : 'Identity pool management is unavailable through this dashboard endpoint.');
    } catch (err) {
      if (generation === requestGeneration.current) { setInventoryError(err instanceof Error ? err.message : 'Unable to load user pools'); setData({ user_pools: [] }); setSelectedPools([]); }
    } finally { if (generation === requestGeneration.current) setLoading(false); }
  };
  useEffect(() => { loadData(); return () => { requestGeneration.current += 1; authGeneration.current += 1; }; }, []);
  const poolsList = (data.user_pools || []).map(normalizeCognitoPool);
  const activePool = requestedPool ? poolsList.find((pool: any) => pool.Id === requestedPool) : onNavigate ? null : selectedPools[0];
  const activePoolId = activePool?.Id || '';
  const activeClient = activePool?.clients.find((client: any) => client.ClientId === requestedClient);
  useEffect(() => {
    setUserGroups(Array.isArray(activePool?.groups) ? activePool.groups : []);
  }, [activePoolId, data]);
  useEffect(() => {
    authGeneration.current += 1;
    setAuthClientId(activeClient?.ClientId || '');
    setAuthResult(null); setDecodedHeader(null); setDecodedPayload(null); setJwtInput(''); setAuthenticating(false);
    setCreateUserOpen(false); setResetPwdOpen(false); setCreateClientOpen(false); setCreateGroupOpen(false);
    setActionMessage(null);
  }, [activePoolId, requestedClient]);

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
      await loadData();
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
      await loadData();
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
      await createCognitoAppClient(activePoolId, clientName.trim(), enablePasswordAuth);
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
    setAuthResult(null); setDecodedHeader(null); setDecodedPayload(null);
    setActionMessage(null);
    const generation = ++authGeneration.current;
    try {
      const res = await initiateCognitoAuth(authClientId, authUsername, authPassword);
      if (generation !== authGeneration.current) return;
      setAuthResult(res);
      const idTok = res.AuthenticationResult?.IdToken || res.AuthenticationResult?.AccessToken || '';
      if (idTok) {
        decodeJwt(idTok);
      }
      setActionMessage({ type: 'success', text: res.AuthenticationResult ? 'Authentication succeeded. Tokens returned by the endpoint.' : `Authentication challenge: ${res.ChallengeName || 'Additional steps required'}.` });
    } catch (err: any) {
      if (generation === authGeneration.current) setActionMessage({ type: 'error', text: err.message || 'Authentication failed' });
    } finally {
      if (generation === authGeneration.current) setAuthenticating(false);
    }
  };

  const decodeJwt = (tok: string) => {
    setDecodedHeader(null); setDecodedPayload(null);
    try {
      const parts = tok.split('.');
      if (parts.length !== 3) return;
      const decode = (part: string) => {
        const normalized = part.replace(/-/g, '+').replace(/_/g, '/');
        const bytes = Uint8Array.from(atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=')), char => char.charCodeAt(0));
        return JSON.parse(new TextDecoder().decode(bytes));
      };
      setDecodedHeader(decode(parts[0])); setDecodedPayload(decode(parts[1]));
    } catch { /* Invalid input clears the previous decoded token. */ }
  };

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

        <div className="console-summary">
          <Box padding="m"><Box variant="awsui-key-label">User pools</Box><Box variant="h2">{poolsList.length}</Box></Box>
          <Box padding="m"><Box variant="awsui-key-label">App clients reported</Box><Box variant="h2">{poolsList.reduce((count: number, pool: any) => count + pool.clients.length, 0)}</Box></Box>
          <Box padding="m"><Box variant="awsui-key-label">Inventory</Box><StatusIndicator type={loading ? 'loading' : inventoryError ? 'error' : 'info'}>{loading ? 'Loading pools' : inventoryError ? 'Unavailable' : 'Retrieved from local endpoint'}</StatusIndicator></Box>
        </div>
        {inventoryError && <Alert type="error">{inventoryError}</Alert>}
      </Container>

      {requestedPool && !loading && !inventoryError && !activePool && <Alert type="warning">Requested user pool was not found. Select an available pool.</Alert>}
      {activePool && selectedTabId !== 'users' && <Container header={<Header variant="h2">Selected pool: {activePool.Name}</Header>}><Button onClick={() => navigatePool(activePoolId, panel, requestedClient || '')}>View pool details</Button></Container>}
      {/* Main Tabs */}
      <Tabs
        activeTabId={selectedTabId}
        onChange={({ detail }) => {
          setSelectedTabId(detail.activeTabId);
          if (activePoolId) navigatePool(activePoolId, panel, requestedClient || '', detail.activeTabId); else if (onNavigate) onNavigate(`cognito/${detail.activeTabId}`); else onTabChange?.(detail.activeTabId);
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
                            <Button variant="inline-link" onClick={() => { setSelectedPools([item]); navigatePool(item.Id); }}>
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
                      loading={loading}
                      selectedItems={activePool ? [activePool] : []}
                      onSelectionChange={({ detail }) => { setSelectedPools(detail.selectedItems); navigatePool(detail.selectedItems[0]?.Id || ''); }}
                      empty={<Box textAlign="center">No Cognito user pools found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activePool && (
                  <Container header={<Header variant="h2">Pool: {activePool.Name || activePool.name}</Header>}>
                    <Tabs activeTabId={panel} onChange={({ detail }) => navigatePool(activePoolId, detail.activeTabId, requestedClient || '')}
                      tabs={[
                        { id: 'overview', label: 'Overview', content: <CognitoPoolOverview pool={activePool} client={activeClient} onClients={() => navigatePool(activePoolId, 'pool-clients', requestedClient || '')} onTest={() => navigatePool(activePoolId, panel, requestedClient || '', 'auth')} /> },
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
                                    cell: (i: any) => <StatusIndicator type={i.UserStatus === 'CONFIRMED' ? 'success' : i.UserStatus === 'UNCONFIRMED' || i.UserStatus === 'FORCE_CHANGE_PASSWORD' ? 'warning' : 'info'}>{i.UserStatus || 'Not reported'}</StatusIndicator>,
                                    width: 140,
                                  },
                                  {
                                    id: 'enabled',
                                    header: 'Enabled',
                                    cell: (i: any) => <Badge color={i.Enabled === true ? 'green' : 'grey'}>{i.Enabled == null ? 'Not reported' : i.Enabled ? 'True' : 'False'}</Badge>,
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
                                selectionType="single" selectedItems={activeClient ? [activeClient] : []}
                                onSelectionChange={({ detail }) => navigatePool(activePoolId, 'pool-clients', detail.selectedItems[0]?.ClientId || '')}
                                columnDefinitions={[
                                  { id: 'name', header: 'Client Name', cell: (i: any) => <Button variant="inline-link" onClick={() => navigatePool(activePoolId, 'pool-clients', i.ClientId)}>{i.ClientName}</Button> },
                                  { id: 'id', header: 'App Client ID', cell: (i: any) => <code>{i.ClientId || i.id}</code> },
                                ]}
                                items={activePool.clients || []}
                                empty={<Box textAlign="center">No app clients configured.</Box>}
                              />
                              {requestedClient && !activeClient && <Alert type="warning">Requested app client was not found in this pool.</Alert>}
                              {activeClient && <CognitoPoolOverview detailsOnly pool={activePool} client={activeClient} onClients={() => navigatePool(activePoolId, 'pool-clients', requestedClient || '')} onTest={() => navigatePool(activePoolId, 'pool-clients', requestedClient || '', 'auth')} />}
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
            label: identityError ? 'Identity Pools' : `Identity Pools (${identityPools.length})`,
            id: 'identities',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" disabled={Boolean(identityError)} onClick={() => setCreateIdentityPoolOpen(true)}>
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
                  empty={identityError ? <Alert type="info">{identityError}</Alert> : <Box textAlign="center">No Identity Pools found.</Box>}
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

                    <Button variant="primary" disabled={!authClientId || !authUsername} loading={authenticating} onClick={handleTestAuth}>
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
                    <Box variant="small">Decodes token contents only. Signature, issuer, and expiry are not validated.</Box>
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
        <SpaceBetween size="m">
          <FormField label="App Client Name" description="Creates a public app client without a client secret.">
            <Input value={clientName} onChange={({ detail }) => setClientName(detail.value)} placeholder="WebAppClient" />
          </FormField>
          <Checkbox checked={enablePasswordAuth} onChange={({ detail }) => setEnablePasswordAuth(detail.checked)}>Enable password sign-in for local testing</Checkbox>
          <Box variant="small">Enables USER_PASSWORD_AUTH and refresh-token authentication. Leave unchecked to use the service defaults.</Box>
        </SpaceBetween>
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
