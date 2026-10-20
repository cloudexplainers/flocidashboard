import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Select from '@cloudscape-design/components/select';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import {
  fetchInventory,
  executeServiceAction,
  fetchKmsKeyPolicy,
  putKmsKeyPolicy,
  fetchKmsKeyRotation,
  updateKmsKeyRotation,
  updateKmsKeyState,
  fetchKmsGrants,
  createKmsGrant,
  revokeKmsGrant,
  generateKmsDataKey,
} from '../api/client';

interface KMSConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const KMSConsole: React.FC<KMSConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({ keys: [], aliases: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [selectedKeys, setSelectedKeys] = useState<any[]>([]);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [selectedTabId, setSelectedTabId] = useState(activeTab || 'keys');

  useEffect(() => {
    if (activeTab) {
      setSelectedTabId(activeTab);
    }
  }, [activeTab]);

  // Create Key Modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [keySpec, setKeySpec] = useState({ label: 'SYMMETRIC_DEFAULT', value: 'SYMMETRIC_DEFAULT' });
  const [keyUsage, setKeyUsage] = useState({ label: 'ENCRYPT_DECRYPT', value: 'ENCRYPT_DECRYPT' });
  const [aliasName, setAliasName] = useState('');
  const [creating, setCreating] = useState(false);

  // Active Key Sub-states
  const [keyPolicyDoc, setKeyPolicyDoc] = useState('');
  const [savingPolicy, setSavingPolicy] = useState(false);
  const [rotationEnabled, setRotationEnabled] = useState(false);
  const [savingRotation, setSavingRotation] = useState(false);
  const [grants, setGrants] = useState<any[]>([]);
  const [createGrantOpen, setCreateGrantOpen] = useState(false);
  const [granteePrincipal, setGranteePrincipal] = useState('arn:aws:iam::000000000000:root');
  const [grantOperations, setGrantOperations] = useState('Decrypt,GenerateDataKey');
  const [savingGrant, setSavingGrant] = useState(false);

  // Data Key Generator
  const [dataKeySpec, setDataKeySpec] = useState({ label: 'AES_256 (256-bit symmetric key)', value: 'AES_256' });
  const [generatedDataKey, setGeneratedDataKey] = useState<any | null>(null);
  const [generatingDataKey, setGeneratingDataKey] = useState(false);

  // Crypto Workbench
  const [cryptoKeyId, setCryptoKeyId] = useState('');
  const [plaintextInput, setPlaintextInput] = useState('Hello Floci KMS');
  const [ciphertextOutput, setCiphertextOutput] = useState('');
  const [decryptInput, setDecryptInput] = useState('');
  const [decryptedOutput, setDecryptedOutput] = useState('');
  const [encrypting, setEncrypting] = useState(false);
  const [decrypting, setDecrypting] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('kms');
      setData(res || { keys: [], aliases: [] });
      if (res.keys?.length > 0 && selectedKeys.length === 0) {
        setSelectedKeys([res.keys[0]]);
        setCryptoKeyId(res.keys[0].KeyId || res.keys[0].key_id);
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

  const activeKey = selectedKeys[0] || null;
  const activeKeyId = activeKey ? activeKey.KeyId || activeKey.key_id : '';

  const loadKeyDetails = async (kId: string) => {
    if (!kId) return;
    try {
      const [policyRes, rotRes, grantsRes]: any[] = await Promise.all([
        fetchKmsKeyPolicy(kId),
        fetchKmsKeyRotation(kId),
        fetchKmsGrants(kId),
      ]);
      const pol = policyRes?.policy || policyRes?.Policy;
      setKeyPolicyDoc(typeof pol === 'object' ? JSON.stringify(pol, null, 2) : String(pol || ''));
      setRotationEnabled(rotRes?.KeyRotationEnabled ?? false);
      setGrants(grantsRes?.Grants || grantsRes?.grants || []);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    if (activeKeyId) {
      loadKeyDetails(activeKeyId);
    }
  }, [activeKeyId]);

  const handleCreateKey = async () => {
    setCreating(true);
    setActionMessage(null);
    try {
      const res = await executeServiceAction('kms', 'create_key', {
        description,
        key_spec: keySpec.value,
        key_usage: keyUsage.value,
      });
      const newKeyId = res?.KeyMetadata?.KeyId || res?.key?.KeyId;
      if (aliasName.trim() && newKeyId) {
        const fullAlias = aliasName.startsWith('alias/') ? aliasName.trim() : `alias/${aliasName.trim()}`;
        await executeServiceAction('kms', 'create_alias', {
          alias_name: fullAlias,
          target_key_id: newKeyId,
        });
      }
      setActionMessage({ type: 'success', text: `KMS Key created successfully${newKeyId ? `: ${newKeyId}` : ''}` });
      setCreateModalOpen(false);
      setDescription('');
      setAliasName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create KMS key' });
    } finally {
      setCreating(false);
    }
  };

  const handleSavePolicy = async () => {
    if (!activeKeyId || !keyPolicyDoc.trim()) return;
    setSavingPolicy(true);
    try {
      await putKmsKeyPolicy(activeKeyId, keyPolicyDoc.trim());
      setActionMessage({ type: 'success', text: 'Key policy updated.' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update key policy' });
    } finally {
      setSavingPolicy(false);
    }
  };

  const handleToggleRotation = async () => {
    if (!activeKeyId) return;
    setSavingRotation(true);
    try {
      await updateKmsKeyRotation(activeKeyId, !rotationEnabled);
      setRotationEnabled(!rotationEnabled);
      setActionMessage({ type: 'success', text: `Automatic key rotation ${!rotationEnabled ? 'enabled' : 'disabled'}.` });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update key rotation' });
    } finally {
      setSavingRotation(false);
    }
  };

  const handleToggleKeyState = async (enable: boolean) => {
    if (!activeKeyId) return;
    try {
      await updateKmsKeyState(activeKeyId, enable ? 'enable' : 'disable');
      setActionMessage({ type: 'success', text: `Key ${enable ? 'enabled' : 'disabled'}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to change key state' });
    }
  };

  const handleCreateGrant = async () => {
    if (!activeKeyId || !granteePrincipal.trim()) return;
    setSavingGrant(true);
    try {
      const ops = grantOperations.split(',').map((s) => s.trim()).filter(Boolean);
      await createKmsGrant(activeKeyId, granteePrincipal.trim(), ops);
      setActionMessage({ type: 'success', text: 'KMS grant created successfully.' });
      setCreateGrantOpen(false);
      await loadKeyDetails(activeKeyId);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create grant' });
    } finally {
      setSavingGrant(false);
    }
  };

  const handleRevokeGrant = async (grantId: string) => {
    if (!activeKeyId) return;
    try {
      await revokeKmsGrant(activeKeyId, grantId);
      setActionMessage({ type: 'success', text: `Grant ${grantId} revoked.` });
      await loadKeyDetails(activeKeyId);
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to revoke grant' });
    }
  };

  const handleGenerateDataKey = async () => {
    if (!activeKeyId) return;
    setGeneratingDataKey(true);
    try {
      const res = await generateKmsDataKey(activeKeyId, dataKeySpec.value as any);
      setGeneratedDataKey(res);
      setActionMessage({ type: 'success', text: 'Data encryption key generated.' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to generate data key' });
    } finally {
      setGeneratingDataKey(false);
    }
  };

  const handleEncrypt = async () => {
    if (!cryptoKeyId || !plaintextInput) return;
    setEncrypting(true);
    try {
      const res = await executeServiceAction('kms', 'encrypt', {
        key_id: cryptoKeyId,
        plaintext: plaintextInput,
      });
      const ct = res.CiphertextBlob || res.ciphertext || '';
      setCiphertextOutput(ct);
      setDecryptInput(ct);
      setActionMessage({ type: 'success', text: 'Plaintext encrypted successfully' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Encryption failed' });
    } finally {
      setEncrypting(false);
    }
  };

  const handleDecrypt = async () => {
    if (!decryptInput) return;
    setDecrypting(true);
    try {
      const res = await executeServiceAction('kms', 'decrypt', {
        ciphertext: decryptInput,
      });
      setDecryptedOutput(res.Plaintext || res.plaintext || '');
      setActionMessage({ type: 'success', text: 'Ciphertext decrypted successfully' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Decryption failed' });
    } finally {
      setDecrypting(false);
    }
  };

  const keysList = (data.keys || []).map((k: any) => {
    const matchingAlias = (data.aliases || []).find((a: any) => (a.TargetKeyId || a.target_key_id) === (k.KeyId || k.key_id));
    return {
      ...k,
      KeyId: k.KeyId || k.key_id,
      Arn: k.Arn || k.arn,
      Description: k.Description || k.description || '—',
      KeySpec: k.KeySpec || k.key_spec || 'SYMMETRIC_DEFAULT',
      KeyUsage: k.KeyUsage || k.key_usage || 'ENCRYPT_DECRYPT',
      Enabled: k.Enabled !== false && k.KeyState !== 'Disabled',
      Alias: matchingAlias ? matchingAlias.AliasName || matchingAlias.alias_name : '—',
    };
  });

  const filteredKeys = keysList.filter((k: any) =>
    (k.KeyId || '').toLowerCase().includes(filterText.toLowerCase()) ||
    (k.Alias || '').toLowerCase().includes(filterText.toLowerCase()) ||
    (k.Description || '').toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {/* Header Container */}
      <Container
        header={
          <Header
            variant="h1"
            description="Manage cryptographic keys, encryption policies, automatic key rotation, and grants."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button variant="primary" iconName="add-plus" onClick={() => setCreateModalOpen(true)}>
                  Create Key
                </Button>
              </SpaceBetween>
            }
          >
            AWS Key Management Service (KMS)
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
            <Box variant="awsui-key-label">Customer Managed Keys</Box>
            <Box variant="h1" color="text-status-info">
              {keysList.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Key Aliases</Box>
            <Box variant="h1" color="text-status-info">
              {data.aliases?.length || 0}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Cryptography Engine</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">AES-GCM / RSA Ready</StatusIndicator>
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
            label: `Customer Managed Keys (${keysList.length})`,
            id: 'keys',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">Keys Inventory</Header>}>
                  <SpaceBetween size="m">
                    <TextFilter
                      filteringText={filterText}
                      filteringPlaceholder="Find key by ID, alias, or description..."
                      onChange={({ detail }) => setFilterText(detail.filteringText)}
                    />

                    <Table
                      columnDefinitions={[
                        {
                          id: 'alias',
                          header: 'Alias',
                          cell: (item: any) => (
                            <Button variant="inline-link" onClick={() => setSelectedKeys([item])}>
                              <strong>{item.Alias}</strong>
                            </Button>
                          ),
                        },
                        {
                          id: 'keyId',
                          header: 'Key ID',
                          cell: (item: any) => <code>{item.KeyId}</code>,
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: (item: any) => (
                            <StatusIndicator type={item.Enabled ? 'success' : 'stopped'}>
                              {item.Enabled ? 'Enabled' : 'Disabled'}
                            </StatusIndicator>
                          ),
                          width: 120,
                        },
                        {
                          id: 'spec',
                          header: 'Key Spec',
                          cell: (item: any) => <Badge color="blue">{item.KeySpec}</Badge>,
                          width: 180,
                        },
                        {
                          id: 'usage',
                          header: 'Usage',
                          cell: (item: any) => item.KeyUsage,
                          width: 160,
                        },
                      ]}
                      items={filteredKeys}
                      selectionType="single"
                      selectedItems={selectedKeys}
                      onSelectionChange={({ detail }) => {
                        setSelectedKeys(detail.selectedItems);
                        if (detail.selectedItems.length > 0) {
                          setCryptoKeyId(detail.selectedItems[0].KeyId);
                        }
                      }}
                      empty={<Box textAlign="center">No KMS keys found.</Box>}
                    />
                  </SpaceBetween>
                </Container>

                {activeKey && (
                  <Container header={<Header variant="h2">Key: {activeKey.KeyId}</Header>}>
                    <Tabs
                      tabs={[
                        {
                          label: 'Key Policy',
                          id: 'policy',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" loading={savingPolicy} onClick={handleSavePolicy}>
                                  Save Key Policy
                                </Button>
                              </Box>
                              <Textarea rows={10} value={keyPolicyDoc} onChange={({ detail }) => setKeyPolicyDoc(detail.value)} />
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: 'Key Rotation',
                          id: 'rotation',
                          content: (
                            <SpaceBetween size="m">
                              <Box>
                                Automatic Key Rotation:{' '}
                                {rotationEnabled ? (
                                  <StatusIndicator type="success">Enabled (Annual rotation)</StatusIndicator>
                                ) : (
                                  <StatusIndicator type="stopped">Disabled</StatusIndicator>
                                )}
                              </Box>
                              <Button loading={savingRotation} onClick={handleToggleRotation}>
                                {rotationEnabled ? 'Disable Rotation' : 'Enable Automatic Rotation'}
                              </Button>
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: 'Key Lifecycle',
                          id: 'lifecycle',
                          content: (
                            <SpaceBetween size="m">
                              <Box>
                                Key State: <Badge color={activeKey.Enabled ? 'green' : 'grey'}>{activeKey.Enabled ? 'Enabled' : 'Disabled'}</Badge>
                              </Box>
                              <SpaceBetween direction="horizontal" size="xs">
                                {activeKey.Enabled ? (
                                  <Button onClick={() => handleToggleKeyState(false)}>Disable Key</Button>
                                ) : (
                                  <Button variant="primary" onClick={() => handleToggleKeyState(true)}>Enable Key</Button>
                                )}
                              </SpaceBetween>
                            </SpaceBetween>
                          ),
                        },
                        {
                          label: `Grants (${grants.length})`,
                          id: 'grants',
                          content: (
                            <SpaceBetween size="m">
                              <Box float="right">
                                <Button variant="primary" iconName="add-plus" onClick={() => setCreateGrantOpen(true)}>
                                  Create Grant
                                </Button>
                              </Box>
                              <Table
                                columnDefinitions={[
                                  { id: 'id', header: 'Grant ID', cell: (g: any) => <code>{g.GrantId || g.grant_id}</code> },
                                  { id: 'principal', header: 'Grantee Principal', cell: (g: any) => g.GranteePrincipal || g.grantee_principal },
                                  { id: 'ops', header: 'Operations', cell: (g: any) => (g.Operations || []).join(', ') },
                                  {
                                    id: 'act',
                                    header: 'Action',
                                    cell: (g: any) => (
                                      <Button iconName="remove" onClick={() => handleRevokeGrant(g.GrantId || g.grant_id)}>
                                        Revoke
                                      </Button>
                                    ),
                                    width: 110,
                                  },
                                ]}
                                items={grants}
                                empty={<Box textAlign="center">No grants created on this key.</Box>}
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
            label: 'Data Key Generator',
            id: 'datakey',
            content: (
              <Container header={<Header variant="h2">Generate Symmetric Data Key (Envelope Encryption)</Header>}>
                <SpaceBetween size="m">
                  <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                    <FormField label="Target KMS Key">
                      <Select
                        selectedOption={cryptoKeyId ? { label: cryptoKeyId, value: cryptoKeyId } : null}
                        onChange={({ detail }) => setCryptoKeyId(detail.selectedOption.value || '')}
                        options={keysList.map((k: any) => ({ label: `${k.Alias} (${k.KeyId})`, value: k.KeyId }))}
                      />
                    </FormField>
                    <FormField label="Data Key Spec">
                      <Select
                        selectedOption={dataKeySpec}
                        onChange={({ detail }) => setDataKeySpec(detail.selectedOption as any)}
                        options={[
                          { label: 'AES_256 (256-bit symmetric key)', value: 'AES_256' },
                          { label: 'AES_128 (128-bit symmetric key)', value: 'AES_128' },
                        ]}
                      />
                    </FormField>
                  </Grid>

                  <Button variant="primary" loading={generatingDataKey} onClick={handleGenerateDataKey}>
                    Generate Data Encryption Key
                  </Button>

                  {generatedDataKey && (
                    <Container header={<Header variant="h3">Generated Data Key Output</Header>}>
                      <KeyValuePairs
                        columns={1}
                        items={[
                          { label: 'Plaintext Key (Base64)', value: generatedDataKey.Plaintext || generatedDataKey.plaintext || '••••' },
                          { label: 'Ciphertext Blob (Encrypted with KMS)', value: generatedDataKey.CiphertextBlob || generatedDataKey.ciphertext_blob || '••••' },
                          { label: 'Key ARN', value: generatedDataKey.KeyId || cryptoKeyId },
                        ]}
                      />
                    </Container>
                  )}
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: 'Encrypt & Decrypt Playground',
            id: 'crypto',
            content: (
              <Container header={<Header variant="h2">KMS Cryptographic Workbench</Header>}>
                <Grid gridDefinition={[{ colspan: { default: 12, m: 6 } }, { colspan: { default: 12, m: 6 } }]}>
                  {/* Encrypt Card */}
                  <Container header={<Header variant="h3">Encrypt Plaintext</Header>}>
                    <SpaceBetween size="m">
                      <FormField label="KMS Key ID / Alias">
                        <Input value={cryptoKeyId} onChange={({ detail }) => setCryptoKeyId(detail.value)} placeholder="Key ID or alias/my-key" />
                      </FormField>

                      <FormField label="Plaintext Data">
                        <Textarea rows={3} value={plaintextInput} onChange={({ detail }) => setPlaintextInput(detail.value)} />
                      </FormField>

                      <Button variant="primary" loading={encrypting} onClick={handleEncrypt}>
                        Encrypt with KMS
                      </Button>

                      {ciphertextOutput && (
                        <FormField label="Ciphertext (Base64 Blob)">
                          <Textarea rows={4} value={ciphertextOutput} readOnly />
                        </FormField>
                      )}
                    </SpaceBetween>
                  </Container>

                  {/* Decrypt Card */}
                  <Container header={<Header variant="h3">Decrypt Ciphertext</Header>}>
                    <SpaceBetween size="m">
                      <FormField label="Ciphertext Blob">
                        <Textarea rows={4} value={decryptInput} onChange={({ detail }) => setDecryptInput(detail.value)} />
                      </FormField>

                      <Button variant="primary" loading={decrypting} onClick={handleDecrypt}>
                        Decrypt with KMS
                      </Button>

                      {decryptedOutput && (
                        <FormField label="Decrypted Plaintext">
                          <Textarea rows={3} value={decryptedOutput} readOnly />
                        </FormField>
                      )}
                    </SpaceBetween>
                  </Container>
                </Grid>
              </Container>
            ),
          },
        ]}
      />

      {/* Create Key Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create Customer Managed Key"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creating} onClick={handleCreateKey}>
                Create Key
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Key Description" description="Optional description for the key.">
            <Input value={description} onChange={({ detail }) => setDescription(detail.value)} placeholder="Primary app encryption key" />
          </FormField>

          <FormField label="Key Alias (Optional)">
            <Input value={aliasName} onChange={({ detail }) => setAliasName(detail.value)} placeholder="alias/app-key" />
          </FormField>

          <FormField label="Key Spec">
            <Select
              selectedOption={keySpec}
              onChange={({ detail }) => setKeySpec(detail.selectedOption as any)}
              options={[
                { label: 'SYMMETRIC_DEFAULT (AES-256-GCM)', value: 'SYMMETRIC_DEFAULT' },
                { label: 'RSA_2048', value: 'RSA_2048' },
                { label: 'RSA_4096', value: 'RSA_4096' },
                { label: 'ECC_NIST_P256', value: 'ECC_NIST_P256' },
              ]}
            />
          </FormField>

          <FormField label="Key Usage">
            <Select
              selectedOption={keyUsage}
              onChange={({ detail }) => setKeyUsage(detail.selectedOption as any)}
              options={[
                { label: 'ENCRYPT_DECRYPT', value: 'ENCRYPT_DECRYPT' },
                { label: 'SIGN_VERIFY', value: 'SIGN_VERIFY' },
                { label: 'GENERATE_VERIFY_MAC', value: 'GENERATE_VERIFY_MAC' },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Grant Modal */}
      <Modal
        visible={createGrantOpen}
        onDismiss={() => setCreateGrantOpen(false)}
        header={`Create Grant for ${activeKeyId}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateGrantOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={savingGrant} onClick={handleCreateGrant}>
                Create Grant
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Grantee Principal ARN">
            <Input value={granteePrincipal} onChange={({ detail }) => setGranteePrincipal(detail.value)} />
          </FormField>
          <FormField label="Operations (comma-separated)">
            <Input value={grantOperations} onChange={({ detail }) => setGrantOperations(detail.value)} placeholder="Decrypt, GenerateDataKey" />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
