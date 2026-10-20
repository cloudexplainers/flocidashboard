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
import {
  fetchServiceInventory,
  putSsmParameter,
  getSsmParameterValue,
  deleteSsmParameter,
  fetchSsmParameterHistory,
  fetchSsmParameterTags,
  addSsmParameterTags,
  removeSsmParameterTags,
  fetchSsmDocuments,
  createSsmDocument,
  deleteSsmDocument,
} from '../api/client';

interface SSMConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const SSMConsole: React.FC<SSMConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({ parameters: [] });
  const [loading, setLoading] = useState(true);
  const [selectedParams, setSelectedParams] = useState<any[]>([]);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'parameters');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Create Parameter Modal
  const [createParamOpen, setCreateParamOpen] = useState(false);
  const [paramName, setParamName] = useState('');
  const [paramType, setParamType] = useState({ label: 'String', value: 'String' });
  const [paramValue, setParamValue] = useState('');
  const [paramDescription, setParamDescription] = useState('');
  const [creatingParam, setCreatingParam] = useState(false);

  // Decryption Revealer State
  const [revealedValue, setRevealedValue] = useState<string | null>(null);
  const [revealing, setRevealing] = useState(false);

  // Parameter History State
  const [paramHistory, setParamHistory] = useState<any[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // Tags State
  const [paramTags, setParamTags] = useState<any[]>([]);
  const [createTagOpen, setCreateTagOpen] = useState(false);
  const [tagKey, setTagKey] = useState('');
  const [tagValue, setTagValue] = useState('');
  const [savingTag, setSavingTag] = useState(false);

  // Documents State
  const [documents, setDocuments] = useState<any[]>([]);
  const [createDocOpen, setCreateDocOpen] = useState(false);
  const [docName, setDocName] = useState('');
  const [docType, setDocType] = useState({ label: 'Command (Automation script)', value: 'Command' });
  const [docContent, setDocContent] = useState('{\n  "schemaVersion": "2.2",\n  "description": "Custom SSM Command Document",\n  "mainSteps": []\n}');
  const [creatingDoc, setCreatingDoc] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const [res, docRes]: any[] = await Promise.all([
        fetchServiceInventory('ssm'),
        fetchSsmDocuments(),
      ]);
      const list = (res?.parameters || res?.Parameters || []).map((p: any) => ({
        ...p,
        Name: p.Name || p.name,
        Type: p.Type || p.type || 'String',
        Version: p.Version || p.version || 1,
        LastModifiedDate: p.LastModifiedDate || p.last_modified || new Date().toISOString().split('T')[0],
        Description: p.Description || p.description || '—',
        Value: p.Value || p.value,
      }));
      setData({ parameters: list });
      setDocuments(docRes?.DocumentIdentifiers || docRes?.documents || []);
      if (list.length > 0 && selectedParams.length === 0) {
        setSelectedParams([list[0]]);
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

  const activeParam = selectedParams[0] || null;

  const loadParamDetails = async (name: string) => {
    if (!name) return;
    setLoadingHistory(true);
    try {
      const [histRes, tagRes]: any[] = await Promise.all([
        fetchSsmParameterHistory(name),
        fetchSsmParameterTags(name),
      ]);
      setParamHistory(histRes?.Parameters || histRes?.history || []);
      setParamTags(tagRes?.TagList || tagRes?.tags || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoadingHistory(false);
    }
  };

  useEffect(() => {
    setRevealedValue(null);
    if (activeParam?.Name) {
      loadParamDetails(activeParam.Name);
    }
  }, [activeParam?.Name]);

  const handleCreateParam = async () => {
    if (!paramName.trim() || !paramValue.trim()) return;
    setCreatingParam(true);
    setActionMessage(null);
    try {
      await putSsmParameter(
        paramName.trim(),
        paramValue.trim(),
        paramType.value as any,
        paramDescription.trim()
      );
      setActionMessage({ type: 'success', text: `Parameter "${paramName.trim()}" created successfully.` });
      setCreateParamOpen(false);
      setParamName('');
      setParamValue('');
      setParamDescription('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create parameter' });
    } finally {
      setCreatingParam(false);
    }
  };

  const handleDeleteParam = async () => {
    if (!activeParam) return;
    try {
      await deleteSsmParameter(activeParam.Name);
      setActionMessage({ type: 'success', text: `Parameter "${activeParam.Name}" deleted.` });
      setSelectedParams([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete parameter' });
    }
  };

  const handleRevealDecrypted = async () => {
    if (!activeParam) return;
    setRevealing(true);
    try {
      const res = await getSsmParameterValue(activeParam.Name, true);
      setRevealedValue(res.parameter?.Value || res.Value || res.value || 'Decrypted');
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to decrypt parameter' });
    } finally {
      setRevealing(false);
    }
  };

  const handleAddTag = async () => {
    if (!activeParam || !tagKey.trim() || !tagValue.trim()) return;
    setSavingTag(true);
    try {
      await addSsmParameterTags(activeParam.Name, [{ Key: tagKey.trim(), Value: tagValue.trim() }]);
      setActionMessage({ type: 'success', text: `Tag "${tagKey.trim()}" added.` });
      setCreateTagOpen(false);
      setTagKey('');
      setTagValue('');
      await loadParamDetails(activeParam.Name);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to add tag' });
    } finally {
      setSavingTag(false);
    }
  };

  const handleRemoveTag = async (key: string) => {
    if (!activeParam || !key) return;
    try {
      await removeSsmParameterTags(activeParam.Name, [key]);
      setActionMessage({ type: 'success', text: `Tag "${key}" removed.` });
      await loadParamDetails(activeParam.Name);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to remove tag' });
    }
  };

  const handleCreateDocument = async () => {
    if (!docName.trim()) return;
    setCreatingDoc(true);
    try {
      await createSsmDocument(docName.trim(), docContent.trim(), docType.value as any);
      setActionMessage({ type: 'success', text: `SSM Document "${docName.trim()}" created.` });
      setCreateDocOpen(false);
      setDocName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create document' });
    } finally {
      setCreatingDoc(false);
    }
  };

  const handleDeleteDocument = async (name: string) => {
    try {
      await deleteSsmDocument(name);
      setActionMessage({ type: 'success', text: `SSM Document "${name}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete document' });
    }
  };

  const paramsList = data.parameters || [];

  const filteredParams = paramsList.filter((p: any) =>
    `${p.Name} ${p.Type} ${p.Description}`.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            description="Secure hierarchical configuration, secret storage with KMS encryption, and operational runbook automation."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button disabled={!activeParam} onClick={handleDeleteParam}>
                  Delete Parameter
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateParamOpen(true)}>
                  Create Parameter
                </Button>
              </SpaceBetween>
            }
          >
            AWS Systems Manager (SSM)
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
            <Box variant="awsui-key-label">Stored Parameters</Box>
            <Box variant="h1" color="text-status-info">
              {paramsList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">SSM Documents</Box>
            <Box variant="h1" color="text-status-info">
              {documents.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">KMS Integration</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">SecureString Ready</StatusIndicator>
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
            label: `Parameters (${paramsList.length})`,
            id: 'parameters',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">Parameters</Header>}>
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={filterText}
                      filteringPlaceholder="Filter by parameter path (/app/config...)"
                      onChange={({ detail }) => setFilterText(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Name / Path',
                          cell: (item) => (
                            <Button variant="inline-link" onClick={() => setSelectedParams([item])}>
                              <strong>{item.Name}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'type',
                          header: 'Type',
                          cell: (item) => (
                            <Badge color={item.Type === 'SecureString' ? 'red' : item.Type === 'StringList' ? 'blue' : 'green'}>
                              {item.Type}
                            </Badge>
                          ),
                          width: 140,
                        },
                        {
                          id: 'version',
                          header: 'Version',
                          cell: (item) => <Badge color="grey">{`v${item.Version}`}</Badge>,
                          width: 100,
                        },
                        {
                          id: 'description',
                          header: 'Description',
                          cell: (item) => item.Description,
                        },
                      ]}
                      items={filteredParams}
                      selectionType="single"
                      selectedItems={selectedParams}
                      onSelectionChange={({ detail }) => setSelectedParams(detail.selectedItems)}
                      empty={<Box textAlign="center">No parameters found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activeParam && (
                  <Container header={<Header variant="h2">Parameter: {activeParam.Name}</Header>}>
                    <Tabs
                      tabs={[
                        {
                          label: 'Value & Decryption',
                          id: 'val',
                          content: (
                            <SpaceBetween size="m">
                              <Container
                                header={
                                  <Header
                                    variant="h3"
                                    actions={
                                      activeParam.Type === 'SecureString' && (
                                        <Button loading={revealing} onClick={handleRevealDecrypted}>
                                          {revealedValue ? 'Re-decrypt with KMS' : 'Reveal Decrypted Value'}
                                        </Button>
                                      )
                                    }
                                  >
                                    Stored Value
                                  </Header>
                                }
                              >
                                {activeParam.Type === 'SecureString' ? (
                                  revealedValue ? (
                                    <SpaceBetween size="s">
                                      <div style={{ fontFamily: 'monospace', fontSize: '13px', background: '#0f1b2a', padding: '12px', borderRadius: '4px', color: '#58a6ff' }}>
                                        {revealedValue}
                                      </div>
                                      <Button iconName="copy" onClick={() => navigator.clipboard.writeText(revealedValue)}>
                                        Copy Value
                                      </Button>
                                    </SpaceBetween>
                                  ) : (
                                    <Box color="text-status-inactive">
                                      ●●●●●●●●●●●● (Encrypted with KMS key). Click "Reveal Decrypted Value" to decrypt.
                                    </Box>
                                  )
                                ) : (
                                  <div style={{ fontFamily: 'monospace', fontSize: '13px', background: '#0f1b2a', padding: '12px', borderRadius: '4px', color: '#58a6ff' }}>
                                    {activeParam.Value || 'Active value'}
                                  </div>
                                )}
                              </Container>

                              <KeyValuePairs
                                columns={3}
                                items={[
                                  { label: 'Name', value: activeParam.Name },
                                  { label: 'Type', value: activeParam.Type },
                                  { label: 'Version', value: `v${activeParam.Version}` },
                                  { label: 'KMS Key ID', value: activeParam.Type === 'SecureString' ? 'alias/aws/ssm' : 'None' },
                                  { label: 'Tier', value: 'Standard' },
                                  { label: 'Data Type', value: 'text' },
                                ]}
                              />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: `Version History (${paramHistory.length || 1})`,
                          id: 'history',
                          content: (
                            <Table
                              columnDefinitions={[
                                { id: 'ver', header: 'Version', cell: (h: any) => <Badge color="blue">{`v${h.Version || h.version || 1}`}</Badge>, width: 100 },
                                { id: 'type', header: 'Type', cell: (h: any) => h.Type || h.type || 'String', width: 130 },
                                { id: 'date', header: 'Modified Date', cell: (h: any) => h.LastModifiedDate || 'Today', width: 160 },
                                { id: 'val', header: 'Historical Value', cell: (h: any) => <code>{h.Value || h.value || 'Encrypted / Active'}</code> },
                              ]}
                              items={paramHistory.length > 0 ? paramHistory : [{ Version: activeParam.Version, Type: activeParam.Type, LastModifiedDate: activeParam.LastModifiedDate, Value: activeParam.Value }]}
                              loading={loadingHistory}
                            />
                          ),
                        },
                        {
                          label: `Tags (${paramTags.length})`,
                          id: 'tags',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={() => setCreateTagOpen(true)}>
                                  Add Tag
                                </Button>
                              </Box>

                              <Table
                                columnDefinitions={[
                                  { id: 'key', header: 'Key', cell: (t: any) => <strong>{t.Key || t.key}</strong> },
                                  { id: 'val', header: 'Value', cell: (t: any) => t.Value || t.value },
                                  {
                                    id: 'act',
                                    header: 'Action',
                                    cell: (t: any) => (
                                      <Button iconName="remove" onClick={() => handleRemoveTag(t.Key || t.key)}>
                                        Remove
                                      </Button>
                                    ),
                                    width: 110,
                                  },
                                ]}
                                items={paramTags}
                                empty={<Box textAlign="center">No tags assigned to this parameter.</Box>}
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
            label: `SSM Documents / Runbooks (${documents.length})`,
            id: 'documents',
            content: (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateDocOpen(true)}>
                          Create SSM Document
                        </Button>
                      }
                    >
                      SSM Documents (Automation Runbooks)
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'name', header: 'Document Name', cell: (d: any) => <strong>{d.Name || d.name}</strong> },
                      { id: 'type', header: 'Document Type', cell: (d: any) => <Badge color="blue">{d.DocumentType || d.documentType || 'Command'}</Badge>, width: 150 },
                      { id: 'format', header: 'Format', cell: (d: any) => d.DocumentFormat || 'JSON', width: 100 },
                      {
                        id: 'act',
                        header: 'Action',
                        cell: (d: any) => (
                          <Button iconName="remove" onClick={() => handleDeleteDocument(d.Name || d.name)}>
                            Delete
                          </Button>
                        ),
                        width: 110,
                      },
                    ]}
                    items={documents}
                    empty={<Box textAlign="center">No SSM documents created.</Box>}
                  />
                </Container>
              </SpaceBetween>
            ),
          },
        ]}
      />

      {/* Create Parameter Modal */}
      <Modal
        visible={createParamOpen}
        onDismiss={() => setCreateParamOpen(false)}
        header="Create Parameter"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateParamOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingParam} onClick={handleCreateParam}>
                Save Parameter
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Name / Path" description="Use hierarchical paths like /config/prod/db_url or /secrets/api_key.">
            <Input value={paramName} onChange={({ detail }) => setParamName(detail.value)} placeholder="/config/prod/database_url" />
          </FormField>

          <FormField label="Type">
            <Select
              selectedOption={paramType}
              onChange={({ detail }) => setParamType(detail.selectedOption as any)}
              options={[
                { label: 'String (Plaintext configuration)', value: 'String' },
                { label: 'StringList (Comma-separated list)', value: 'StringList' },
                { label: 'SecureString (Encrypted with KMS)', value: 'SecureString' },
              ]}
            />
          </FormField>

          <FormField label="Value">
            <Textarea rows={4} value={paramValue} onChange={({ detail }) => setParamValue(detail.value)} placeholder="postgres://user:secret@localhost:5432/mydb" />
          </FormField>

          <FormField label="Description (Optional)">
            <Input value={paramDescription} onChange={({ detail }) => setParamDescription(detail.value)} placeholder="Database connection string" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Add Tag Modal */}
      <Modal
        visible={createTagOpen}
        onDismiss={() => setCreateTagOpen(false)}
        header="Add Tag to Parameter"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateTagOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingTag} onClick={handleAddTag}>
                Add Tag
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Key">
            <Input value={tagKey} onChange={({ detail }) => setTagKey(detail.value)} placeholder="Environment" />
          </FormField>
          <FormField label="Value">
            <Input value={tagValue} onChange={({ detail }) => setTagValue(detail.value)} placeholder="production" />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Document Modal */}
      <Modal
        visible={createDocOpen}
        onDismiss={() => setCreateDocOpen(false)}
        header="Create SSM Document"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateDocOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingDoc} onClick={handleCreateDocument}>
                Create Document
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Document Name">
            <Input value={docName} onChange={({ detail }) => setDocName(detail.value)} placeholder="RestartWebServices" />
          </FormField>
          <FormField label="Document Type">
            <Select
              selectedOption={docType}
              onChange={({ detail }) => setDocType(detail.selectedOption as any)}
              options={[
                { label: 'Command', value: 'Command' },
                { label: 'Automation', value: 'Automation' },
                { label: 'Package', value: 'Package' },
              ]}
            />
          </FormField>
          <FormField label="Document Content (JSON)">
            <Textarea rows={10} value={docContent} onChange={({ detail }) => setDocContent(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
