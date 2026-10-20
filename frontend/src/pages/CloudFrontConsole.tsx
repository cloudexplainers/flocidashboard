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
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Link from '@cloudscape-design/components/link';
import {
  fetchInventory,
  createCloudFrontDistribution,
  updateCloudFrontDistribution,
  deleteCloudFrontDistribution,
  createCloudFrontInvalidation,
  createCloudFrontCachePolicy,
  createCloudFrontFunction,
  deleteCloudFrontFunction,
} from '../api/client';

interface CloudFrontConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const CloudFrontConsole: React.FC<CloudFrontConsoleProps> = ({ activeTab, onTabChange }) => {
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
  const [originDomain, setOriginDomain] = useState('my-bucket.s3.localhost:4566');
  const [originId, setOriginId] = useState('s3-origin-1');
  const [distComment, setDistComment] = useState('Production CDN distribution');
  const [viewerProtocolPolicy, setViewerProtocolPolicy] = useState({ label: 'Redirect HTTP to HTTPS', value: 'redirect-to-https' });
  const [creatingDist, setCreatingDist] = useState(false);

  // Create Invalidation Modal
  const [createInvOpen, setCreateInvOpen] = useState(false);
  const [invPaths, setInvPaths] = useState('/*\n/index.html\n/static/*');
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
    setLoading(true);
    try {
      const res = await fetchInventory('cloudfront');
      setData(
        res || {
          distributions: [],
          invalidations: [],
          cache_policies: [],
          functions: [],
          origin_access_identities: [],
        }
      );
      if (res?.distributions?.length > 0 && selectedDists.length === 0) {
        setSelectedDists([res.distributions[0]]);
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

  const activeDist = selectedDists[0] || null;
  const activeDistId = activeDist ? activeDist.Id || activeDist.id : '';

  const filteredDists = useMemo(() => {
    const list = data.distributions || [];
    if (!filterText) return list;
    return list.filter((d: any) =>
      (d.DomainName || d.domain_name || d.Id || d.id || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [data.distributions, filterText]);

  // Actions
  const handleCreateDistribution = async () => {
    if (!originDomain.trim()) return;
    setCreatingDist(true);
    try {
      await createCloudFrontDistribution({
        origin_domain_name: originDomain.trim(),
        origin_id: originId.trim() || undefined,
        comment: distComment.trim() || undefined,
        viewer_protocol_policy: viewerProtocolPolicy.value,
        enabled: true,
      });
      setActionMessage({ type: 'success', text: 'CloudFront Distribution deployed successfully.' });
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
    if (!confirm(`Are you sure you want to delete distribution "${distId}"?`)) return;
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
      await createCloudFrontInvalidation(activeDistId, paths);
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
    setCreatingPolicy(true);
    try {
      await createCloudFrontCachePolicy({
        name: policyName.trim(),
        default_ttl: parseInt(defaultTtl, 10) || 86400,
        max_ttl: parseInt(maxTtl, 10) || 31536000,
        min_ttl: parseInt(minTtl, 10) || 0,
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
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateDistOpen(true)}>
              Create Distribution
            </Button>
          </SpaceBetween>
        }
      >
        Amazon CloudFront
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {/* Metrics */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
        <Container>
          <Box variant="awsui-key-label">Distributions</Box>
          <Box variant="awsui-value-large">{(data.distributions || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Invalidations</Box>
          <Box variant="awsui-value-large">{(data.invalidations || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Cache Policies</Box>
          <Box variant="awsui-value-large">{(data.cache_policies || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">CloudFront Functions</Box>
          <Box variant="awsui-value-large">{(data.functions || []).length}</Box>
        </Container>
      </Grid>

      <Tabs
        activeTabId={activeTab || 'distributions'}
        onChange={({ detail }) => onTabChange && onTabChange(detail.activeTabId)}
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
                          <Button variant="inline-link" onClick={() => setSelectedDists([item])}>
                            <strong>{item.Id || item.id}</strong>
                          </Button>
                        ),
                        width: 170,
                      },
                      {
                        id: 'domain',
                        header: 'Domain Name',
                        cell: (item: any) => {
                          const dom = item.DomainName || item.domain_name || `${item.Id || item.id}.cloudfront.net`;
                          return <Link href={`https://${dom}`} external>{dom}</Link>;
                        },
                      },
                      {
                        id: 'status',
                        header: 'Status',
                        cell: (item: any) => <StatusIndicator type="success">{item.Status || 'Deployed'}</StatusIndicator>,
                        width: 130,
                      },
                      {
                        id: 'enabled',
                        header: 'State',
                        cell: (item: any) => (
                          <Badge color={item.Enabled !== false ? 'green' : 'grey'}>
                            {item.Enabled !== false ? 'Enabled' : 'Disabled'}
                          </Badge>
                        ),
                        width: 110,
                      },
                      {
                        id: 'origins',
                        header: 'Origins Count',
                        cell: (item: any) => item.Origins?.Items?.length || 1,
                        width: 130,
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              onClick={() => handleToggleDistEnabled(item.Id || item.id, item.Enabled !== false)}
                            >
                              {item.Enabled !== false ? 'Disable' : 'Enable'}
                            </Button>
                            <Button
                              iconName="remove"
                              onClick={() => handleDeleteDistribution(item.Id || item.id)}
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
                    onSelectionChange={({ detail }) => setSelectedDists(detail.selectedItems)}
                    empty={<Box textAlign="center">No CloudFront distributions found.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Invalidations (${(data.invalidations || []).length})`,
            id: 'invalidations',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeDistId ? `Invalidations for "${activeDistId}"` : 'Select a distribution'}
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateInvOpen(true)}>
                        Create Invalidation
                      </Button>
                    }
                  >
                    Cache Invalidations
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'id',
                      header: 'Invalidation ID',
                      cell: (item: any) => <code>{item.Id || item.id}</code>,
                      width: 220,
                    },
                    {
                      id: 'status',
                      header: 'Status',
                      cell: (item: any) => <StatusIndicator type="success">{item.Status || 'Completed'}</StatusIndicator>,
                      width: 140,
                    },
                    {
                      id: 'paths',
                      header: 'Object Paths',
                      cell: (item: any) => <code>{item.Paths?.Items?.join(', ') || item.paths?.join(', ') || '/*'}</code>,
                    },
                    {
                      id: 'date',
                      header: 'Created Time',
                      cell: (item: any) => item.CreateTime || 'Just now',
                      width: 160,
                    },
                  ]}
                  items={data.invalidations || []}
                  empty={<Box textAlign="center">No invalidations on record. Click Create Invalidation to purge cache.</Box>}
                />
              </Container>
            ),
          },
          {
            label: 'Origins & Behaviors',
            id: 'origins',
            content: (
              <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                <Container header={<Header variant="h3">Origins</Header>}>
                  <Table
                    columnDefinitions={[
                      { id: 'id', header: 'Origin ID', cell: (i: any) => <strong>{i.Id || 'default-origin'}</strong> },
                      { id: 'domain', header: 'Domain Name', cell: (i: any) => <code>{i.DomainName || 's3.localhost:4566'}</code> },
                      { id: 'path', header: 'Origin Path', cell: (i: any) => i.OriginPath || '/' },
                    ]}
                    items={activeDist?.Origins?.Items || [{ Id: 's3-origin-1', DomainName: originDomain, OriginPath: '/' }]}
                  />
                </Container>

                <Container header={<Header variant="h3">Default Cache Behavior</Header>}>
                  <SpaceBetween size="s">
                    <Box><strong>Viewer Protocol:</strong> {activeDist?.DefaultCacheBehavior?.ViewerProtocolPolicy || 'redirect-to-https'}</Box>
                    <Box><strong>Allowed HTTP Methods:</strong> GET, HEAD, OPTIONS, PUT, POST, PATCH, DELETE</Box>
                    <Box><strong>Cache Policy:</strong> Managed-CachingOptimized (Default TTL: 86400s)</Box>
                    <Box><strong>Compress Objects Automatically:</strong> Enabled (Gzip/Brotli)</Box>
                  </SpaceBetween>
                </Container>
              </Grid>
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
                    { id: 'name', header: 'Policy Name', cell: (i: any) => <strong>{i.CachePolicyConfig?.Name || i.name || 'Managed-CachingOptimized'}</strong> },
                    { id: 'minTtl', header: 'Min TTL', cell: (i: any) => `${i.CachePolicyConfig?.MinTTL || 1}s`, width: 120 },
                    { id: 'defaultTtl', header: 'Default TTL', cell: (i: any) => `${i.CachePolicyConfig?.DefaultTTL || 86400}s`, width: 140 },
                    { id: 'maxTtl', header: 'Max TTL', cell: (i: any) => `${i.CachePolicyConfig?.MaxTTL || 31536000}s`, width: 140 },
                  ]}
                  items={
                    data.cache_policies?.length > 0
                      ? data.cache_policies
                      : [
                          { name: 'Managed-CachingOptimized', CachePolicyConfig: { MinTTL: 1, DefaultTTL: 86400, MaxTTL: 31536000 } },
                          { name: 'Managed-CachingDisabled', CachePolicyConfig: { MinTTL: 0, DefaultTTL: 0, MaxTTL: 0 } },
                        ]
                  }
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
                    { id: 'runtime', header: 'Runtime', cell: (i: any) => <Badge color="blue">{i.FunctionConfig?.Runtime || 'cloudfront-js-2.0'}</Badge>, width: 180 },
                    { id: 'status', header: 'Status', cell: (i: any) => <StatusIndicator type="success">{i.Status || 'LIVE'}</StatusIndicator>, width: 130 },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (i: any) => (
                        <Button iconName="remove" onClick={() => handleDeleteFunction(i.Name || i.name)}>
                          Delete
                        </Button>
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
          <FormField label="Origin Domain Name" description="DNS domain name of the S3 bucket or custom web server.">
            <Input value={originDomain} onChange={({ detail }) => setOriginDomain(detail.value)} placeholder="my-bucket.s3.localhost:4566" />
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
        <FormField label="Object Paths (one per line)" description='Specify exact paths e.g. "/images/logo.png" or wildcards e.g. "/*"'>
          <Textarea rows={6} value={invPaths} onChange={({ detail }) => setInvPaths(detail.value)} />
        </FormField>
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
