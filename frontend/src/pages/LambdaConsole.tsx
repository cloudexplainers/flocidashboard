import { FunctionOverview, FunctionLogs } from '../components/FunctionOverview';
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
import Grid from '@cloudscape-design/components/grid';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import Link from '@cloudscape-design/components/link';
import {
  fetchServiceInventory,
  executeServiceAction,
  fetchLambdaFunctionUrl,
  createLambdaFunctionUrl,
  deleteLambdaFunctionUrl,
  fetchLambdaEventSourceMappings,
  createLambdaEventSourceMapping,
  deleteLambdaEventSourceMapping,
  fetchLambdaVersions,
  publishLambdaVersion,
  updateLambdaFunctionConfiguration,
  updateLambdaEnvironmentVariables,
  fetchLambdaAliases,
  createLambdaAlias,
  deleteLambdaAlias,
} from '../api/client';

interface FunctionItem {
  FunctionName: string;
  FunctionArn: string;
  Runtime: string;
  Handler: string;
  CodeSize?: number;
  Description?: string;
  Timeout?: number;
  MemorySize?: number;
  LastModified?: string;
  Environment?: { Variables?: Record<string, string> };
  Layers?: any[];
}

interface LambdaConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const LambdaConsole: React.FC<LambdaConsoleProps> = ({ activeTab, onTabChange }) => {
  const [functions, setFunctions] = useState<FunctionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFunctions, setSelectedFunctions] = useState<FunctionItem[]>([]);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'code');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [functionName, setFunctionName] = useState('');
  const [runtime, setRuntime] = useState({ label: 'Python 3.12', value: 'python3.12' });
  const [handler, setHandler] = useState('lambda_function.lambda_handler');
  const [creating, setCreating] = useState(false);

  const [invokeModalOpen, setInvokeModalOpen] = useState(false);
  const [invokePayload, setInvokePayload] = useState('{\n  "key1": "value1",\n  "key2": "value2"\n}');
  const [invokeResponse, setInvokeResponse] = useState<string | null>(null);
  const [invoking, setInvoking] = useState(false);

  const [functionUrlConfig, setFunctionUrlConfig] = useState<any | null>(null);
  const [urlAuthType, setUrlAuthType] = useState({ label: 'NONE (Public)', value: 'NONE' });
  const [savingUrl, setSavingUrl] = useState(false);

  const [triggers, setTriggers] = useState<any[]>([]);
  const [addTriggerOpen, setAddTriggerOpen] = useState(false);
  const [triggerSourceArn, setTriggerSourceArn] = useState('arn:aws:sqs:us-east-1:000000000000:my-queue');
  const [triggerBatchSize, setTriggerBatchSize] = useState('10');
  const [savingTrigger, setSavingTrigger] = useState(false);

  const [versions, setVersions] = useState<any[]>([]);
  const [publishVersionOpen, setPublishVersionOpen] = useState(false);
  const [versionDescription, setVersionDescription] = useState('');
  const [publishingVersion, setPublishingVersion] = useState(false);

  const [envVars, setEnvVars] = useState<Array<{ key: string; value: string }>>([]);
  const [newEnvKey, setNewEnvKey] = useState('');
  const [newEnvVal, setNewEnvVal] = useState('');
  const [savingEnv, setSavingEnv] = useState(false);

  const [cfgMemory, setCfgMemory] = useState('128');
  const [cfgTimeout, setCfgTimeout] = useState('30');
  const [cfgDescription, setCfgDescription] = useState('');
  const [cfgHandler, setCfgHandler] = useState('');
  const [savingConfig, setSavingConfig] = useState(false);

  const [aliases, setAliases] = useState<any[]>([]);
  const [createAliasOpen, setCreateAliasOpen] = useState(false);
  const [aliasName, setAliasName] = useState('');
  const [aliasVersion, setAliasVersion] = useState('$LATEST');
  const [aliasDesc, setAliasDesc] = useState('');
  const [savingAlias, setSavingAlias] = useState(false);

  const loadFunctions = async () => {
    setLoading(true);
    try {
      const data = await fetchServiceInventory('lambda');
      const list = (data.functions || data.Functions || []).map((f: any) => ({
        FunctionName: f.FunctionName || f.name || 'my-function',
        FunctionArn: f.FunctionArn || f.arn || 'Unavailable',
        Runtime: f.Runtime || f.runtime || 'python3.12',
        Handler: f.Handler || f.handler || 'index.handler',
        CodeSize: f.CodeSize ?? f.configuration?.CodeSize,
        Description: f.Description || f.description || 'Serverless compute function',
        Timeout: f.Timeout ?? f.timeout ?? 30,
        MemorySize: f.MemorySize ?? f.memory_size ?? 128,
        LastModified: f.LastModified || f.last_modified || 'Unavailable',
        Environment: f.Environment || f.environment,
        Layers: f.Layers || f.layers || [],
      }));
      setFunctions(list);
      if (list.length > 0 && selectedFunctions.length === 0) {
        setSelectedFunctions([list[0]]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFunctions();
  }, []);

  const activeFunction = selectedFunctions[0] || null;

  const loadFunctionDetails = async (fn: FunctionItem) => {
    try {
      const [urlRes, triggersRes, versionsRes, aliasesRes] = await Promise.all([
        fetchLambdaFunctionUrl(fn.FunctionName),
        fetchLambdaEventSourceMappings(fn.FunctionName),
        fetchLambdaVersions(fn.FunctionName),
        fetchLambdaAliases(fn.FunctionName),
      ]);
      setFunctionUrlConfig(urlRes);
      setTriggers(triggersRes || []);
      setVersions(versionsRes || []);
      setAliases(aliasesRes || []);
    } catch (err) {
      console.error(err);
    }

    setCfgMemory(String(fn.MemorySize || 128));
    setCfgTimeout(String(fn.Timeout || 30));
    setCfgDescription(fn.Description || '');
    setCfgHandler(fn.Handler || 'index.handler');

    const envMap = fn.Environment?.Variables || {};
    setEnvVars(Object.entries(envMap).map(([k, v]) => ({ key: k, value: String(v) })));
  };

  useEffect(() => {
    if (activeFunction) {
      loadFunctionDetails(activeFunction);
      setInvokeResponse(null);
    } else {
      setFunctionUrlConfig(null);
      setTriggers([]);
      setVersions([]);
      setAliases([]);
    }
  }, [activeFunction?.FunctionName]);

  const handleCreateFunction = async () => {
    if (!functionName.trim()) return;
    setCreating(true);
    setActionMessage(null);
    try {
      await executeServiceAction('lambda', 'create_function', {
        FunctionName: functionName.trim(),
        Runtime: runtime.value,
        Handler: handler.trim(),
        Code: { ZipFile: 'dummy' },
        Role: 'arn:aws:iam::000000000000:role/lambda-basic-execution',
      });
      setActionMessage({ type: 'success', text: `Lambda function "${functionName.trim()}" created successfully.` });
      setCreateModalOpen(false);
      setFunctionName('');
      await loadFunctions();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create Lambda function' });
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteFunction = async () => {
    if (!activeFunction) return;
    try {
      await executeServiceAction('lambda', 'delete_function', { FunctionName: activeFunction.FunctionName });
      setActionMessage({ type: 'success', text: `Function "${activeFunction.FunctionName}" deleted.` });
      setSelectedFunctions([]);
      await loadFunctions();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete function' });
    }
  };

  const handleInvokeFunction = async () => {
    if (!activeFunction) return;
    setInvoking(true);
    setActionMessage(null);
    try {
      const res = await executeServiceAction('lambda', 'invoke', {
        FunctionName: activeFunction.FunctionName,
        Payload: invokePayload,
      });
      setInvokeResponse(JSON.stringify(res, null, 2));
      setActionMessage({ type: 'success', text: 'Function invoked successfully.' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to invoke function' });
    } finally {
      setInvoking(false);
    }
  };

  const handleToggleFunctionUrl = async (enable: boolean) => {
    if (!activeFunction) return;
    setSavingUrl(true);
    try {
      if (enable) {
        const res = await createLambdaFunctionUrl(activeFunction.FunctionName, urlAuthType.value as any);
        setFunctionUrlConfig(res);
        setActionMessage({ type: 'success', text: 'Function URL enabled.' });
      } else {
        await deleteLambdaFunctionUrl(activeFunction.FunctionName);
        setFunctionUrlConfig(null);
        setActionMessage({ type: 'success', text: 'Function URL disabled.' });
      }
      await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update Function URL' });
    } finally {
      setSavingUrl(false);
    }
  };

  const handleAddTrigger = async () => {
    if (!activeFunction || !triggerSourceArn.trim()) return;
    setSavingTrigger(true);
    try {
      await createLambdaEventSourceMapping(
        activeFunction.FunctionName,
        triggerSourceArn.trim(),
        Number(triggerBatchSize) || 10
      );
      setActionMessage({ type: 'success', text: 'Event source mapping created.' });
      setAddTriggerOpen(false);
      await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to add trigger' });
    } finally {
      setSavingTrigger(false);
    }
  };

  const handleDeleteTrigger = async (uuid: string) => {
    try {
      await deleteLambdaEventSourceMapping(uuid);
      setActionMessage({ type: 'success', text: 'Trigger mapping removed.' });
      if (activeFunction) await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete trigger' });
    }
  };

  const handlePublishVersion = async () => {
    if (!activeFunction) return;
    setPublishingVersion(true);
    try {
      await publishLambdaVersion(activeFunction.FunctionName, versionDescription.trim());
      setActionMessage({ type: 'success', text: 'New version published.' });
      setPublishVersionOpen(false);
      setVersionDescription('');
      await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to publish version' });
    } finally {
      setPublishingVersion(false);
    }
  };

  const handleSaveEnvVars = async () => {
    if (!activeFunction) return;
    setSavingEnv(true);
    try {
      const varsObj: Record<string, string> = {};
      envVars.forEach((v) => {
        if (v.key.trim()) varsObj[v.key.trim()] = v.value;
      });
      await updateLambdaEnvironmentVariables(activeFunction.FunctionName, varsObj);
      setActionMessage({ type: 'success', text: 'Environment variables saved successfully.' });
      await loadFunctions();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to save environment variables' });
    } finally {
      setSavingEnv(false);
    }
  };

  const handleSaveConfiguration = async () => {
    if (!activeFunction) return;
    setSavingConfig(true);
    try {
      await updateLambdaFunctionConfiguration(activeFunction.FunctionName, {
        memorySize: Number(cfgMemory) || 128,
        timeout: Number(cfgTimeout) || 30,
        description: cfgDescription.trim(),
        handler: cfgHandler.trim(),
      });
      setActionMessage({ type: 'success', text: 'Function configuration saved.' });
      await loadFunctions();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to save configuration' });
    } finally {
      setSavingConfig(false);
    }
  };

  const handleCreateAlias = async () => {
    if (!activeFunction || !aliasName.trim()) return;
    setSavingAlias(true);
    try {
      await createLambdaAlias(activeFunction.FunctionName, aliasName.trim(), aliasVersion, aliasDesc.trim());
      setActionMessage({ type: 'success', text: `Alias "${aliasName.trim()}" created.` });
      setCreateAliasOpen(false);
      setAliasName('');
      await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create alias' });
    } finally {
      setSavingAlias(false);
    }
  };

  const handleDeleteAlias = async (name: string) => {
    if (!activeFunction) return;
    try {
      await deleteLambdaAlias(activeFunction.FunctionName, name);
      setActionMessage({ type: 'success', text: `Alias "${name}" deleted.` });
      await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete alias' });
    }
  };

  const filteredFunctions = functions.filter((f) =>
    f.FunctionName.toLowerCase().includes(filterText.toLowerCase())
  );

  const functionUrlEndpoint = activeFunction
    ? `http://${activeFunction.FunctionName}.lambda-url.localhost:4566/`
    : '';

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            counter={`(${functions.length})`}
            description="Run code without thinking about servers. Scalable, event-driven serverless compute."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadFunctions} loading={loading}>
                  Refresh
                </Button>
                <Button disabled={!activeFunction} onClick={handleDeleteFunction}>
                  Delete function
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateModalOpen(true)}>
                  Create function
                </Button>
              </SpaceBetween>
            }
          >
            AWS Lambda
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
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Total Functions</Box>
            <Box variant="h1" color="text-status-info">
              {functions.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active Region</Box>
            <Box variant="h2" color="text-status-info">
              us-east-1
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Execution Engine</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">Serverless Ready</StatusIndicator>
            </Box>
          </Box>
        </div>
      </Container>

      <Container
        header={
          <Header variant="h2" description="Lambda functions deployed in Floci environment.">
            Functions ({functions.length})
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Find function by name..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />

          <Table
            columnDefinitions={[
              {
                id: 'name',
                header: 'Function name',
                cell: (item) => (
                  <Button variant="inline-link" onClick={() => setSelectedFunctions([item])}>
                    <strong>{item.FunctionName}</strong>
                  </Button>
                ),
              },
              {
                id: 'runtime',
                header: 'Runtime',
                cell: (item) => <Badge color="blue">{item.Runtime}</Badge>,
                width: 150,
              },
              {
                id: 'handler',
                header: 'Handler',
                cell: (item) => <code>{item.Handler}</code>,
                width: 240,
              },
              {
                id: 'memory',
                header: 'Memory / Timeout',
                cell: (item) => `${item.MemorySize || 128} MB / ${item.Timeout || 30}s`,
                width: 180,
              },
            ]}
            items={filteredFunctions}
            selectionType="single"
            selectedItems={selectedFunctions}
            onSelectionChange={({ detail }) => setSelectedFunctions(detail.selectedItems)}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No Lambda functions found</b>
                <p>Create a function to execute event-driven serverless code.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {activeFunction && (
        <Container
          header={
            <Header variant="h2" description={`Inspecting function: ${activeFunction.FunctionName}`}>
              Function: {activeFunction.FunctionName}
            </Header>
          }
        >
          <FunctionOverview name={activeFunction.FunctionName} runtime={activeFunction.Runtime} triggers={triggers} onShowTriggers={() => { setSelectedTabId('triggers'); onTabChange?.('triggers'); }} />
          <Tabs
            activeTabId={selectedTabId}
            onChange={({ detail }) => {
              setSelectedTabId(detail.activeTabId);
              onTabChange?.(detail.activeTabId);
            }}
            tabs={[
              {
                label: 'Code & Test Invocation',
                id: 'code',
                content: (
                  <SpaceBetween size="m">
                    <Box float="right">
                      <Button variant="primary" iconName="caret-right-filled" onClick={() => setInvokeModalOpen(true)}>
                        Test / Invoke Function
                      </Button>
                    </Box>

                    <Container header={<Header variant="h3">Function Code & Handler</Header>}>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Function ARN', value: activeFunction.FunctionArn },
                          { label: 'Runtime', value: activeFunction.Runtime },
                          { label: 'Handler', value: activeFunction.Handler },
                          { label: 'Code Size', value: activeFunction.CodeSize == null ? 'Unavailable' : `${activeFunction.CodeSize} Bytes` },
                        ]}
                      />
                    </Container>

                    {invokeResponse && (
                      <Container header={<Header variant="h3">Execution Result</Header>}>
                        <Textarea rows={6} value={invokeResponse} readOnly />
                      </Container>
                    )}
                  </SpaceBetween>
                ),
              },
              { label: 'Monitor', id: 'monitor', content: <FunctionLogs name={activeFunction.FunctionName} /> },
              {
                label: `Environment Variables (${envVars.length})`,
                id: 'env',
                content: (
                  <SpaceBetween size="m">
                    <Box float="right">
                      <Button variant="primary" loading={savingEnv} onClick={handleSaveEnvVars}>
                        Save Variables
                      </Button>
                    </Box>

                    <Table
                      columnDefinitions={[
                        { id: 'key', header: 'Key', cell: (v) => <strong>{v.key}</strong> },
                        { id: 'value', header: 'Value', cell: (v) => <code>{v.value}</code> },
                        {
                          id: 'act',
                          header: 'Action',
                          cell: (v) => (
                            <Button iconName="remove" onClick={() => setEnvVars(envVars.filter((e) => e.key !== v.key))}>
                              Remove
                            </Button>
                          ),
                          width: 110,
                        },
                      ]}
                      items={envVars}
                      empty={<Box textAlign="center">No environment variables defined.</Box>}
                    />

                    <Grid gridDefinition={[{ colspan: { default: 12, s: 5 } }, { colspan: { default: 12, s: 5 } }, { colspan: { default: 12, s: 2 } }]}>
                      <FormField label="Variable Key">
                        <Input value={newEnvKey} onChange={({ detail }) => setNewEnvKey(detail.value)} placeholder="DATABASE_URL" />
                      </FormField>
                      <FormField label="Variable Value">
                        <Input value={newEnvVal} onChange={({ detail }) => setNewEnvVal(detail.value)} placeholder="postgres://..." />
                      </FormField>
                      <Box margin={{ top: 'l' }}>
                        <Button
                          onClick={() => {
                            if (!newEnvKey.trim()) return;
                            setEnvVars([...envVars.filter((e) => e.key !== newEnvKey.trim()), { key: newEnvKey.trim(), value: newEnvVal.trim() }]);
                            setNewEnvKey('');
                            setNewEnvVal('');
                          }}
                        >
                          Add Pair
                        </Button>
                      </Box>
                    </Grid>
                  </SpaceBetween>
                ),
              },
              {
                label: 'General Configuration',
                id: 'config',
                content: (
                  <SpaceBetween size="m">
                    <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                      <FormField label="Memory Size (MB)" description="Amount of memory allocated to the function (128 MB to 10,240 MB).">
                        <Input value={cfgMemory} onChange={({ detail }) => setCfgMemory(detail.value)} type="number" />
                      </FormField>
                      <FormField label="Timeout (Seconds)" description="Execution timeout duration (1 to 900 seconds).">
                        <Input value={cfgTimeout} onChange={({ detail }) => setCfgTimeout(detail.value)} type="number" />
                      </FormField>
                    </Grid>

                    <FormField label="Description">
                      <Input value={cfgDescription} onChange={({ detail }) => setCfgDescription(detail.value)} placeholder="Function description..." />
                    </FormField>

                    <FormField label="Handler Entrypoint">
                      <Input value={cfgHandler} onChange={({ detail }) => setCfgHandler(detail.value)} placeholder="index.handler" />
                    </FormField>

                    <Button variant="primary" loading={savingConfig} onClick={handleSaveConfiguration}>
                      Save Configuration
                    </Button>
                  </SpaceBetween>
                ),
              },
              {
                label: `Aliases (${aliases.length})`,
                id: 'aliases',
                content: (
                  <SpaceBetween size="m">
                    <Box float="right">
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateAliasOpen(true)}>
                        Create Alias
                      </Button>
                    </Box>

                    <Table
                      columnDefinitions={[
                        { id: 'name', header: 'Alias Name', cell: (a) => <strong>{a.Name || a.name}</strong> },
                        { id: 'version', header: 'Function Version', cell: (a) => <Badge color="blue">{a.FunctionVersion || a.functionVersion || '$LATEST'}</Badge> },
                        { id: 'desc', header: 'Description', cell: (a) => a.Description || a.description || '—' },
                        {
                          id: 'act',
                          header: 'Action',
                          cell: (a) => (
                            <Button iconName="remove" onClick={() => handleDeleteAlias(a.Name || a.name)}>
                              Delete
                            </Button>
                          ),
                          width: 110,
                        },
                      ]}
                      items={aliases}
                      empty={<Box textAlign="center">No aliases created for this function.</Box>}
                    />
                  </SpaceBetween>
                ),
              },
              {
                label: 'Function URL',
                id: 'url',
                content: (
                  <Container
                    header={
                      <Header
                        variant="h3"
                        actions={
                          functionUrlConfig ? (
                            <Button loading={savingUrl} onClick={() => handleToggleFunctionUrl(false)}>
                              Delete Function URL
                            </Button>
                          ) : (
                            <Button variant="primary" loading={savingUrl} onClick={() => handleToggleFunctionUrl(true)}>
                              Create Function URL
                            </Button>
                          )
                        }
                      >
                        Function URL Configuration
                      </Header>
                    }
                  >
                    <SpaceBetween size="m">
                      <Box>
                        Status:{' '}
                        {functionUrlConfig ? (
                          <StatusIndicator type="success">Active</StatusIndicator>
                        ) : (
                          <StatusIndicator type="stopped">Not Configured</StatusIndicator>
                        )}
                      </Box>
                      {!functionUrlConfig && (
                        <FormField label="Auth Type">
                          <Select
                            selectedOption={urlAuthType}
                            onChange={({ detail }) => setUrlAuthType(detail.selectedOption as any)}
                            options={[
                              { label: 'NONE (Public Unauthenticated)', value: 'NONE' },
                              { label: 'AWS_IAM (Signed with SigV4)', value: 'AWS_IAM' },
                            ]}
                          />
                        </FormField>
                      )}
                      {functionUrlConfig && (
                        <Alert type="info">
                          Function URL: <Link href={functionUrlEndpoint} external>{functionUrlEndpoint}</Link>
                        </Alert>
                      )}
                    </SpaceBetween>
                  </Container>
                ),
              },
              {
                label: `Triggers (${triggers.length})`,
                id: 'triggers',
                content: (
                  <Container
                    header={
                      <Header
                        variant="h3"
                        actions={
                          <Button variant="primary" iconName="add-plus" onClick={() => setAddTriggerOpen(true)}>
                            Add Trigger
                          </Button>
                        }
                      >
                        Event Source Mappings
                      </Header>
                    }
                  >
                    <Table
                      columnDefinitions={[
                        { id: 'source', header: 'Event Source ARN', cell: (i: any) => <code>{i.EventSourceArn || i.eventSourceArn}</code> },
                        { id: 'batch', header: 'Batch Size', cell: (i: any) => i.BatchSize || 10, width: 120 },
                        {
                          id: 'action',
                          header: 'Action',
                          cell: (i: any) => (
                            <Button iconName="remove" onClick={() => handleDeleteTrigger(i.UUID || i.uuid)}>
                              Delete
                            </Button>
                          ),
                          width: 100,
                        },
                      ]}
                      items={triggers}
                      empty={<Box textAlign="center">No event source triggers attached.</Box>}
                    />
                  </Container>
                ),
              },
              {
                label: `Versions (${versions.length})`,
                id: 'versions',
                content: (
                  <Container
                    header={
                      <Header
                        variant="h3"
                        actions={
                          <Button variant="primary" iconName="add-plus" onClick={() => setPublishVersionOpen(true)}>
                            Publish New Version
                          </Button>
                        }
                      >
                        Published Versions
                      </Header>
                    }
                  >
                    <Table
                      columnDefinitions={[
                        { id: 'version', header: 'Version', cell: (i: any) => <strong>{i.Version || i.version || '$LATEST'}</strong>, width: 120 },
                        { id: 'desc', header: 'Description', cell: (i: any) => i.Description || i.description || '—' },
                      ]}
                      items={versions.length > 0 ? versions : [{ Version: '$LATEST', Description: 'Active working draft' }]}
                    />
                  </Container>
                ),
              },
            ]}
          />
        </Container>
      )}

      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create Lambda Function"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creating} onClick={handleCreateFunction}>
                Create Function
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Function Name">
            <Input value={functionName} onChange={({ detail }) => setFunctionName(detail.value)} placeholder="my-function" />
          </FormField>
          <FormField label="Runtime">
            <Select
              selectedOption={runtime}
              onChange={({ detail }) => setRuntime(detail.selectedOption as any)}
              options={[
                { label: 'Python 3.12', value: 'python3.12' },
                { label: 'Node.js 20.x', value: 'nodejs20.x' },
              ]}
            />
          </FormField>
          <FormField label="Handler">
            <Input value={handler} onChange={({ detail }) => setHandler(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={invokeModalOpen}
        onDismiss={() => setInvokeModalOpen(false)}
        header={`Test Invocation: ${activeFunction?.FunctionName}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setInvokeModalOpen(false)}>
                Close
              </Button>
              <Button variant="primary" loading={invoking} onClick={handleInvokeFunction}>
                Execute Function
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Event JSON Payload">
            <Textarea
              rows={8}
              value={invokePayload}
              onChange={({ detail }) => setInvokePayload(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={addTriggerOpen}
        onDismiss={() => setAddTriggerOpen(false)}
        header="Add Event Source Trigger"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setAddTriggerOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingTrigger} onClick={handleAddTrigger}>
                Add Trigger
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Event Source ARN" description="SQS queue, DynamoDB stream, or Kinesis stream ARN.">
            <Input
              value={triggerSourceArn}
              onChange={({ detail }) => setTriggerSourceArn(detail.value)}
            />
          </FormField>
          <FormField label="Batch Size">
            <Input
              value={triggerBatchSize}
              onChange={({ detail }) => setTriggerBatchSize(detail.value)}
              type="number"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      <Modal
        visible={publishVersionOpen}
        onDismiss={() => setPublishVersionOpen(false)}
        header={`Publish New Version of ${activeFunction?.FunctionName}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setPublishVersionOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={publishingVersion} onClick={handlePublishVersion}>
                Publish Version
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Version Description (Optional)">
          <Input
            value={versionDescription}
            onChange={({ detail }) => setVersionDescription(detail.value)}
            placeholder="Release v1.2"
          />
        </FormField>
      </Modal>

      <Modal
        visible={createAliasOpen}
        onDismiss={() => setCreateAliasOpen(false)}
        header={`Create Alias for ${activeFunction?.FunctionName}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateAliasOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingAlias} onClick={handleCreateAlias}>
                Create Alias
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Alias Name">
            <Input value={aliasName} onChange={({ detail }) => setAliasName(detail.value)} placeholder="PROD or STAGE" />
          </FormField>
          <FormField label="Target Version">
            <Input value={aliasVersion} onChange={({ detail }) => setAliasVersion(detail.value)} placeholder="$LATEST or 1" />
          </FormField>
          <FormField label="Description (Optional)">
            <Input value={aliasDesc} onChange={({ detail }) => setAliasDesc(detail.value)} placeholder="Production deployment alias" />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
