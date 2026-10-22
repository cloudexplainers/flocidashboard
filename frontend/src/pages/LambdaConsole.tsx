import { useSession } from '../components/SessionContext';
import { FunctionOverview, FunctionLogs } from '../components/FunctionOverview';
import React, { useState, useEffect, useRef } from 'react';
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
import Alert from '@cloudscape-design/components/alert';
import Link from '@cloudscape-design/components/link';
import Autosuggest from '@cloudscape-design/components/autosuggest';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate, formatBytes } from '../format';
import {
  fetchServiceInventory,
  createLambdaFunction,
  deleteLambdaFunction,
  invokeLambdaFunction,
  LambdaInvokeResult,
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
  State?: string;
  StateReason?: string;
  LastUpdateStatus?: string;
  Role?: string;
  PackageType?: string;
  Architectures?: string[];
  EphemeralStorage?: number;
  LogGroup?: string;
}

const STARTER_CODE: Record<string, { fileName: string; handler: string; source: string }> = {
  python: {
    fileName: 'lambda_function.py',
    handler: 'lambda_function.lambda_handler',
    source: "import json\n\ndef lambda_handler(event, context):\n    # TODO implement\n    return {\n        'statusCode': 200,\n        'body': json.dumps('Hello from Lambda!')\n    }\n",
  },
  nodejs: {
    fileName: 'index.mjs',
    handler: 'index.handler',
    source: "export const handler = async (event) => {\n  // TODO implement\n  const response = {\n    statusCode: 200,\n    body: JSON.stringify('Hello from Lambda!'),\n  };\n  return response;\n};\n",
  },
};
const starterFor = (runtime: string) => STARTER_CODE[runtime.startsWith('python') ? 'python' : 'nodejs'];

interface LambdaConsoleProps {
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const LambdaConsole: React.FC<LambdaConsoleProps> = ({ activeTab, onTabChange, resourcePath, onNavigate }) => {
  const { identity } = useSession();
  const [inventoryError, setInventoryError] = useState('');
  const requestedResource = new URLSearchParams((resourcePath || '').split('?')[1] || '').get('function');
  const detailGeneration = useRef(0);
  const navigateResource = (name: string, tab = selectedTabId) => {
    if (onNavigate) onNavigate(`lambda/${tab}?${new URLSearchParams({ function: name })}`);
    else onTabChange?.(tab);
  };
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
  const [architecture, setArchitecture] = useState('x86_64');
  const [roleArn, setRoleArn] = useState('');
  const [lambdaRoles, setLambdaRoles] = useState<Array<{ name: string; arn: string }>>([]);
  const [creating, setCreating] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [aliasToDelete, setAliasToDelete] = useState<string | null>(null);

  const [invokeModalOpen, setInvokeModalOpen] = useState(false);
  const [invokePayload, setInvokePayload] = useState('{\n  "key1": "value1",\n  "key2": "value2",\n  "key3": "value3"\n}');
  const [invokeResponse, setInvokeResponse] = useState<LambdaInvokeResult | null>(null);
  const [invoking, setInvoking] = useState(false);

  const [functionUrlConfig, setFunctionUrlConfig] = useState<any | null>(null);
  const [urlAuthType, setUrlAuthType] = useState({ label: 'AWS_IAM', value: 'AWS_IAM' });
  const [savingUrl, setSavingUrl] = useState(false);

  const [triggers, setTriggers] = useState<any[]>([]);
  const [addTriggerOpen, setAddTriggerOpen] = useState(false);
  const [triggerSourceArn, setTriggerSourceArn] = useState('');
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
    setInventoryError('');
    try {
      const data = await fetchServiceInventory('lambda');
      if (data.error) throw new Error(data.error);
      const list = (data.functions || data.Functions || []).map((f: any) => ({
        FunctionName: f.FunctionName || f.name,
        FunctionArn: f.FunctionArn || f.arn || f.configuration?.FunctionArn,
        Runtime: f.Runtime || f.runtime || f.configuration?.Runtime,
        Handler: f.Handler || f.handler || f.configuration?.Handler,
        CodeSize: f.CodeSize ?? f.configuration?.CodeSize,
        Description: f.Description ?? f.description ?? f.configuration?.Description ?? '',
        Timeout: f.Timeout ?? f.timeout ?? f.configuration?.Timeout,
        MemorySize: f.MemorySize ?? f.memory_size ?? f.configuration?.MemorySize,
        LastModified: f.LastModified || f.last_modified || f.configuration?.LastModified,
        PackageType: f.PackageType || f.package_type || f.configuration?.PackageType,
        Architectures: f.Architectures || f.architectures || f.configuration?.Architectures,
        EphemeralStorage: f.configuration?.EphemeralStorage?.Size,
        LogGroup: f.configuration?.LoggingConfig?.LogGroup,
        Environment: f.Environment || f.environment,
        State: f.State || f.state || f.configuration?.State,
        StateReason: f.StateReason || f.configuration?.StateReason,
        LastUpdateStatus: f.LastUpdateStatus || f.configuration?.LastUpdateStatus,
        Role: f.Role || f.role || f.configuration?.Role,
        Layers: f.Layers || f.layers || [],
      }));
      setFunctions(list);
      setSelectedFunctions(previous => {
        const kept = previous.length ? list.filter((item: FunctionItem) => item.FunctionName === previous[0].FunctionName) : [];
        return kept.length ? kept : requestedResource ? [] : list.slice(0, 1);
      });
    } catch (err) {
      setInventoryError(err instanceof Error ? err.message : 'Inventory unavailable');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadFunctions();
  }, []);

