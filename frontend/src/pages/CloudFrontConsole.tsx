import { DistributionBehaviors } from '../components/DistributionBehaviors';
import { ResourceValue } from '../components/ResourceDetails';
import React, { useState, useEffect, useMemo, useRef } from 'react';
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
  createCloudFrontDistribution,
  updateCloudFrontDistribution,
  deleteCloudFrontDistribution,
  createCloudFrontInvalidation,
  createCloudFrontCachePolicy,
  createCloudFrontFunction,
  deleteCloudFrontFunction,
  publishCloudFrontFunction,
} from '../api/client';

interface CloudFrontConsoleProps {
  activeTab?: string;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
  onTabChange?: (tabId: string) => void;
}

export const CloudFrontConsole: React.FC<CloudFrontConsoleProps> = ({ activeTab, onTabChange, resourcePath = 'cloudfront', onNavigate }) => {
  const query = new URLSearchParams(resourcePath.split('?')[1] || '');
  const pinned = query.get('distribution');
  const section = activeTab || resourcePath.split('?')[0].split('/')[1] || 'distributions';
  const latestDistribution = useRef(pinned); latestDistribution.current = pinned;
  const [confirmation, setConfirmation] = useState<{ title: string; description: string; action: () => Promise<void> } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [loadError, setLoadError] = useState('');
  const showTab = (tab: string, values?: Record<string, string>) => {
    const next = new URLSearchParams(resourcePath.split('?')[1] || '');
    Object.entries(values || {}).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    if (onNavigate) onNavigate(`cloudfront/${tab}${next.size ? '?' + next : ''}`); else onTabChange?.(tab);
  };
  const [data, setData] = useState<any>({
    distributions: [],
    invalidations: [],
    cache_policies: [],
    functions: [],
    origin_access_identities: [],
  });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Selected Distribution
  const [selectedDists, setSelectedDists] = useState<any[]>([]);

  // Create Distribution Modal
  const [createDistOpen, setCreateDistOpen] = useState(false);
  const [originDomain, setOriginDomain] = useState('');
  const [originId, setOriginId] = useState('');
  const [distComment, setDistComment] = useState('');
  const [viewerProtocolPolicy, setViewerProtocolPolicy] = useState({ label: 'Redirect HTTP to HTTPS', value: 'redirect-to-https' });
  const [originType, setOriginType] = useState('s3');
  const [creatingDist, setCreatingDist] = useState(false);

  // Create Invalidation Modal
  const [createInvOpen, setCreateInvOpen] = useState(false);
  const [invPaths, setInvPaths] = useState('');
  const [creatingInv, setCreatingInv] = useState(false);

  // Create Cache Policy Modal
  const [createPolicyOpen, setCreatePolicyOpen] = useState(false);
  const [policyName, setPolicyName] = useState('');
  const [defaultTtl, setDefaultTtl] = useState('86400');
  const [maxTtl, setMaxTtl] = useState('31536000');
  const [minTtl, setMinTtl] = useState('0');
  const [creatingPolicy, setCreatingPolicy] = useState(false);

  // Create Function Modal
  const [createFuncOpen, setCreateFuncOpen] = useState(false);
  const [funcName, setFuncName] = useState('');
  const [funcCode, setFuncCode] = useState(
    'function handler(event) {\n    var request = event.request;\n    var uri = request.uri;\n    if (uri.endsWith("/")) {\n        request.uri += "index.html";\n    }\n    return request;\n}'
  );
  const [creatingFunc, setCreatingFunc] = useState(false);

  const loadData = async () => {
    setLoading(true); setLoadError('');
    try {
      const response = await fetch('/api/cloudfront/');
      const payload = await response.json();
      if (!response.ok || payload.error) throw new Error(payload.error || 'Inventory unavailable');
      const res = payload;
      setData(
        res || {
          distributions: [],
          invalidations: [],
          cache_policies: [],
          functions: [],
          origin_access_identities: [],
        }
      );
      setSelectedDists(previous => (res?.distributions || []).filter((distribution: any) => previous.some(item => (item.Id || item.id) === (distribution.Id || distribution.id))));
    } catch (err: any) {
      setLoadError(err.message || 'Inventory unavailable');
      setData({});
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeDist = (data.distributions || []).find((item: any) => (item.Id || item.id) === pinned) || null;
  const activeDistId = activeDist ? activeDist.Id || activeDist.id : '';

  const filteredDists = useMemo(() => {
    const list = data.distributions || [];
    if (!filterText) return list;
    return list.filter((d: any) =>
      (d.DomainName || d.domain_name || d.Id || d.id || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [data.distributions, filterText]);

  const [invalidations, setInvalidations] = useState<any[]>([]);
  const [invalidationError, setInvalidationError] = useState('');
  const [invalidationDetail, setInvalidationDetail] = useState<any>(null);
  const [invalidationRevision, setInvalidationRevision] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setInvalidations([]); setInvalidationDetail(null); setInvalidationError('');
    if (activeDistId) fetch(`/api/cloudfront/distributions/${encodeURIComponent(activeDistId)}/invalidations/`)
      .then(async response => { const result = await response.json(); if (!response.ok || result.error) throw new Error(result.error || 'Invalidations unavailable'); return result; })
      .then(result => { if (!cancelled) setInvalidations(result.invalidations || []); })
      .catch(error => { if (!cancelled) setInvalidationError(error.message); });
    return () => { cancelled = true; };
  }, [activeDistId, invalidationRevision]);
  const inspectInvalidation = async (id: string) => {
    const distribution = activeDistId;
    setInvalidationDetail(null); setInvalidationError('');
    try {
      const response = await fetch(`/api/cloudfront/distributions/${encodeURIComponent(activeDistId)}/invalidations/${encodeURIComponent(id)}/`);
      const result = await response.json();
      if (!response.ok || result.error) throw new Error(result.error || 'Invalidation unavailable');
      if (latestDistribution.current === distribution) setInvalidationDetail(result.invalidation);
    } catch (error: any) { if (latestDistribution.current === distribution) setInvalidationError(error.message); }
  };

  // Actions
  const handleCreateDistribution = async () => {
    if (!originDomain.trim()) return;
    setCreatingDist(true);
    try {
      await createCloudFrontDistribution({
        origin_domain_name: originDomain.trim(),
        origin_type: originType,
        origin_id: originId.trim() || undefined,
        comment: distComment.trim() || undefined,
        viewer_protocol_policy: viewerProtocolPolicy.value,
        enabled: true,
      });
      setActionMessage({ type: 'success', text: 'Distribution creation accepted. Check the reported deployment status.' });
      setCreateDistOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create distribution' });
    } finally {
      setCreatingDist(false);
    }
  };

  const handleToggleDistEnabled = async (distId: string, currentEnabled: boolean) => {
    try {
      await updateCloudFrontDistribution(distId, { enabled: !currentEnabled });
      setActionMessage({ type: 'success', text: `Distribution ${!currentEnabled ? 'enabled' : 'disabled'}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update distribution' });
    }
  };

  const handleDeleteDistribution = async (distId: string) => {
    try {
      await deleteCloudFrontDistribution(distId);
      setActionMessage({ type: 'success', text: `Distribution "${distId}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete distribution' });
    }
  };

  const handleCreateInvalidation = async () => {
    if (!activeDistId || !invPaths.trim()) return;
    setCreatingInv(true);
    try {
      const paths = invPaths
        .split('\n')
        .map((p) => p.trim())
        .filter(Boolean);
      if (paths.some(path => !path.startsWith('/'))) throw new Error('Each object path must begin with /.');
      await createCloudFrontInvalidation(activeDistId, paths);
      setInvalidationRevision(value => value + 1);
      setActionMessage({ type: 'success', text: `Invalidation created for ${paths.length} path(s).` });
      setCreateInvOpen(false);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create invalidation' });
    } finally {
      setCreatingInv(false);
    }
  };

  const handleCreateCachePolicy = async () => {
    if (!policyName.trim()) return;
    const ttls = [minTtl, defaultTtl, maxTtl].map(value => value.trim() === '' ? NaN : Number(value));
    if (ttls.some(value => !Number.isSafeInteger(value) || value < 0) || ttls[0] > ttls[1] || ttls[1] > ttls[2]) { setActionMessage({ type: 'error', text: 'TTLs must be whole seconds with 0 ≤ minimum ≤ default ≤ maximum.' }); return; }
    setCreatingPolicy(true);
    try {
      await createCloudFrontCachePolicy({
        name: policyName.trim(),
        default_ttl: Number(defaultTtl),
        max_ttl: Number(maxTtl),
        min_ttl: Number(minTtl),
      });
      setActionMessage({ type: 'success', text: `Cache policy "${policyName.trim()}" created.` });
      setCreatePolicyOpen(false);
      setPolicyName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create cache policy' });
    } finally {
      setCreatingPolicy(false);
    }
  };

  const handleCreateFunction = async () => {
    if (!funcName.trim()) return;
    setCreatingFunc(true);
    try {
      await createCloudFrontFunction(funcName.trim(), funcCode.trim());
      setActionMessage({ type: 'success', text: `CloudFront Function "${funcName.trim()}" created.` });
      setCreateFuncOpen(false);
      setFuncName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create function' });
    } finally {
      setCreatingFunc(false);
    }
  };

  const handleDeleteFunction = async (name: string) => {
    try {
      await deleteCloudFrontFunction(name);
      setActionMessage({ type: 'success', text: `Function "${name}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete function' });
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Amazon CloudFront is a fast content delivery network (CDN) service that securely delivers data, videos, and APIs."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={() => { loadData(); setInvalidationRevision(value => value + 1); }}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateDistOpen(true)}>
              Create Distribution
            </Button>
          </SpaceBetween>
        }
      >
        {pinned ? `Distribution: ${pinned}` : 'Amazon CloudFront'}
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {loadError && <Alert type="error">{loadError}</Alert>}
      {/* Inventory totals */}
      {!pinned && <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
        <Container>
          <Box variant="awsui-key-label">Distributions</Box>
          <Box variant="awsui-value-large">{(data.distributions || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Origin access identities</Box>
          <Box variant="awsui-value-large">{(data.origin_access_identities || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Cache Policies</Box>
          <Box variant="awsui-value-large">{(data.cache_policies || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">CloudFront Functions</Box>
          <Box variant="awsui-value-large">{(data.functions || []).length}</Box>
        </Container>
      </Grid>}

      <FormField label="Distribution context"><Select ariaLabel="Distribution context" placeholder="Select a distribution" selectedOption={activeDistId ? { label: activeDistId, value: activeDistId } : null} options={(data.distributions || []).map((distribution: any) => ({ label: distribution.Id || distribution.id, value: distribution.Id || distribution.id, description: distribution.DomainName || distribution.domain_name }))} onChange={({ detail }) => showTab(activeTab || 'origins', { distribution: detail.selectedOption.value! })} /></FormField>
      {pinned && !loading && !loadError && !activeDist && <Alert type="warning">Distribution not found in the active account and Region.</Alert>}
      {activeDist && <Container header={<Header variant="h2" description={activeDist.DomainName || 'Domain not reported'} actions={<Button onClick={() => showTab('origins')}>View origins and behaviors</Button>}>Distribution overview</Header>}>
        <SpaceBetween size="m"><Grid gridDefinition={Array.from({ length: 6 }, () => ({ colspan: { default: 12, s: 4 } }))}>{[['Deployment status', activeDist.Status], ['Enabled', activeDist.Enabled == null ? null : activeDist.Enabled ? 'Yes' : 'No'], ['Last modified', activeDist.LastModifiedTime], ['ARN', activeDist.ARN], ['Price class', activeDist.PriceClass], ['HTTP version', activeDist.HttpVersion]].map(([label, value]) => <div key={label}><Box variant="awsui-key-label">{label}</Box><Box>{value == null ? 'Not reported' : String(value)}</Box></div>)}</Grid><div className="resource-flow cloudfront-request-flow"><section><Header variant="h3">Viewer</Header><p>{activeDist.DomainName || 'Not reported'}</p></section><span aria-hidden="true">→</span><section><Header variant="h3">Default behavior</Header><p>Target origin: {activeDist.DefaultCacheBehavior?.TargetOriginId || 'Not reported'}</p></section><span aria-hidden="true">→</span><section><Header variant="h3">Origins</Header>{(activeDist.Origins?.Items || []).map((origin: any) => <p key={origin.Id}>{origin.Id}: {origin.DomainName}</p>)}{!activeDist.Origins?.Items?.length && <p>Not reported</p>}</section></div></SpaceBetween>
      </Container>}
      <Tabs
        activeTabId={section}
        onChange={({ detail }) => showTab(detail.activeTabId)}
        tabs={[
          {
            label: `Distributions (${(data.distributions || []).length})`,
            id: 'distributions',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateDistOpen(true)}>
                        Create Distribution
                      </Button>
                    }
                  >
                    CDN Distributions
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                    filteringPlaceholder="Filter distributions..."
                  />
                  <Table
                    columnDefinitions={[
                      {
                        id: 'id',
                        header: 'Distribution ID',
                        cell: (item: any) => (
                          <Button variant="inline-link" onClick={() => { setSelectedDists([item]); showTab('origins', { distribution: item.Id || item.id }); }}>
                            <strong>{item.Id || item.id}</strong>
                          </Button>
                        ),
                        width: 170,
                      },
                      {
                        id: 'domain',
                        header: 'Domain Name',
                        cell: (item: any) => {
                          const dom = item.DomainName || item.domain_name;
                          return dom ? <Link href={`https://${dom}`} external>{dom}</Link> : <span>Not reported</span>;
                        },
                      },
                      {
                        id: 'status',
                        header: 'Status',
                        cell: (item: any) => <ResourceValue name="status" value={item.Status} />,
                        width: 130,
                      },
                      {
                        id: 'enabled',
                        header: 'State',
                        cell: (item: any) => (
                          <Badge color={item.Enabled !== false ? 'green' : 'grey'}>
                            {item.Enabled == null ? 'Not reported' : item.Enabled ? 'Enabled' : 'Disabled'}
                          </Badge>
                        ),
                        width: 110,
                      },
                      {
                        id: 'origins',
                        header: 'Origins Count',
                        cell: (item: any) => item.Origins?.Quantity ?? item.Origins?.Items?.length ?? 'Not reported',
                        width: 130,
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              disabled={item.Enabled == null} onClick={() => handleToggleDistEnabled(item.Id || item.id, item.Enabled !== false)}
                            >
                              {item.Enabled !== false ? 'Disable' : 'Enable'}
                            </Button>
                            <Button
                              iconName="remove"
                              disabled={item.Enabled !== false || item.Status !== 'Deployed'}
                              onClick={() => setConfirmation({ title: 'Delete distribution', description: `Delete ${item.Id || item.id}? The distribution must be disabled and deployed.`, action: () => handleDeleteDistribution(item.Id || item.id) })}
                            >
                              Delete
                            </Button>
                          </SpaceBetween>
                        ),
                        width: 190,
                      },
                    ]}
                    items={filteredDists}
                    selectionType="single"
                    selectedItems={selectedDists}
                    onSelectionChange={({ detail }) => { setSelectedDists(detail.selectedItems); showTab(activeTab || 'distributions', { distribution: detail.selectedItems[0]?.Id || detail.selectedItems[0]?.id || '' }); }}
                    empty={<Box textAlign="center">No CloudFront distributions found.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Invalidations (${invalidations.length})`,
            id: 'invalidations',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeDistId ? `Invalidations for "${activeDistId}"` : 'Select a distribution'}
                    actions={
                      <Button variant="primary" iconName="add-plus" disabled={!activeDistId} onClick={() => setCreateInvOpen(true)}>
                        Create Invalidation
                      </Button>
                    }
                  >
                    Cache Invalidations
                  </Header>
                }
              >
                <SpaceBetween size="m">
                {invalidationError && <Alert type="error">{invalidationError}</Alert>}
                {invalidationDetail && <Container header={<Header variant="h3">Invalidation: {invalidationDetail.Id}</Header>}><SpaceBetween size="s"><Box>Status: {invalidationDetail.Status || 'Not reported'}</Box><Box>Caller reference: {invalidationDetail.InvalidationBatch?.CallerReference || 'Not reported'}</Box>{(invalidationDetail.InvalidationBatch?.Paths?.Items || []).map((path: string) => <Box key={path}><code>{path}</code></Box>)}</SpaceBetween></Container>}
                <Table
                  columnDefinitions={[
                    {
                      id: 'id',
                      header: 'Invalidation ID',
                      cell: (item: any) => <Button variant="inline-link" onClick={() => inspectInvalidation(item.Id || item.id)}>{item.Id || item.id}</Button>,
                      width: 220,
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      cell: (item: any) => <ResourceValue name="status" value={item.Status} />,
                      width: 140,
                    },
                    {
                      id: 'paths',
                      header: 'Object Paths',
                      cell: (item: any) => <code>{item.InvalidationBatch?.Paths?.Items?.join(', ') || 'Open invalidation for paths'}</code>,
                    },
                    {
                      id: 'date',
                      header: 'Created Time',
                      cell: (item: any) => item.CreateTime || 'Not reported',
                      width: 160,
                    },
                  ]}
                  items={invalidations}
                  empty={<Box textAlign="center">{activeDistId ? 'No invalidations reported for this distribution.' : 'Select a distribution to inspect its invalidations.'}</Box>}
                />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: 'Origins & Behaviors',
            id: 'origins',
            content: (
              <SpaceBetween size="m">
              {activeDist && <DistributionBehaviors key={activeDistId} distribution={activeDist} />}
              <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                <Container header={<Header variant="h3">Origins</Header>}>
                  <Table
                    columnDefinitions={[
                      { id: 'id', header: 'Origin ID', cell: (i: any) => <strong>{i.Id || 'Not reported'}</strong> },
                      { id: 'domain', header: 'Domain Name', cell: (i: any) => <code>{i.DomainName || 'Not reported'}</code> },
                      { id: 'path', header: 'Origin Path', cell: (i: any) => i.OriginPath || '/' },
                    ]}
                    items={activeDist?.Origins?.Items || []}
                  />
                </Container>

                <Container header={<Header variant="h3">Default Cache Behavior</Header>}>
                  <SpaceBetween size="s">
                    <Box><strong>Viewer Protocol:</strong> {activeDist?.DefaultCacheBehavior?.ViewerProtocolPolicy || 'Not reported'}</Box>
                    <Box><strong>Allowed HTTP Methods:</strong> {activeDist?.DefaultCacheBehavior?.AllowedMethods?.Items?.join(', ') || 'Not reported'}</Box>
                    <Box><strong>Cache Policy:</strong> {activeDist?.DefaultCacheBehavior?.CachePolicyId || 'Not reported'}</Box>
                    <Box><strong>Compress Objects Automatically:</strong> {activeDist?.DefaultCacheBehavior?.Compress == null ? 'Not reported' : activeDist.DefaultCacheBehavior.Compress ? 'Enabled' : 'Disabled'}</Box>
                  </SpaceBetween>
                </Container>
              </Grid>
              </SpaceBetween>
            ),
          },
          {
            label: `Cache Policies (${(data.cache_policies || []).length})`,
            id: 'cache-policies',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreatePolicyOpen(true)}>
                        Create Cache Policy
                      </Button>
                    }
                  >
                    Cache Policies
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'name', header: 'Policy Name', cell: (i: any) => <strong>{i.CachePolicyConfig?.Name || 'Not reported'}</strong> },
                    { id: 'minTtl', header: 'Min TTL', cell: (i: any) => i.CachePolicyConfig?.MinTTL == null ? 'Not reported' : `${i.CachePolicyConfig.MinTTL}s`, width: 120 },
                    { id: 'defaultTtl', header: 'Default TTL', cell: (i: any) => i.CachePolicyConfig?.DefaultTTL == null ? 'Not reported' : `${i.CachePolicyConfig.DefaultTTL}s`, width: 140 },
                    { id: 'maxTtl', header: 'Max TTL', cell: (i: any) => i.CachePolicyConfig?.MaxTTL == null ? 'Not reported' : `${i.CachePolicyConfig.MaxTTL}s`, width: 140 },
                  ]}
                  items={(data.cache_policies || []).map((item: any) => item.CachePolicy || item)}
                  empty={<Box textAlign="center">No cache policies reported.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Functions (${(data.functions || []).length})`,
            id: 'functions',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateFuncOpen(true)}>
                        Create Function
                      </Button>
                    }
                  >
                    CloudFront Functions
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'name', header: 'Function Name', cell: (i: any) => <strong>{i.Name || i.name}</strong> },
                    { id: 'runtime', header: 'Runtime', cell: (i: any) => <Badge color="blue">{i.FunctionConfig?.Runtime || 'Not reported'}</Badge>, width: 180 },
                    { id: 'stage', header: 'Stage', cell: (i: any) => i.FunctionMetadata?.Stage || 'Not reported' },
                    { id: 'status', header: 'Status', cell: (i: any) => <ResourceValue name="status" value={i.Status} />, width: 130 },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (i: any) => (
                        <SpaceBetween direction="horizontal" size="xs"><Button onClick={() => setConfirmation({ title: 'Publish function', description: `Publish ${i.Name || i.name} to LIVE? Publishing does not associate the function with a distribution.`, action: async () => { try { await publishCloudFrontFunction(i.Name || i.name); setActionMessage({ type: 'success', text: 'Function publication accepted. Associate its LIVE version with a behavior to serve viewer requests.' }); await loadData(); } catch (error: any) { setActionMessage({ type: 'error', text: error.message }); } } })}>Publish</Button><Button iconName="remove" onClick={() => setConfirmation({ title: 'Delete function', description: `Delete ${i.Name || i.name}?`, action: () => handleDeleteFunction(i.Name || i.name) })}>Delete</Button></SpaceBetween>
                      ),
                      width: 120,
                    },
                  ]}
                  items={data.functions || []}
                  empty={<Box textAlign="center">No CloudFront edge functions created.</Box>}
                />
              </Container>
            ),
          },
        ]}
      />

      <Modal visible={!!confirmation} header={confirmation?.title || ''} onDismiss={() => { if (!confirming) setConfirmation(null); }} footer={<SpaceBetween direction="horizontal" size="xs"><Button disabled={confirming} onClick={() => setConfirmation(null)}>Cancel</Button><Button variant="primary" loading={confirming} onClick={async () => { if (!confirmation || confirming) return; setConfirming(true); try { await confirmation.action(); setConfirmation(null); } finally { setConfirming(false); } }}>Confirm</Button></SpaceBetween>}>{confirmation?.description}</Modal>
      {/* Create Distribution Modal */}
      <Modal
        visible={createDistOpen}
        onDismiss={() => setCreateDistOpen(false)}
        header="Create CloudFront Distribution"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateDistOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingDist} onClick={handleCreateDistribution}>
                Create Distribution
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {actionMessage?.type === 'error' && <Alert type="error">{actionMessage.text}</Alert>}
          <FormField label="Origin type"><Select selectedOption={{ label: originType === 's3' ? 'Amazon S3' : 'Custom HTTP origin', value: originType }} options={[{ label: 'Amazon S3', value: 's3' }, { label: 'Custom HTTP origin', value: 'custom' }]} onChange={({ detail }) => setOriginType(detail.selectedOption.value!)} /></FormField>
          {originType === 'custom' && <Box>Origin connections use HTTPS on port 443.</Box>}
          <FormField label="Origin Domain Name" description="Hostname of the S3 bucket or custom web server, without a scheme, port, or path.">
            <Input value={originDomain} onChange={({ detail }) => setOriginDomain(detail.value)} placeholder="assets.example.com" />
          </FormField>
          <FormField label="Origin ID">
            <Input value={originId} onChange={({ detail }) => setOriginId(detail.value)} placeholder="s3-origin-1" />
          </FormField>
          <FormField label="Viewer Protocol Policy">
            <Select
              selectedOption={viewerProtocolPolicy}
              onChange={({ detail }) => setViewerProtocolPolicy(detail.selectedOption as any)}
              options={[
                { label: 'Redirect HTTP to HTTPS', value: 'redirect-to-https' },
                { label: 'HTTPS Only', value: 'https-only' },
                { label: 'HTTP and HTTPS', value: 'allow-all' },
              ]}
            />
          </FormField>
          <FormField label="Description / Comment">
            <Input value={distComment} onChange={({ detail }) => setDistComment(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Invalidation Modal */}
      <Modal
        visible={createInvOpen}
        onDismiss={() => setCreateInvOpen(false)}
        header={`Create Invalidation for ${activeDistId || 'distribution'}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateInvOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingInv} onClick={handleCreateInvalidation}>
                Create Invalidation
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">{actionMessage?.type === 'error' && <Alert type="error">{actionMessage.text}</Alert>}<FormField label="Object Paths (one per line)" description='Specify exact paths e.g. "/images/logo.png" or wildcards e.g. "/*"'>
          <Textarea rows={6} value={invPaths} onChange={({ detail }) => setInvPaths(detail.value)} />
        </FormField></SpaceBetween>
      </Modal>

      {/* Create Cache Policy Modal */}
      <Modal
        visible={createPolicyOpen}
        onDismiss={() => setCreatePolicyOpen(false)}
        header="Create Cache Policy"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreatePolicyOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingPolicy} onClick={handleCreateCachePolicy}>
                Create Policy
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {actionMessage?.type === 'error' && <Alert type="error">{actionMessage.text}</Alert>}
          <FormField label="Policy Name">
            <Input value={policyName} onChange={({ detail }) => setPolicyName(detail.value)} placeholder="CustomStaticAssetsPolicy" />
          </FormField>
          <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
            <FormField label="Min TTL (s)">
              <Input type="number" value={minTtl} onChange={({ detail }) => setMinTtl(detail.value)} />
            </FormField>
            <FormField label="Default TTL (s)">
              <Input type="number" value={defaultTtl} onChange={({ detail }) => setDefaultTtl(detail.value)} />
            </FormField>
            <FormField label="Max TTL (s)">
              <Input type="number" value={maxTtl} onChange={({ detail }) => setMaxTtl(detail.value)} />
            </FormField>
          </Grid>
        </SpaceBetween>
      </Modal>

      {/* Create CloudFront Function Modal */}
      <Modal
        visible={createFuncOpen}
        onDismiss={() => setCreateFuncOpen(false)}
        header="Create CloudFront Function"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateFuncOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingFunc} onClick={handleCreateFunction}>
                Create Function
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {actionMessage?.type === 'error' && <Alert type="error">{actionMessage.text}</Alert>}
          <FormField label="Function Name">
            <Input value={funcName} onChange={({ detail }) => setFuncName(detail.value)} placeholder="url-rewrite-function" />
          </FormField>
          <FormField label="JavaScript Code (runtime: cloudfront-js-2.0)">
            <Textarea rows={10} value={funcCode} onChange={({ detail }) => setFuncCode(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
