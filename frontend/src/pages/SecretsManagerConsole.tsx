import { ResourceDetails } from '../components/ResourceDetails';
import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import { fetchInventory, executeServiceAction } from '../api/client';
import { CodeSnippet } from '../components/CodeSnippet';

export const SecretsManagerConsole: React.FC = () => {
  const [data, setData] = useState<any>({ secrets: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [selectedSecrets, setSelectedSecrets] = useState<any[]>([]);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Store Secret Modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [secretName, setSecretName] = useState('');
  const [secretDescription, setSecretDescription] = useState('');
  const [secretValue, setSecretValue] = useState('{\n  "username": "dbadmin",\n  "password": "SuperSecretPassword123!"\n}');
  const [creating, setCreating] = useState(false);

  // Reveal Secret Modal
  const [revealModalOpen, setRevealModalOpen] = useState(false);
  const [revealedValue, setRevealedValue] = useState<any | null>(null);
  const [revealing, setRevealing] = useState(false);

  // Delete Modal
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('secretsmanager');
      setData(res || { secrets: [] });
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateSecret = async () => {
    if (!secretName.trim()) return;
    setCreating(true);
    setActionMessage(null);
    try {
      await executeServiceAction('secretsmanager', 'create_secret', {
        name: secretName.trim(),
        description: secretDescription,
        secret_string: secretValue,
      });
      setActionMessage({ type: 'success', text: `Secret "${secretName.trim()}" created successfully.` });
      setCreateModalOpen(false);
      setSecretName('');
      setSecretDescription('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create secret' });
    } finally {
      setCreating(false);
    }
  };

  const handleRevealSecret = async (secret: any) => {
    setRevealing(true);
    setRevealModalOpen(true);
    setRevealedValue(null);
    try {
      const res = await executeServiceAction('secretsmanager', 'get_secret_value', {
        secret_id: secret.Name || secret.name || secret.ARN,
      });
      setRevealedValue(res);
    } catch (err: any) {
      setRevealedValue({ error: err.message || 'Failed to retrieve secret value' });
    } finally {
      setRevealing(false);
    }
  };

  const handleDeleteSecret = async () => {
    if (!selectedSecrets.length) return;
    const secret = selectedSecrets[0];
    setDeleting(true);
    try {
      await executeServiceAction('secretsmanager', 'delete_secret', {
        secret_id: secret.Name || secret.name || secret.ARN,
        recovery_window_in_days: 0,
      });
      setActionMessage({ type: 'success', text: `Secret "${secret.Name || secret.name}" deleted.` });
      setDeleteModalOpen(false);
      setSelectedSecrets([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete secret' });
    } finally {
      setDeleting(false);
    }
  };

  const secretsList = (data.secrets || []).map((s: any) => ({
    ...s,
    Name: s.Name || s.name,
    ARN: s.ARN || s.arn,
    Description: s.Description || s.description || '—',
    LastChangedDate: s.LastChangedDate || s.last_changed_date,
    Stages: [...new Set(Object.values(s.SecretVersionsToStages || s.VersionIdsToStages || {}).flat())] as string[],
  }));

  const filteredSecrets = secretsList.filter((s: any) => {
    const text = `${s.Name} ${s.Description} ${s.ARN}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  return (
    <SpaceBetween size="l">
      {/* Header */}
      <Container
        header={
          <Header
            variant="h1"
            description="Rotate, manage, and retrieve database credentials, API keys, and other secrets throughout their lifecycle."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateModalOpen(true)}>
                  Store a New Secret
                </Button>
              </SpaceBetween>
            }
          >
            AWS Secrets Manager
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
            <Box variant="awsui-key-label">Total Secrets</Box>
            <Box variant="h1" color="text-status-info">
              {secretsList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active Credentials</Box>
            <Box variant="h1" color="text-status-info">
              {secretsList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Encryption Engine</Box>
            <Box variant="h2" color="text-status-info">
              <Badge color="green">AWS KMS (Mock)</Badge>
            </Box>
          </Box>
        </Grid>
      </Container>

      {/* Secrets Table */}
      <Container
        header={
          <Header
            variant="h2"
            description="Secrets stored securely in local Floci Secrets Manager."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  disabled={!selectedSecrets.length}
                  onClick={() => handleRevealSecret(selectedSecrets[0])}
                >
                  Retrieve Secret Value
                </Button>
                <Button
                  disabled={!selectedSecrets.length}
                  onClick={() => setDeleteModalOpen(true)}
                >
                  Delete Secret
                </Button>
              </SpaceBetween>
            }
          >
            Secrets ({secretsList.length})
          </Header>
        }
      >
        <SpaceBetween size="m">
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter secrets by name, description..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />

          <Table
            columnDefinitions={[
              {
                id: 'name',
                header: 'Secret Name',
                cell: (item) => (
                  <Button variant="inline-link" onClick={() => setSelectedSecrets([item])}>
                    <strong>{item.Name}</strong>
                  </Button>
                ),
              },
              {
                id: 'description',
                header: 'Description',
                cell: (item) => item.Description,
              },
              {
                id: 'stages',
                header: 'Version Stages',
                cell: (item) => (
                  <SpaceBetween direction="horizontal" size="xs">
                    {(item.Stages.length > 0 ? item.Stages : ['AWSCURRENT']).map((st: string) => (
                      <Badge key={st} color={st === 'AWSCURRENT' ? 'green' : 'blue'}>
                        {st}
                      </Badge>
                    ))}
                  </SpaceBetween>
                ),
                width: 220,
              },
              {
                id: 'actions',
                header: 'Value',
                cell: (item) => (
                  <Button variant="normal" iconName="security" onClick={() => handleRevealSecret(item)}>
                    Reveal Value
                  </Button>
                ),
                width: 150,
              },
            ]}
            items={filteredSecrets}
            selectionType="single"
            selectedItems={selectedSecrets}
            onSelectionChange={({ detail }) => setSelectedSecrets(detail.selectedItems)}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No secrets found</b>
                <p>Store a new secret to begin managing credentials in Secrets Manager.</p>
              </Box>
            }
          />
        </SpaceBetween>
      </Container>

      {selectedSecrets[0] && <ResourceDetails resource={Object.fromEntries(Object.entries(selectedSecrets[0]).filter(([key]) => !/secretstring|secretbinary|value/i.test(key)))} title={`Secret: ${selectedSecrets[0].Name}`} />}
      {/* Store Secret Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Store a New Secret"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creating} onClick={handleCreateSecret}>
                Store Secret
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Secret Name" description="Unique identifier for the secret (e.g. prod/database/master).">
            <Input
              value={secretName}
              onChange={({ detail }) => setSecretName(detail.value)}
              placeholder="prod/db/credentials"
            />
          </FormField>

          <FormField label="Description" description="Optional description.">
            <Input
              value={secretDescription}
              onChange={({ detail }) => setSecretDescription(detail.value)}
              placeholder="Production PostgreSQL database master credentials"
            />
          </FormField>

          <FormField
            label="Secret Value (JSON or Plaintext)"
            description="Enter the secret content to encrypt and store."
          >
            <Textarea
              rows={6}
              value={secretValue}
              onChange={({ detail }) => setSecretValue(detail.value)}
              placeholder="{\n  &quot;key&quot;: &quot;value&quot;\n}"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Reveal Secret Modal */}
      <Modal
        visible={revealModalOpen}
        onDismiss={() => setRevealModalOpen(false)}
        header={`Secret Value: ${selectedSecrets[0]?.Name || 'Retrieved Secret'}`}
        size="large"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setRevealModalOpen(false)}>
              Close
            </Button>
          </Box>
        }
      >
        {revealing ? (
          <Box textAlign="center" padding="l">
            <StatusIndicator type="loading">Retrieving decrypted secret value from KMS...</StatusIndicator>
          </Box>
        ) : revealedValue?.error ? (
          <Alert type="error">{revealedValue.error}</Alert>
        ) : (
          <SpaceBetween size="m">
            <div>
              <strong>ARN: </strong> <code>{revealedValue?.ARN || revealedValue?.arn || '—'}</code>
            </div>
            <div>
              <strong>Version ID: </strong> <code>{revealedValue?.VersionId || revealedValue?.version_id || 'AWSCURRENT'}</code>
            </div>
            <Header variant="h3">Decrypted Secret String</Header>
            <CodeSnippet
              language={revealedValue?.SecretString?.trim().startsWith('{') ? 'json' : 'cli'}
              code={revealedValue?.SecretString || '(Empty secret string)'}
            />
          </SpaceBetween>
        )}
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={deleteModalOpen}
        onDismiss={() => setDeleteModalOpen(false)}
        header={`Delete Secret: ${selectedSecrets[0]?.Name}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={deleting} onClick={handleDeleteSecret}>
                Confirm Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="warning">
          Are you sure you want to permanently delete secret <strong>{selectedSecrets[0]?.Name}</strong>?
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};