  useEffect(() => {
    if (requestedResource) setSelectedFunctions(functions.filter(item => item.FunctionName === requestedResource));
  }, [requestedResource, functions]);

  const activeFunction = selectedFunctions[0] || null;

  const loadFunctionDetails = async (fn: FunctionItem) => {
    const generation = ++detailGeneration.current;
    setFunctionUrlConfig(null); setTriggers([]); setVersions([]); setAliases([]);
    try {
      const [urlRes, triggersRes, versionsRes, aliasesRes] = await Promise.all([
        fetchLambdaFunctionUrl(fn.FunctionName),
        fetchLambdaEventSourceMappings(fn.FunctionName),
        fetchLambdaVersions(fn.FunctionName),
        fetchLambdaAliases(fn.FunctionName),
      ]);
      if (generation !== detailGeneration.current) return;
      setFunctionUrlConfig(urlRes);
      setTriggers(triggersRes || []);
      setVersions(versionsRes || []);
      setAliases(aliasesRes || []);
    } catch (err) {
      console.error(err);
    }

    if (generation !== detailGeneration.current) return;
    setCfgMemory(fn.MemorySize == null ? '' : String(fn.MemorySize));
    setCfgTimeout(fn.Timeout == null ? '' : String(fn.Timeout));
    setCfgDescription(fn.Description || '');
    setCfgHandler(fn.Handler || '');

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
    return () => { detailGeneration.current += 1; };
  }, [activeFunction?.FunctionName]);

  useEffect(() => {
    if (!createModalOpen) return;
    // Execution roles are IAM roles whose trust policy lets lambda.amazonaws.com assume them.
    fetchServiceInventory('iam').then(data => {
      const roles = (data.roles || []).filter((role: any) => JSON.stringify(role.trust_policy || role.AssumeRolePolicyDocument || '').includes('lambda.amazonaws.com'))
        .map((role: any) => ({ name: role.name || role.RoleName, arn: role.arn || role.Arn }));
      setLambdaRoles(roles);
      setRoleArn(current => current || roles[0]?.arn || '');
    }).catch(() => setLambdaRoles([]));
  }, [createModalOpen]);

  const handleCreateFunction = async () => {
    const name = functionName.trim();
    if (!name || !roleArn.trim()) return;
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
      setActionMessage({ type: 'error', text: 'Function name must be 1 to 64 characters: letters, numbers, hyphens or underscores.' });
      return;
    }
    const starter = starterFor(runtime.value);
    setCreating(true);
    setActionMessage(null);
    try {
      await createLambdaFunction({ name, role: roleArn.trim(), runtime: runtime.value, handler: starter.handler, fileName: starter.fileName, source: starter.source, architecture });
      setActionMessage({ type: 'success', text: `Successfully created the function ${name}. You can now change its code and configuration.` });
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
    setDeleting(true);
    try {
      await deleteLambdaFunction(activeFunction.FunctionName);
      setActionMessage({ type: 'success', text: `Successfully deleted function ${activeFunction.FunctionName}.` });
      setSelectedFunctions([]);
      await loadFunctions();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete function' });
    } finally {
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  const handleInvokeFunction = async () => {
    if (!activeFunction) return;
    let payload: unknown;
    try { payload = JSON.parse(invokePayload); } catch { setActionMessage({ type: 'error', text: 'Test event must be valid JSON.' }); return; }
    setInvoking(true);
    setActionMessage(null);
    try {
      const res = await invokeLambdaFunction(activeFunction.FunctionName, payload);
      setInvokeResponse(res);
      setInvokeModalOpen(false);
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
    const memory = Number(cfgMemory);
    const timeout = Number(cfgTimeout);
    if (!Number.isInteger(memory) || memory < 128 || memory > 10240) {
      setActionMessage({ type: 'error', text: 'Memory must be a whole number of MB from 128 to 10240.' });
      return;
    }
    if (!Number.isInteger(timeout) || timeout < 1 || timeout > 900) {
      setActionMessage({ type: 'error', text: 'Timeout must be between 1 and 900 seconds (15 minutes).' });
      return;
    }
    setSavingConfig(true);
    try {
      await updateLambdaFunctionConfiguration(activeFunction.FunctionName, {
        memorySize: memory,
        timeout,
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

  const handleDeleteAlias = async () => {
    if (!activeFunction || !aliasToDelete) return;
    try {
      await deleteLambdaAlias(activeFunction.FunctionName, aliasToDelete);
      setActionMessage({ type: 'success', text: `Alias "${aliasToDelete}" deleted.` });
      await loadFunctionDetails(activeFunction);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete alias' });
    } finally {
      setAliasToDelete(null);
    }
  };

  const filteredFunctions = functions.filter((f) =>
    f.FunctionName.toLowerCase().includes(filterText.toLowerCase())
  );

  const functionUrlEndpoint: string = functionUrlConfig?.FunctionUrl || '';
  const functionUrlBrowser: string = functionUrlConfig?.BrowserUrl || functionUrlEndpoint;
  const invokePayloadText = (() => {
    const payload: any = invokeResponse?.payload;
    if (payload == null) return '';
    if (typeof payload === 'string') return payload;
    if ('json' in payload && payload.json !== undefined && payload.json !== null) return JSON.stringify(payload.json, null, 2);
    if ('raw' in payload) return String(payload.raw ?? '');
    return JSON.stringify(payload, null, 2);
  })();

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
                <Button disabled={!activeFunction} onClick={() => setDeleteOpen(true)}>
                  Delete function
                </Button>
                <Button variant="primary" onClick={() => setCreateModalOpen(true)}>
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
              {identity?.region || 'Unavailable'}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Inventory status</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type={inventoryError ? 'error' : loading ? 'loading' : 'success'}>{inventoryError || (loading ? 'Loading inventory' : 'Inventory loaded')}</StatusIndicator>
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
                  <Button variant="inline-link" onClick={() => { setSelectedFunctions([item]); navigateResource(item.FunctionName); }}>
                    <strong>{item.FunctionName}</strong>
                  </Button>
                ),
              },
              { id: 'description', header: 'Description', cell: (item) => item.Description || '-' },
              { id: 'package', header: 'Package type', cell: (item) => item.PackageType || '-', width: 120 },
              { id: 'runtime', header: 'Runtime', cell: (item) => item.Runtime || '-', width: 140 },
              {
                id: 'memory',
                header: 'Memory / Timeout',
                cell: (item) => (item.MemorySize == null || item.Timeout == null ? '-' : `${item.MemorySize} MB / ${item.Timeout}s`),
                width: 160,
              },
              { id: 'modified', header: 'Last modified', cell: (item) => formatAwsDate(item.LastModified), width: 280 },
            ]}
            items={filteredFunctions}
            loading={loading}
            loadingText="Loading functions"
            trackBy="FunctionName"
            selectionType="single"
            selectedItems={selectedFunctions}
            onSelectionChange={({ detail }) => { setSelectedFunctions(detail.selectedItems); if (detail.selectedItems[0]) navigateResource(detail.selectedItems[0].FunctionName); }}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No Lambda functions found</b>
                <p>Create a function to execute event-driven serverless code.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {requestedResource && !loading && !activeFunction && <Alert type="warning">Function “{requestedResource}” was not found. Select an available function.</Alert>}
      {activeFunction && (
        <Container
          header={
            <Header variant="h2" description={`Inspecting function: ${activeFunction.FunctionName}`}>
              Function: {activeFunction.FunctionName}
            </Header>
          }
        >
          <FunctionOverview name={activeFunction.FunctionName} runtime={activeFunction.Runtime} triggers={triggers} onShowTriggers={() => { setSelectedTabId('triggers'); navigateResource(activeFunction.FunctionName, 'triggers'); }} />
          <Tabs
            activeTabId={selectedTabId}
            onChange={({ detail }) => {
              setSelectedTabId(detail.activeTabId);
              if (activeFunction) navigateResource(activeFunction.FunctionName, detail.activeTabId); else onTabChange?.(detail.activeTabId);
            }}
            tabs={[
              {
                label: 'Code and test',
                id: 'code',
                content: (
                  <SpaceBetween size="m">
                    <Box float="right">
                      <Button variant="primary" onClick={() => setInvokeModalOpen(true)}>
                        Test / Invoke Function
                      </Button>
                    </Box>

                    {invokeResponse && (
                      <Alert
                        type={invokeResponse.function_error ? 'error' : 'success'}
                        header={invokeResponse.function_error ? `Execution result: failed (${invokeResponse.function_error})` : 'Execution result: succeeded'}
                        dismissible
                        onDismiss={() => setInvokeResponse(null)}
                      >
                        <SpaceBetween size="s">
                          <KeyValuePairs columns={3} items={[
                            { label: 'Status code', value: invokeResponse.status_code ?? '-' },
                            { label: 'Executed version', value: invokeResponse.executed_version || '-' },
                            { label: 'Function error', value: invokeResponse.function_error || '-' },
                          ]} />
                          <FormField label="Response">
                            <Textarea rows={5} readOnly value={invokePayloadText} />
                          </FormField>
                          <FormField label="Function Logs" description="The last 4 KB of the execution log (LogType Tail).">
                            <Textarea rows={6} readOnly value={invokeResponse.log_tail || ''} placeholder="Floci returned no LogResult for this invocation. Check the Monitor tab for the function's CloudWatch logs." />
                          </FormField>
                        </SpaceBetween>
                      </Alert>
                    )}

                    <Container header={<Header variant="h3">Function details</Header>}>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Function ARN', value: activeFunction.FunctionArn },
                          { label: 'State', value: <StatusIndicator type={activeFunction.State === 'Active' ? 'success' : activeFunction.State === 'Failed' ? 'error' : activeFunction.State === 'Pending' ? 'pending' : 'info'}>{activeFunction.State || '-'}</StatusIndicator> },
                          { label: 'State reason', value: activeFunction.StateReason || '-' },
                          { label: 'Last update status', value: activeFunction.LastUpdateStatus || '-' },
                          { label: 'Execution role', value: activeFunction.Role || '-' },
                          { label: 'Last modified', value: formatAwsDate(activeFunction.LastModified) },
                          { label: 'Runtime', value: activeFunction.Runtime || '-' },
                          { label: 'Handler', value: activeFunction.Handler || '-' },
                          { label: 'Architecture', value: activeFunction.Architectures?.join(', ') || '-' },
                          { label: 'Package size', value: activeFunction.CodeSize == null ? '-' : formatBytes(activeFunction.CodeSize) },
                        ]}
                      />
                    </Container>

                  </SpaceBetween>
                ),
              },
              { label: 'Monitor', id: 'monitor', content: <FunctionLogs name={activeFunction.FunctionName} /> },
              {
                label: `Environment variables (${envVars.length})`,
                id: 'env',
                content: (
                  <SpaceBetween size="m">
                    <Box float="right">
                      <Button variant="primary" loading={savingEnv} onClick={handleSaveEnvVars}>
                        Save
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
                          Add environment variable
                        </Button>
                      </Box>
                    </Grid>
                  </SpaceBetween>
                ),
              },
              {
                label: 'General configuration',
                id: 'config',
                content: (
                  <SpaceBetween size="m">
                    <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                      <FormField label="Memory" description="Your function is allocated CPU proportional to the memory configured. Set memory between 128 MB and 10240 MB.">
                        <Input value={cfgMemory} onChange={({ detail }) => setCfgMemory(detail.value)} type="number" />
                      </FormField>
                      <FormField label="Timeout (seconds)" description="Between 1 second and 15 minutes (900 seconds). The AWS default is 3 seconds.">
                        <Input value={cfgTimeout} onChange={({ detail }) => setCfgTimeout(detail.value)} type="number" />
                      </FormField>
                    </Grid>

                    <FormField label="Description">
                      <Input value={cfgDescription} onChange={({ detail }) => setCfgDescription(detail.value)} placeholder="Function description..." />
                    </FormField>

                    <KeyValuePairs columns={2} items={[
                      { label: 'Ephemeral storage', value: activeFunction.EphemeralStorage ? `${activeFunction.EphemeralStorage} MB` : '-' },
                      { label: 'Log group', value: activeFunction.LogGroup || `/aws/lambda/${activeFunction.FunctionName}` },
                    ]} />

                    <FormField label="Handler" description="The method in your function code that processes events, for example lambda_function.lambda_handler.">
                      <Input value={cfgHandler} onChange={({ detail }) => setCfgHandler(detail.value)} placeholder="index.handler" />
                    </FormField>

                    <Button variant="primary" loading={savingConfig} onClick={handleSaveConfiguration}>
                      Save
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
                      <Button variant="primary" onClick={() => setCreateAliasOpen(true)}>
                        Create alias
                      </Button>
                    </Box>

                    <Table
                      columnDefinitions={[
                        { id: 'name', header: 'Name', cell: (a) => a.Name || a.name },
                        { id: 'version', header: 'Versions', cell: (a) => {
                          const weights = a.RoutingConfig?.AdditionalVersionWeights || {};
                          const extra = Object.entries(weights).map(([version, weight]) => `${version} (${Math.round(Number(weight) * 100)}%)`);
                          return [a.FunctionVersion || a.functionVersion || '-', ...extra].join(', ');
                        } },
                        { id: 'desc', header: 'Description', cell: (a) => a.Description || a.description || '-' },
                        { id: 'arn', header: 'Alias ARN', cell: (a) => a.AliasArn || '-' },
                        {
                          id: 'act',
                          header: 'Actions',
                          cell: (a) => (
                            <Button variant="inline-link" onClick={() => setAliasToDelete(a.Name || a.name)}>
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
                              Delete
                            </Button>
                          ) : (
                            <Button variant="primary" loading={savingUrl} onClick={() => handleToggleFunctionUrl(true)}>
                              Create function URL
                            </Button>
                          )
                        }
                        description="A dedicated HTTP(S) endpoint for your function."
                      >
                        Function URL
                      </Header>
                    }
                  >
                    <SpaceBetween size="m">
                      {!functionUrlConfig && <Box color="text-status-inactive">No function URL. Create one to invoke this function over HTTP.</Box>}
                      {!functionUrlConfig && (
                        <FormField label="Auth type" description="AWS_IAM requires SigV4-signed requests. NONE lets anyone call the URL; Lambda still applies the function's resource-based policy.">
                          <Select
                            selectedOption={urlAuthType}
                            onChange={({ detail }) => setUrlAuthType(detail.selectedOption as any)}
                            options={[
                              { label: 'AWS_IAM', value: 'AWS_IAM', description: 'Only authenticated IAM users and roles can make requests.' },
                              { label: 'NONE', value: 'NONE', description: 'Lambda won\'t perform IAM authentication on requests.' },
                            ]}
                          />
                        </FormField>
                      )}
                      {functionUrlConfig && (
                        <KeyValuePairs columns={3} items={[
                          { label: 'Function URL', value: functionUrlEndpoint || '-' },
                          { label: 'Open from this browser', value: functionUrlBrowser ? <Link href={functionUrlBrowser} external>{functionUrlBrowser}</Link> : '-' },
                          { label: 'Auth type', value: functionUrlConfig.AuthType || '-' },
                          { label: 'Invoke mode', value: functionUrlConfig.InvokeMode || 'BUFFERED' },
                          { label: 'CORS', value: functionUrlConfig.Cors ? 'Enabled' : 'Disabled' },
                          { label: 'Created', value: formatAwsDate(functionUrlConfig.CreationTime) },
                          { label: 'Last modified', value: formatAwsDate(functionUrlConfig.LastModifiedTime) },
                        ]} />
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
                          <Button variant="primary" onClick={() => setAddTriggerOpen(true)}>
                            Add trigger
                          </Button>
                        }
                        description="Event source mappings that poll SQS, Kinesis or DynamoDB Streams and invoke this function."
                      >
                        Triggers
                      </Header>
                    }
                  >
                    <Table
                      columnDefinitions={[
                        { id: 'source', header: 'Event source ARN', cell: (i: any) => i.EventSourceArn || i.eventSourceArn || '-' },
                        { id: 'state', header: 'State', cell: (i: any) => i.State ? <StatusIndicator type={i.State === 'Enabled' ? 'success' : i.State === 'Disabled' ? 'stopped' : 'in-progress'}>{i.State}</StatusIndicator> : '-', width: 130 },
                        { id: 'batch', header: 'Batch size', cell: (i: any) => i.BatchSize ?? '-', width: 110 },
                        { id: 'result', header: 'Last processing result', cell: (i: any) => i.LastProcessingResult || '-', width: 190 },
                        { id: 'uuid', header: 'UUID', cell: (i: any) => i.UUID || '-' },
                        {
                          id: 'action',
                          header: 'Actions',
                          cell: (i: any) => (
                            <Button variant="inline-link" onClick={() => handleDeleteTrigger(i.UUID || i.uuid)}>
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
                          <Button variant="primary" onClick={() => setPublishVersionOpen(true)}>
                            Publish new version
                          </Button>
                        }
                        description="$LATEST is the unpublished, editable version. Published versions are immutable snapshots."
                      >
                        Versions
                      </Header>
                    }
                  >
                    <Table
                      columnDefinitions={[
                        { id: 'version', header: 'Version', cell: (i: any) => i.Version || i.version, width: 120 },
                        { id: 'aliases', header: 'Aliases', cell: (i: any) => aliases.filter(alias => alias.FunctionVersion === i.Version).map(alias => alias.Name).join(', ') || '-' },
                        { id: 'desc', header: 'Version description', cell: (i: any) => i.Description || i.description || '-' },
                        { id: 'modified', header: 'Last modified', cell: (i: any) => formatAwsDate(i.LastModified), width: 280 },
                      ]}
                      items={versions}
                      trackBy={(i: any) => i.Version}
                      empty={<Box textAlign="center">No versions returned by ListVersionsByFunction.</Box>}
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
        header="Create function"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>Cancel</Button>
              <Button variant="primary" loading={creating} disabled={!functionName.trim() || !roleArn.trim()} onClick={handleCreateFunction}>Create function</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Box variant="small">Author from scratch: starts with a simple Hello World example.</Box>
          <FormField label="Function name" constraintText="Function name must be 1 to 64 characters, must be unique to the Region, and can't include spaces. Valid characters are a-z, A-Z, 0-9, hyphens (-), and underscores (_).">
            <Input value={functionName} onChange={({ detail }) => setFunctionName(detail.value)} placeholder="myFunctionName" />
          </FormField>
          <FormField label="Runtime">
            <Select
              selectedOption={runtime}
              onChange={({ detail }) => setRuntime(detail.selectedOption as any)}
              options={[
                { label: 'Python 3.13', value: 'python3.13' },
                { label: 'Python 3.12', value: 'python3.12' },
                { label: 'Node.js 22.x', value: 'nodejs22.x' },
                { label: 'Node.js 20.x', value: 'nodejs20.x' },
              ]}
            />
          </FormField>
          <FormField label="Architecture">
            <Select
              selectedOption={{ label: architecture, value: architecture }}
              onChange={({ detail }) => setArchitecture(detail.selectedOption.value || 'x86_64')}
              options={[{ label: 'x86_64', value: 'x86_64' }, { label: 'arm64', value: 'arm64' }]}
            />
          </FormField>
          <FormField label="Execution role" description="Roles whose trust policy allows lambda.amazonaws.com are suggested. The role grants the function its permissions.">
            <Autosuggest
              value={roleArn}
              onChange={({ detail }) => setRoleArn(detail.value)}
              options={lambdaRoles.map(role => ({ value: role.arn, label: role.name, description: role.arn }))}
              placeholder="arn:aws:iam::000000000000:role/my-lambda-role"
              enteredTextLabel={value => `Use: "${value}"`}
              empty="No roles trust lambda.amazonaws.com. Create one in IAM."
            />
          </FormField>
          <FormField label={`Code (${starterFor(runtime.value).fileName})`} description={`Handler: ${starterFor(runtime.value).handler}`}>
            <Textarea rows={8} readOnly value={starterFor(runtime.value).source} />
          </FormField>
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={deleteOpen}
        header="Delete function"
        confirmLabel="Delete"
        confirmationText="confirm"
        loading={deleting}
        onConfirm={handleDeleteFunction}
        onDismiss={() => setDeleteOpen(false)}
      >
        Deleting {activeFunction?.FunctionName} permanently removes its code, configuration, versions and aliases. Event source mappings and the CloudWatch log group are not deleted.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={Boolean(aliasToDelete)}
        header="Delete alias"
        confirmLabel="Delete"
        onConfirm={handleDeleteAlias}
        onDismiss={() => setAliasToDelete(null)}
      >
        Delete alias {aliasToDelete}? Callers that invoke the function through this alias ARN will fail.
      </ConfirmActionModal>

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
          {actionMessage && <Alert type={actionMessage.type}>{actionMessage.text}</Alert>}
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
          <FormField label="Event source ARN" description="An SQS queue, DynamoDB stream or Kinesis stream ARN. Streams start at LATEST.">
            <Input
              value={triggerSourceArn}
              onChange={({ detail }) => setTriggerSourceArn(detail.value)}
              placeholder="arn:aws:sqs:us-east-1:000000000000:my-queue"
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
          <FormField label="Version">
            <Select
              selectedOption={{ value: aliasVersion, label: aliasVersion }}
              onChange={({ detail }) => setAliasVersion(detail.selectedOption.value || '$LATEST')}
              options={(versions.length ? versions : [{ Version: '$LATEST' }]).map((version: any) => ({ value: version.Version, label: version.Version, description: version.Description || undefined }))}
            />
          </FormField>
          <FormField label="Description (Optional)">
            <Input value={aliasDesc} onChange={({ detail }) => setAliasDesc(detail.value)} placeholder="Production deployment alias" />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
