import React, { useState, useEffect, useMemo } from 'react';
import Table from '../components/ResourceTable';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Container from '@cloudscape-design/components/container';
import Modal from '@cloudscape-design/components/modal';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import Grid from '@cloudscape-design/components/grid';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Link from '@cloudscape-design/components/link';
import {
  fetchInventory,
  createAppSyncGraphQLApi,
  runAppSyncGraphQL,
  deleteAppSyncGraphQLApi,
  createAppSyncSchema,
  createAppSyncApiKey,
  deleteAppSyncApiKey,
  createAppSyncDataSource,
  deleteAppSyncDataSource,
  createAppSyncResolver,
  deleteAppSyncResolver,
} from '../api/client';

interface AppSyncConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const AppSyncConsole: React.FC<AppSyncConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({
    graphql_apis: [],
    schemas: {},
    data_sources: [],
    resolvers: [],
    api_keys: [],
  });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Selected API
  const [selectedApis, setSelectedApis] = useState<any[]>([]);

  // Create API Modal
  const [createApiOpen, setCreateApiOpen] = useState(false);
  const [apiName, setApiName] = useState('');
  const [authType, setAuthType] = useState<{ label: string; value: 'API_KEY' | 'AWS_IAM' | 'OPENID_CONNECT' | 'AMAZON_COGNITO_USER_POOLS' }>({
    label: 'API_KEY (Simple Key-based authentication)',
    value: 'API_KEY',
  });
  const [creatingApi, setCreatingApi] = useState(false);

  // Schema Editor
  const [schemaDoc, setSchemaDoc] = useState(`schema {
  query: Query
  mutation: Mutation
}

type Query {
  getPost(id: ID!): Post
  listPosts(limit: Int): [Post]
}

type Mutation {
  createPost(title: String!, content: String): Post
  deletePost(id: ID!): Boolean
}

type Post {
  id: ID!
  title: String!
  content: String
  createdAt: String
}`);
  const [savingSchema, setSavingSchema] = useState(false);

  // Create Data Source Modal
  const [createDsOpen, setCreateDsOpen] = useState(false);
  const [dsName, setDsName] = useState('');
  const [dsType, setDsType] = useState({ label: 'AMAZON_DYNAMODB (NoSQL Table)', value: 'AMAZON_DYNAMODB' });
  const [dsDesc, setDsDesc] = useState('');
  const [creatingDs, setCreatingDs] = useState(false);

  // Create Resolver Modal
  const [createResolverOpen, setCreateResolverOpen] = useState(false);
  const [resTypeName, setResTypeName] = useState('Query');
  const [resFieldName, setResFieldName] = useState('getPost');
  const [resDataSourceName, setResDataSourceName] = useState('');
  const [creatingResolver, setCreatingResolver] = useState(false);

  // Create API Key Modal
  const [createKeyOpen, setCreateKeyOpen] = useState(false);
  const [keyDesc, setKeyDesc] = useState('Development API Key');
  const [creatingKey, setCreatingKey] = useState(false);

  // Query Sandbox State
  const [gqlQuery, setGqlQuery] = useState(`query GetPosts {\n  listPosts(limit: 5) {\n    id\n    title\n    createdAt\n  }\n}`);
  const [gqlVariables, setGqlVariables] = useState('{\n  "limit": 5\n}');
  const [gqlResponse, setGqlResponse] = useState<string | null>(null);
  const [runningGql, setRunningGql] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('appsync');
      setData(
        res || {
          graphql_apis: [],
          schemas: {},
          data_sources: [],
          resolvers: [],
          api_keys: [],
        }
      );
      if (res?.graphql_apis?.length > 0 && selectedApis.length === 0) {
        setSelectedApis([res.graphql_apis[0]]);
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

  const activeApi = selectedApis[0] || null;
  const activeApiId = activeApi ? activeApi.apiId || activeApi.id : '';
  const activeApiEndpoint = activeApi ? activeApi.uris?.GRAPHQL || `http://localhost:4566/graphql/${activeApiId}` : 'http://localhost:4566/graphql';

  const filteredApis = useMemo(() => {
    const list = data.graphql_apis || [];
    if (!filterText) return list;
    return list.filter((a: any) =>
      (a.name || a.apiId || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [data.graphql_apis, filterText]);

  // Actions
  const handleCreateApi = async () => {
    if (!apiName.trim()) return;
    setCreatingApi(true);
    try {
      await createAppSyncGraphQLApi(apiName.trim(), authType.value);
      setActionMessage({ type: 'success', text: `GraphQL API "${apiName.trim()}" created.` });
      setCreateApiOpen(false);
      setApiName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create GraphQL API' });
    } finally {
      setCreatingApi(false);
    }
  };

  const handleDeleteApi = async (apiId: string) => {
    if (!confirm(`Are you sure you want to delete GraphQL API "${apiId}"?`)) return;
    try {
      await deleteAppSyncGraphQLApi(apiId);
      setActionMessage({ type: 'success', text: `API "${apiId}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete API' });
    }
  };

  const handleSaveSchema = async () => {
    if (!activeApiId || !schemaDoc.trim()) return;
    setSavingSchema(true);
    try {
      await createAppSyncSchema(activeApiId, schemaDoc.trim());
      setActionMessage({ type: 'success', text: 'GraphQL Schema deployed successfully.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to deploy schema' });
    } finally {
      setSavingSchema(false);
    }
  };

  const handleCreateDataSource = async () => {
    if (!activeApiId || !dsName.trim()) return;
    setCreatingDs(true);
    try {
      await createAppSyncDataSource(activeApiId, dsName.trim(), dsType.value, dsDesc.trim());
      setActionMessage({ type: 'success', text: `Data Source "${dsName.trim()}" created.` });
      setCreateDsOpen(false);
      setDsName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create data source' });
    } finally {
      setCreatingDs(false);
    }
  };

  const handleDeleteDataSource = async (name: string) => {
    if (!activeApiId) return;
    try {
      await deleteAppSyncDataSource(activeApiId, name);
      setActionMessage({ type: 'success', text: `Data source "${name}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete data source' });
    }
  };

  const handleCreateResolver = async () => {
    if (!activeApiId || !resTypeName.trim() || !resFieldName.trim()) return;
    setCreatingResolver(true);
    try {
      await createAppSyncResolver(activeApiId, resTypeName.trim(), resFieldName.trim(), resDataSourceName.trim() || undefined);
      setActionMessage({ type: 'success', text: `Resolver ${resTypeName}.${resFieldName} attached.` });
      setCreateResolverOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create resolver' });
    } finally {
      setCreatingResolver(false);
    }
  };

  const handleDeleteResolver = async (typeName: string, fieldName: string) => {
    if (!activeApiId) return;
    try {
      await deleteAppSyncResolver(activeApiId, typeName, fieldName);
      setActionMessage({ type: 'success', text: `Resolver ${typeName}.${fieldName} deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete resolver' });
    }
  };

  const handleCreateApiKey = async () => {
    if (!activeApiId) return;
    setCreatingKey(true);
    try {
      await createAppSyncApiKey(activeApiId, keyDesc.trim());
      setActionMessage({ type: 'success', text: 'API Key generated.' });
      setCreateKeyOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create API key' });
    } finally {
      setCreatingKey(false);
    }
  };

  const handleDeleteApiKey = async (keyId: string) => {
    if (!activeApiId) return;
    try {
      await deleteAppSyncApiKey(activeApiId, keyId);
      setActionMessage({ type: 'success', text: 'API Key deleted.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete API key' });
    }
  };

  const handleRunGraphQLQuery = async () => {
    if (!gqlQuery.trim() || !activeApiId) return;
    setRunningGql(true);
    setGqlResponse(null);
    try {
      const variables = JSON.parse(gqlVariables || '{}');
      if (!variables || Array.isArray(variables) || typeof variables !== 'object') throw new Error('Query variables must be a JSON object');
      const result = await runAppSyncGraphQL(activeApiId, gqlQuery, variables, activeApi?.api_keys?.[0]?.id);
      setGqlResponse(JSON.stringify(result, null, 2));
    } catch (err: any) {
      setGqlResponse(JSON.stringify({ errors: [{ message: err.message || 'GraphQL Query execution failed' }] }, null, 2));
    } finally {
      setRunningGql(false);
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="AWS AppSync creates robust GraphQL and Pub/Sub APIs that securely connect applications to data and events."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateApiOpen(true)}>
              Create GraphQL API
            </Button>
          </SpaceBetween>
        }
      >
        AWS AppSync
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {/* Metrics */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
        <Container>
          <Box variant="awsui-key-label">GraphQL APIs</Box>
          <Box variant="awsui-value-large">{(data.graphql_apis || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Data Sources</Box>
          <Box variant="awsui-value-large">{(data.data_sources || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Resolvers</Box>
          <Box variant="awsui-value-large">{(data.resolvers || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">API Keys</Box>
          <Box variant="awsui-value-large">{(data.api_keys || []).length}</Box>
        </Container>
      </Grid>

      <Tabs
        activeTabId={activeTab || 'apis'}
        onChange={({ detail }) => onTabChange && onTabChange(detail.activeTabId)}
        tabs={[
          {
            label: `APIs (${(data.graphql_apis || []).length})`,
            id: 'apis',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateApiOpen(true)}>
                        Create API
                      </Button>
                    }
                  >
                    GraphQL APIs
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                    filteringPlaceholder="Filter APIs..."
                  />
                  <Table
                    columnDefinitions={[
                      {
                        id: 'name',
                        header: 'API Name',
                        cell: (item: any) => (
                          <Button variant="inline-link" onClick={() => setSelectedApis([item])}>
                            <strong>{item.name}</strong>
                          </Button>
                        ),
                      },
                      {
                        id: 'apiId',
                        header: 'API ID',
                        cell: (item: any) => <code>{item.apiId || item.id}</code>,
                        width: 170,
                      },
                      {
                        id: 'auth',
                        header: 'Default Auth Mode',
                        cell: (item: any) => <Badge color="blue">{item.authenticationType || 'API_KEY'}</Badge>,
                        width: 160,
                      },
                      {
                        id: 'endpoint',
                        header: 'GraphQL Endpoint',
                        cell: (item: any) => (
                          <Link href={item.uris?.GRAPHQL || `http://localhost:4566/graphql/${item.apiId || item.id}`} external>
                            {item.uris?.GRAPHQL || `http://localhost:4566/graphql/${item.apiId || item.id}`}
                          </Link>
                        ),
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <Button iconName="remove" onClick={() => handleDeleteApi(item.apiId || item.id)}>
                            Delete
                          </Button>
                        ),
                        width: 110,
                      },
                    ]}
                    items={filteredApis}
                    selectionType="single"
                    selectedItems={selectedApis}
                    onSelectionChange={({ detail }) => setSelectedApis(detail.selectedItems)}
                    empty={<Box textAlign="center">No AppSync GraphQL APIs created.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: 'Schema Editor',
            id: 'schema',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeApi ? `Editing schema for "${activeApi.name}"` : 'Select an API'}
                    actions={
                      <Button variant="primary" loading={savingSchema} onClick={handleSaveSchema}>
                        Save & Deploy Schema
                      </Button>
                    }
                  >
                    GraphQL Schema (SDL)
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <FormField label="Schema Definition">
                    <Textarea rows={16} value={schemaDoc} onChange={({ detail }) => setSchemaDoc(detail.value)} />
                  </FormField>
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Data Sources (${(data.data_sources || []).length})`,
            id: 'datasources',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateDsOpen(true)}>
                        Create Data Source
                      </Button>
                    }
                  >
                    Data Sources
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'name', header: 'Data Source Name', cell: (i: any) => <strong>{i.name}</strong> },
                    { id: 'type', header: 'Type', cell: (i: any) => <Badge color="blue">{i.type || i.source_type || 'AMAZON_DYNAMODB'}</Badge>, width: 180 },
                    { id: 'desc', header: 'Description', cell: (i: any) => i.description || '—' },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (i: any) => (
                        <Button iconName="remove" onClick={() => handleDeleteDataSource(i.name)}>
                          Delete
                        </Button>
                      ),
                      width: 110,
                    },
                  ]}
                  items={data.data_sources || []}
                  empty={<Box textAlign="center">No data sources configured.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Resolvers (${(data.resolvers || []).length})`,
            id: 'resolvers',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateResolverOpen(true)}>
                        Attach Resolver
                      </Button>
                    }
                  >
                    Field Resolvers
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'field', header: 'GraphQL Field', cell: (i: any) => <strong>{`${i.typeName || i.type_name}.${i.fieldName || i.field_name}`}</strong> },
                    { id: 'dataSource', header: 'Data Source Name', cell: (i: any) => <code>{i.dataSourceName || i.data_source_name || 'NONE'}</code>, width: 220 },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (i: any) => (
                        <Button iconName="remove" onClick={() => handleDeleteResolver(i.typeName || i.type_name, i.fieldName || i.field_name)}>
                          Delete
                        </Button>
                      ),
                      width: 110,
                    },
                  ]}
                  items={data.resolvers || []}
                  empty={<Box textAlign="center">No field resolvers attached to schema.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `API Keys (${(data.api_keys || []).length})`,
            id: 'apikeys',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateKeyOpen(true)}>
                        Generate API Key
                      </Button>
                    }
                  >
                    API Keys
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'id', header: 'API Key ID', cell: (i: any) => <code>{i.id || 'da2-sample1234567890'}</code> },
                    { id: 'desc', header: 'Description', cell: (i: any) => i.description || 'Workbench Key' },
                    { id: 'expires', header: 'Expires', cell: (i: any) => i.expires || '30 days', width: 140 },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (i: any) => (
                        <Button iconName="remove" onClick={() => handleDeleteApiKey(i.id)}>
                          Delete
                        </Button>
                      ),
                      width: 110,
                    },
                  ]}
                  items={activeApi?.api_keys || []}
                  empty={<Box textAlign="center">No API keys reported for this API.</Box>}
                />
              </Container>
            ),
          },
          {
            label: 'GraphQL Query Sandbox',
            id: 'sandbox',
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" disabled={!activeApiId || !gqlQuery.trim()} loading={runningGql} iconName="play" onClick={handleRunGraphQLQuery}>
                          Execute Query
                        </Button>
                      }
                    >
                      GraphQL Explorer
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <Alert type="info">Endpoint: {activeApiEndpoint}</Alert>
                    <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                      <FormField label="GraphQL Query / Mutation">
                        <Textarea rows={10} value={gqlQuery} onChange={({ detail }) => setGqlQuery(detail.value)} />
                      </FormField>
                      <FormField label="Query Variables (JSON)">
                        <Textarea rows={10} value={gqlVariables} onChange={({ detail }) => setGqlVariables(detail.value)} />
                      </FormField>
                    </Grid>
                  </SpaceBetween>
                </Container>

                {gqlResponse && (
                  <Container header={<Header variant="h3">Execution Result</Header>}>
                    <Textarea rows={10} value={gqlResponse} readOnly />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
        ]}
      />

      {/* Create API Modal */}
      <Modal
        visible={createApiOpen}
        onDismiss={() => setCreateApiOpen(false)}
        header="Create GraphQL API"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateApiOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingApi} onClick={handleCreateApi}>
                Create API
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="API Name">
            <Input value={apiName} onChange={({ detail }) => setApiName(detail.value)} placeholder="social-app-api" />
          </FormField>
          <FormField label="Default Authorization Mode">
            <Select
              selectedOption={authType}
              onChange={({ detail }) => setAuthType(detail.selectedOption as any)}
              options={[
                { label: 'API_KEY (Simple Key-based authentication)', value: 'API_KEY' },
                { label: 'AWS_IAM (Signature V4 signed)', value: 'AWS_IAM' },
                { label: 'AMAZON_COGNITO_USER_POOLS (Cognito JWT)', value: 'AMAZON_COGNITO_USER_POOLS' },
                { label: 'OPENID_CONNECT (OIDC)', value: 'OPENID_CONNECT' },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Data Source Modal */}
      <Modal
        visible={createDsOpen}
        onDismiss={() => setCreateDsOpen(false)}
        header="Create Data Source"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateDsOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingDs} onClick={handleCreateDataSource}>
                Create Data Source
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Data Source Name">
            <Input value={dsName} onChange={({ detail }) => setDsName(detail.value)} placeholder="PostsTableDataSource" />
          </FormField>
          <FormField label="Data Source Type">
            <Select
              selectedOption={dsType}
              onChange={({ detail }) => setDsType(detail.selectedOption as any)}
              options={[
                { label: 'AMAZON_DYNAMODB (NoSQL Table)', value: 'AMAZON_DYNAMODB' },
                { label: 'AWS_LAMBDA (Serverless Function)', value: 'AWS_LAMBDA' },
                { label: 'HTTP (REST Endpoint)', value: 'HTTP' },
                { label: 'NONE (Local Resolver)', value: 'NONE' },
              ]}
            />
          </FormField>
          <FormField label="Description">
            <Input value={dsDesc} onChange={({ detail }) => setDsDesc(detail.value)} placeholder="DynamoDB table for posts" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Resolver Modal */}
      <Modal
        visible={createResolverOpen}
        onDismiss={() => setCreateResolverOpen(false)}
        header="Attach Field Resolver"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateResolverOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingResolver} onClick={handleCreateResolver}>
                Attach Resolver
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
            <FormField label="Type Name">
              <Input value={resTypeName} onChange={({ detail }) => setResTypeName(detail.value)} placeholder="Query" />
            </FormField>
            <FormField label="Field Name">
              <Input value={resFieldName} onChange={({ detail }) => setResFieldName(detail.value)} placeholder="getPost" />
            </FormField>
          </Grid>
          <FormField label="Data Source Name">
            <Input value={resDataSourceName} onChange={({ detail }) => setResDataSourceName(detail.value)} placeholder="PostsTableDataSource" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create API Key Modal */}
      <Modal
        visible={createKeyOpen}
        onDismiss={() => setCreateKeyOpen(false)}
        header="Generate API Key"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateKeyOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingKey} onClick={handleCreateApiKey}>
                Generate Key
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Description">
          <Input value={keyDesc} onChange={({ detail }) => setKeyDesc(detail.value)} placeholder="Frontend Client Key" />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
