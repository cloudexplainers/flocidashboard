import { ApiRequestFlow } from '../components/ApiRequestFlow';
import React, { useEffect, useRef, useState } from 'react';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
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
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import { createApiGatewayApi, deleteApiGatewayApi, testApiGatewayRequest } from '../api/client';
import { CodeSnippet } from '../components/CodeSnippet';

type Api = Record<string, any> & { id: string; name: string; api_type: string };
const rows = (value: unknown): any[] => Array.isArray(value) ? value : [];

export const ApiGatewayConsole: React.FC<{ activeTab?: string; onTabChange?: (tabId: string) => void; resourcePath?: string; onNavigate?: (path: string) => void }> = ({ activeTab, onTabChange, resourcePath = 'apigateway', onNavigate }) => {
  const [apis, setApis] = useState<Api[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [filter, setFilter] = useState('');
  const [tab, setTab] = useState(activeTab || 'resources');
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [apiType, setApiType] = useState('HTTP');
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState('');
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('/');
  const [headers, setHeaders] = useState('{}');
  const [body, setBody] = useState('');
  const [result, setResult] = useState<any>(null);
  const queryApi = new URLSearchParams(resourcePath.split('?')[1] || '').get('api');
  const effectiveApiId = queryApi || selectedId;
  const api = apis.find(item => item.id === effectiveApiId);
  const selectApi = (id: string) => { setSelectedId(id); onNavigate?.(`apigateway/${tab}?${new URLSearchParams({ api: id })}`); };
  const stages = rows(api?.stages);
  const stageName = (item: any) => item.StageName || item.stageName || item.name;
  const selectedStage = stages.some(item => stageName(item) === stage) ? stage : (stages.length ? stageName(stages[0]) : '');
  const http = api?.api_type === 'http';
  const currentApiId = useRef(effectiveApiId);
  currentApiId.current = effectiveApiId;

  useEffect(() => { if (activeTab) setTab(activeTab); }, [activeTab]);
  const load = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/apigateway/');
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error || 'Unable to load APIs');
      const normalize = (item: any, type?: string): Api => ({ ...item,
        id: item.id || item.ApiId || item.Id,
        name: item.name || item.Name,
        api_type: type || String(item.protocol_type || item.ProtocolType || 'HTTP').toLowerCase(),
      });
      const next: Api[] = [...rows(data.rest_apis).map(item => normalize(item, 'rest')), ...rows(data.http_apis).map(item => normalize(item, 'http')),
        ...rows(data.apis || data.items).map(item => normalize(item))];
      setApis(next);
      setSelectedId(previous => previous ? (next.some(item => item.id === previous) ? previous : '') : next[0]?.id || '');
    } catch (error: any) {
      setNotice({ type: 'error', text: error.message }); setApis([]); setSelectedId('');
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);
  useEffect(() => { setStage(''); setResult(null); setPath('/'); setDeleteOpen(false); }, [effectiveApiId]);
  const run = async (task: () => Promise<void>) => {
    setBusy(true); setNotice(null);
    try { await task(); } catch (error: any) { setNotice({ type: 'error', text: error.message }); }
    finally { setBusy(false); }
  };
  const showTab = (id: string) => { setTab(id); if (api && onNavigate) onNavigate(`apigateway/${id}?${new URLSearchParams({ api: api.id })}`); else onTabChange?.(id); };
  const configured = (value: unknown, label: string) => {
    const error = typeof value === 'string' ? value : value && typeof value === 'object' && 'error' in value ? String(value.error) : '';
    return error ? <Alert type="warning">{label} could not be retrieved: {error}</Alert> : null;
  };
  const empty = (text: string) => <Box textAlign="center" padding="m">{text}</Box>;

  return <SpaceBetween size="l">
    {notice && <Alert type={notice.type} dismissible onDismiss={() => setNotice(null)}>{notice.text}</Alert>}
    <Table header={<Header variant="h1" counter={`(${apis.length})`} description="REST and HTTP APIs in the active account and Region."
      actions={<SpaceBetween direction="horizontal" size="xs">
        <Button iconName="refresh" loading={loading} onClick={load}>Refresh</Button>
        <Button disabled={!api} onClick={() => setDeleteOpen(true)}>Delete API</Button>
        <Button variant="primary" onClick={() => setCreateOpen(true)}>Create API</Button>
      </SpaceBetween>}>Amazon API Gateway</Header>}
      filter={<TextFilter filteringText={filter} filteringPlaceholder="Find APIs by name, ID, or protocol" onChange={({ detail }) => { setFilter(detail.filteringText); setSelectedId(''); }} />}
      items={apis.filter(item => `${item.name} ${item.id} ${item.api_type}`.toLowerCase().includes(filter.toLowerCase()))}
      loading={loading} loadingText="Loading APIs" selectionType="single" trackBy="id" selectedItems={api ? [api] : []}
      onSelectionChange={({ detail }) => selectApi(detail.selectedItems[0]?.id || '')}
      columnDefinitions={[
        { id: 'name', header: 'API name', cell: item => <Button variant="inline-link" onClick={() => selectApi(item.id)}>{item.name}</Button> },
        { id: 'id', header: 'API ID', cell: item => item.id },
        { id: 'api_type', header: 'Protocol', cell: item => item.api_type.toUpperCase() },
        { id: 'stage_count', header: 'Stages', cell: item => Array.isArray(item.stages) ? item.stages.length : 'Unavailable' },
      ]} empty={empty(filter ? 'No APIs match your filter.' : 'No APIs found. Create an API to get started.')} />
    {queryApi && !loading && !api && <Alert type="warning" header="API not found">The selected API is not reported in the current account and Region.</Alert>}
    {api && <Container header={<Header variant="h2" description={`${api.api_type.toUpperCase()} API · ${api.id}`}>{api.name}</Header>}>
      <SpaceBetween size="m">
        <KeyValuePairs columns={3} items={[
          { label: 'API ID', value: api.id }, { label: 'Protocol', value: api.api_type.toUpperCase() },
          { label: 'Deployed stages', value: stages.map(stageName).join(', ') || 'No deployed stages reported' },
          { label: 'Endpoint', value: api.api_endpoint || api.ApiEndpoint || api.execute_url_pattern || 'Not reported' },
        ]} />
        <ApiRequestFlow key={api.id} api={api} onTest={(verb, requestPath, selectedStage) => { setMethod(verb); setPath(requestPath); setStage(selectedStage); showTab('test'); }} />
        <Tabs activeTabId={tab} onChange={({ detail }) => showTab(detail.activeTabId)} tabs={[
          { id: 'resources', label: http ? 'Routes' : 'Resources and methods', content: <SpaceBetween size="m">
            {configured(http ? api.routes : api.resources, http ? 'Routes' : 'Resources')}
            {!http && <ExpandableSection defaultExpanded headerText="Resource and method tree">
              <ul aria-label="API resource paths" style={{ listStyle: 'none', padding: 0 }}>{rows(api.resources).slice().sort((a, b) => String(a.path || '/').localeCompare(String(b.path || '/'))).map(resource => <li key={resource.id} style={{ padding: '8px', paddingInlineStart: `${Math.max(0, String(resource.path || '/').split('/').filter(Boolean).length - 1) * 20 + 8}px` }}>
                <strong>{resource.path || '/'}</strong>{Object.keys(resource.resourceMethods || {}).map(verb => <Button key={verb} variant="inline-link" onClick={() => { setMethod(verb === 'ANY' ? 'GET' : verb); setPath(resource.path || '/'); showTab('test'); }}>{verb}</Button>)}
                {!Object.keys(resource.resourceMethods || {}).length && <span> · No methods</span>}
              </li>)}</ul>
            </ExpandableSection>}
            <Table items={rows(http ? api.routes : api.resources)} columnDefinitions={http ? [
              { id: 'RouteKey', header: 'Route', cell: (item: any) => <Button variant="inline-link" onClick={() => {
                const [verb, ...parts] = (item.RouteKey || '').split(' '); setMethod(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].includes(verb) ? verb : 'GET'); setPath(parts.join(' ') || '/'); showTab('test');
              }}>{item.RouteKey}</Button> },
              { id: 'RouteId', header: 'Route ID', cell: (item: any) => item.RouteId },
              { id: 'Target', header: 'Integration target', cell: (item: any) => item.Target || 'Not configured' },
              { id: 'AuthorizationType', header: 'Authorization', cell: (item: any) => item.AuthorizationType || 'Not reported' },
            ] : [
              { id: 'path', header: 'Resource path', cell: (item: any) => item.path || '/' },
              { id: 'id', header: 'Resource ID', cell: (item: any) => item.id },
              { id: 'methods', header: 'HTTP methods', cell: (item: any) => Object.keys(item.resourceMethods || {}).join(', ') || 'None' },
            ]} empty={empty(http ? 'No routes configured.' : 'No resources configured.')} />
          </SpaceBetween> },
          { id: 'integrations', label: 'Integrations', content: <SpaceBetween size="m">
            {configured(api.integrations, 'Integrations')}
            <Table items={http ? rows(api.integrations) : rows(api.resources).flatMap(resource => Object.entries(resource.resourceMethods || {}).flatMap(([verb, method]: [string, any]) => method.methodIntegration ? [{ ...method.methodIntegration, resource: `${verb} ${resource.path}` }] : []))}
              columnDefinitions={[
                { id: 'id', header: http ? 'Integration ID' : 'Resource method', cell: (item: any) => item.IntegrationId || item.resource },
                { id: 'type', header: 'Type', cell: (item: any) => item.IntegrationType || item.type || 'Not reported' },
                { id: 'uri', header: 'URI', cell: (item: any) => item.IntegrationUri || item.uri || 'Not reported' },
              ]} empty={empty('No integrations reported.')} />
          </SpaceBetween> },
          { id: 'stages', label: `Stages (${stages.length})`, content: <SpaceBetween size="m">
            {configured(api.stages, 'Stages')}
            <Table items={stages} columnDefinitions={[
              { id: 'name', header: 'Stage name', cell: (item: any) => stageName(item) },
              { id: 'deployment', header: 'Deployment ID', cell: (item: any) => item.DeploymentId || item.deploymentId || 'Not reported' },
              { id: 'auto', header: 'Automatic deployment', cell: (item: any) => item.AutoDeploy === undefined ? 'Not reported' : item.AutoDeploy ? 'Enabled' : 'Disabled' },
            ]} empty={empty('No stages deployed.')} />
          </SpaceBetween> },
          { id: 'authorizers', label: 'Authorizers', content: <SpaceBetween size="m">
            {configured(api.authorizers, 'Authorizers')}
            <Table items={rows(api.authorizers)} columnDefinitions={[
              { id: 'name', header: 'Name', cell: (item: any) => item.Name || item.name },
              { id: 'id', header: 'Authorizer ID', cell: (item: any) => item.AuthorizerId || item.id },
              { id: 'type', header: 'Type', cell: (item: any) => item.AuthorizerType || item.type },
            ]} empty={empty('No authorizers configured.')} />
          </SpaceBetween> },
          { id: 'test', label: 'Test request', content: <SpaceBetween size="m">
            <FormField label="Stage"><Select ariaLabel="Request stage" selectedOption={selectedStage ? { value: selectedStage, label: selectedStage } : null} placeholder="Select a deployed stage"
              options={stages.map(item => ({ value: stageName(item), label: stageName(item) }))} onChange={({ detail }) => setStage(detail.selectedOption.value!)} /></FormField>
            <FormField label="Method"><Select ariaLabel="Request method" selectedOption={{ value: method, label: method }} options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map(value => ({ value, label: value }))} onChange={({ detail }) => setMethod(detail.selectedOption.value!)} /></FormField>
            <FormField label="Request path"><Input value={path} onChange={({ detail }) => setPath(detail.value)} /></FormField>
            <FormField label="Request headers (JSON)"><Textarea value={headers} onChange={({ detail }) => setHeaders(detail.value)} rows={3} /></FormField>
            <FormField label="Request body"><Textarea value={body} onChange={({ detail }) => setBody(detail.value)} rows={4} /></FormField>
            {!selectedStage && <Alert type="info">Deploy a stage before sending a test request.</Alert>}
            <Button variant="primary" disabled={!selectedStage || !path.trim()} loading={busy} onClick={() => { setResult(null); run(async () => {
              const parsedHeaders = JSON.parse(headers);
              if (!parsedHeaders || Array.isArray(parsedHeaders) || typeof parsedHeaders !== 'object') throw new Error('Request headers must be a JSON object');
              const response = await testApiGatewayRequest(api.id, path, method, parsedHeaders, body || null, api.api_type, selectedStage);
              if (currentApiId.current === api.id) setResult(response);
            }); }}>Send request</Button>
            {result && <Container header={<Header variant="h3">Response · HTTP {result.status_code}</Header>}><CodeSnippet language="json" code={JSON.stringify(result, null, 2)} /></Container>}
          </SpaceBetween> },
        ]} />
      </SpaceBetween>
    </Container>}
    <Modal visible={createOpen} onDismiss={() => setCreateOpen(false)} header="Create API" footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
      <Button onClick={() => setCreateOpen(false)}>Cancel</Button><Button variant="primary" disabled={!name.trim()} loading={busy} onClick={() => run(async () => {
        await createApiGatewayApi(name.trim(), apiType, description); setCreateOpen(false); setName(''); setDescription(''); await load(); setNotice({ type: 'success', text: `API "${name.trim()}" created.` });
      })}>Create API</Button></SpaceBetween></Box>}>
      <SpaceBetween size="m">
        {notice?.type === 'error' && <Alert type="error">{notice.text}</Alert>}
        <FormField label="API name"><Input value={name} onChange={({ detail }) => setName(detail.value)} /></FormField>
        <FormField label="API type"><Select ariaLabel="API type" selectedOption={{ value: apiType, label: `${apiType} API` }} options={['HTTP', 'REST'].map(value => ({ value, label: `${value} API` }))} onChange={({ detail }) => setApiType(detail.selectedOption.value!)} /></FormField>
        <FormField label="Description"><Input value={description} onChange={({ detail }) => setDescription(detail.value)} /></FormField>
      </SpaceBetween>
    </Modal>
    <Modal visible={deleteOpen} onDismiss={() => setDeleteOpen(false)} header={`Delete API ${api?.name || ''}`} footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
      <Button onClick={() => setDeleteOpen(false)}>Cancel</Button><Button variant="primary" loading={busy} disabled={!api} onClick={() => run(async () => {
        if (!api) return; await deleteApiGatewayApi(api.id, api.api_type); setDeleteOpen(false); await load(); setNotice({ type: 'success', text: `API "${api.name}" deleted.` });
      })}>Delete</Button></SpaceBetween></Box>}>Delete {api?.name} ({api?.id}) and its routes, integrations, stages, and deployments?</Modal>
  </SpaceBetween>;
};
