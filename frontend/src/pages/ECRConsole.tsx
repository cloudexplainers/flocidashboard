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
import {
  fetchInventory,
  createEcrRepository,
  deleteEcrRepository,
  fetchEcrAuthToken,
  batchDeleteEcrImages,
  putEcrTagMutability,
  fetchEcrLifecyclePolicy,
  putEcrLifecyclePolicy,
  runEcrGarbageCollection,
} from '../api/client';

interface ECRConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const ECRConsole: React.FC<ECRConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({ repositories: [], images: [] });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Selected Repository
  const [selectedRepos, setSelectedRepos] = useState<any[]>([]);
  const [selectedImages, setSelectedImages] = useState<any[]>([]);

  // Create Repository Modal
  const [createRepoOpen, setCreateRepoOpen] = useState(false);
  const [repoName, setRepoName] = useState('');
  const [tagMutability, setTagMutability] = useState<{ label: string; value: 'MUTABLE' | 'IMMUTABLE' }>({
    label: 'MUTABLE (Tags can be overwritten)',
    value: 'MUTABLE',
  });
  const [creatingRepo, setCreatingRepo] = useState(false);

  // Push Commands Modal
  const [pushCommandsOpen, setPushCommandsOpen] = useState(false);
  const [authTokenData, setAuthTokenData] = useState<any | null>(null);

  // Lifecycle Policy
  const [lifecycleDoc, setLifecycleDoc] = useState('{\n  "rules": [\n    {\n      "rulePriority": 1,\n      "description": "Expire untagged images older than 14 days",\n      "selection": {\n        "tagStatus": "untagged",\n        "countType": "sinceImagePushed",\n        "countUnit": "days",\n        "countNumber": 14\n      },\n      "action": {\n        "type": "expire"\n      }\n    }\n  ]\n}');
  const [savingLifecycle, setSavingLifecycle] = useState(false);

  // Garbage Collection
  const [runningGc, setRunningGc] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('ecr');
      setData(res || { repositories: [], images: [] });
      if (res?.repositories?.length > 0 && selectedRepos.length === 0) {
        setSelectedRepos([res.repositories[0]]);
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

  const activeRepo = selectedRepos[0] || null;
  const activeRepoName = activeRepo ? activeRepo.repositoryName || activeRepo.name : '';
  const activeRepoUri = activeRepo ? activeRepo.repositoryUri || `000000000000.dkr.ecr.us-east-1.localhost:4566/${activeRepoName}` : '';

  const loadLifecycle = async (rName: string) => {
    if (!rName) return;
    try {
      const pol = await fetchEcrLifecyclePolicy(rName);
      if (pol?.lifecyclePolicyText) {
        setLifecycleDoc(pol.lifecyclePolicyText);
      }
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    if (activeRepoName) {
      loadLifecycle(activeRepoName);
    }
  }, [activeRepoName]);

  const filteredRepos = useMemo(() => {
    const list = data.repositories || [];
    if (!filterText) return list;
    return list.filter((r: any) =>
      (r.repositoryName || r.name || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [data.repositories, filterText]);

  const repoImages = useMemo(() => {
    const allImgs = data.images || [];
    if (!activeRepoName) return allImgs;
    return allImgs.filter((img: any) => {
      const r = img.repositoryName || img.repository;
      return r === activeRepoName || !r;
    });
  }, [data.images, activeRepoName]);

  // Actions
  const handleCreateRepo = async () => {
    if (!repoName.trim()) return;
    setCreatingRepo(true);
    try {
      await createEcrRepository(repoName.trim(), tagMutability.value);
      setActionMessage({ type: 'success', text: `ECR Repository "${repoName.trim()}" created.` });
      setCreateRepoOpen(false);
      setRepoName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create repository' });
    } finally {
      setCreatingRepo(false);
    }
  };

  const handleDeleteRepo = async (rName: string) => {
    if (!confirm(`Are you sure you want to delete repository "${rName}"?`)) return;
    try {
      await deleteEcrRepository(rName, true);
      setActionMessage({ type: 'success', text: `Repository "${rName}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete repository' });
    }
  };

  const handleFetchPushCommands = async () => {
    setPushCommandsOpen(true);
    try {
      const auth = await fetchEcrAuthToken();
      setAuthTokenData(auth);
    } catch (err: any) {
      console.error(err);
    }
  };

  const handleDeleteSelectedImages = async () => {
    if (!activeRepoName || selectedImages.length === 0) return;
    try {
      const imageIds = selectedImages.map((img: any) => ({
        imageDigest: img.imageDigest,
        imageTag: img.imageTag || img.imageTags?.[0],
      }));
      await batchDeleteEcrImages(activeRepoName, imageIds);
      setActionMessage({ type: 'success', text: `Deleted ${selectedImages.length} images from "${activeRepoName}".` });
      setSelectedImages([]);
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete images' });
    }
  };

  const handleToggleMutability = async (current: string) => {
    if (!activeRepoName) return;
    const next: 'MUTABLE' | 'IMMUTABLE' = current === 'IMMUTABLE' ? 'MUTABLE' : 'IMMUTABLE';
    try {
      await putEcrTagMutability(activeRepoName, next);
      setActionMessage({ type: 'success', text: `Repository mutability set to ${next}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update tag mutability' });
    }
  };

  const handleSaveLifecyclePolicy = async () => {
    if (!activeRepoName) return;
    setSavingLifecycle(true);
    try {
      await putEcrLifecyclePolicy(activeRepoName, lifecycleDoc);
      setActionMessage({ type: 'success', text: 'Lifecycle policy saved successfully.' });
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to save lifecycle policy' });
    } finally {
      setSavingLifecycle(false);
    }
  };

  const handleRunGc = async () => {
    setRunningGc(true);
    try {
      await runEcrGarbageCollection();
      setActionMessage({ type: 'success', text: 'ECR Garbage Collection completed. Untagged images cleaned up.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Garbage collection failed' });
    } finally {
      setRunningGc(false);
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Amazon Elastic Container Registry (ECR) is a fully managed container registry for Docker OCI images."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button loading={runningGc} onClick={handleRunGc}>
              Run Garbage Collection
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setCreateRepoOpen(true)}>
              Create Repository
            </Button>
          </SpaceBetween>
        }
      >
        Amazon Elastic Container Registry (ECR)
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {/* Metrics */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
        <Container>
          <Box variant="awsui-key-label">Repositories</Box>
          <Box variant="awsui-value-large">{(data.repositories || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Total Container Images</Box>
          <Box variant="awsui-value-large">{(data.images || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Active Registry</Box>
          <Box variant="awsui-value-large">000000000000</Box>
        </Container>
      </Grid>

      <Tabs
        activeTabId={activeTab || 'repositories'}
        onChange={({ detail }) => onTabChange && onTabChange(detail.activeTabId)}
        tabs={[
          {
            label: `Repositories (${(data.repositories || []).length})`,
            id: 'repositories',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        {activeRepo && (
                          <Button onClick={handleFetchPushCommands}>
                            View Push Commands
                          </Button>
                        )}
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateRepoOpen(true)}>
                          Create Repository
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Private Repositories
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                    filteringPlaceholder="Filter repositories by name..."
                  />
                  <Table
                    columnDefinitions={[
                      {
                        id: 'name',
                        header: 'Repository Name',
                        cell: (item: any) => (
                          <Button variant="inline-link" onClick={() => setSelectedRepos([item])}>
                            <strong>{item.repositoryName || item.name}</strong>
                          </Button>
                        ),
                      },
                      {
                        id: 'uri',
                        header: 'URI',
                        cell: (item: any) => <code>{item.repositoryUri || `000000000000.dkr.ecr.us-east-1.localhost:4566/${item.repositoryName}`}</code>,
                      },
                      {
                        id: 'mutability',
                        header: 'Tag Mutability',
                        cell: (item: any) => (
                          <Badge color={item.imageTagMutability === 'IMMUTABLE' ? 'blue' : 'grey'}>
                            {item.imageTagMutability || 'MUTABLE'}
                          </Badge>
                        ),
                        width: 140,
                      },
                      {
                        id: 'created',
                        header: 'Created At',
                        cell: (item: any) => item.createdAt || 'Just now',
                        width: 140,
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <SpaceBetween direction="horizontal" size="xs">
                            <Button
                              onClick={() => {
                                setSelectedRepos([item]);
                                handleFetchPushCommands();
                              }}
                            >
                              Push Commands
                            </Button>
                            <Button
                              iconName="remove"
                              onClick={() => handleDeleteRepo(item.repositoryName || item.name)}
                            >
                              Delete
                            </Button>
                          </SpaceBetween>
                        ),
                        width: 220,
                      },
                    ]}
                    items={filteredRepos}
                    selectionType="single"
                    selectedItems={selectedRepos}
                    onSelectionChange={({ detail }) => setSelectedRepos(detail.selectedItems)}
                    empty={<Box textAlign="center">No ECR repositories found.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Images Explorer (${repoImages.length})`,
            id: 'images',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    description={activeRepoName ? `Images in "${activeRepoName}"` : 'Select a repository to inspect images'}
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button
                          disabled={selectedImages.length === 0}
                          iconName="remove"
                          onClick={handleDeleteSelectedImages}
                        >
                          Delete ({selectedImages.length})
                        </Button>
                        <Button variant="primary" onClick={handleFetchPushCommands}>
                          Push Image
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Image Artifacts
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    {
                      id: 'tags',
                      header: 'Image Tags',
                      cell: (item: any) => (
                        <SpaceBetween direction="horizontal" size="xxs">
                          {(item.imageTags || [item.imageTag || 'latest']).map((t: string) => (
                            <Badge key={t} color="blue">{t}</Badge>
                          ))}
                        </SpaceBetween>
                      ),
                      width: 180,
                    },
                    {
                      id: 'digest',
                      header: 'Image Digest (SHA256)',
                      cell: (item: any) => <code>{item.imageDigest || 'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'}</code>,
                    },
                    {
                      id: 'size',
                      header: 'Size',
                      cell: (item: any) => `${((item.imageSizeInBytes || 52428800) / (1024 * 1024)).toFixed(1)} MB`,
                      width: 110,
                    },
                    {
                      id: 'pushedAt',
                      header: 'Pushed At',
                      cell: (item: any) => item.imagePushedAt || 'Recent',
                      width: 140,
                    },
                  ]}
                  items={repoImages}
                  selectionType="multi"
                  selectedItems={selectedImages}
                  onSelectionChange={({ detail }) => setSelectedImages(detail.selectedItems)}
                  empty={<Box textAlign="center">No images found in this repository. Use Push Commands to push an image.</Box>}
                />
              </Container>
            ),
          },
          {
            label: 'Lifecycle Policy & Mutability',
            id: 'lifecycle',
            content: (
              <Grid gridDefinition={[{ colspan: { default: 12, s: 7 } }, { colspan: { default: 12, s: 5 } }]}>
                <Container
                  header={
                    <Header
                      variant="h3"
                      actions={
                        <Button variant="primary" loading={savingLifecycle} onClick={handleSaveLifecyclePolicy}>
                          Save Policy
                        </Button>
                      }
                    >
                      Lifecycle Policy Document (JSON)
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <FormField label="JSON Lifecycle Rules" description="Automates expiration and clean up of obsolete images.">
                      <Textarea
                        rows={12}
                        value={lifecycleDoc}
                        onChange={({ detail }) => setLifecycleDoc(detail.value)}
                      />
                    </FormField>
                  </SpaceBetween>
                </Container>

                <Container header={<Header variant="h3">Repository Settings</Header>}>
                  <SpaceBetween size="l">
                    <FormField label="Tag Mutability">
                      <SpaceBetween direction="horizontal" size="s">
                        <Box>
                          Current: <Badge color={activeRepo?.imageTagMutability === 'IMMUTABLE' ? 'blue' : 'grey'}>
                            {activeRepo?.imageTagMutability || 'MUTABLE'}
                          </Badge>
                        </Box>
                        <Button
                          onClick={() => handleToggleMutability(activeRepo?.imageTagMutability || 'MUTABLE')}
                        >
                          Switch to {activeRepo?.imageTagMutability === 'IMMUTABLE' ? 'MUTABLE' : 'IMMUTABLE'}
                        </Button>
                      </SpaceBetween>
                    </FormField>

                    <FormField label="Garbage Collection">
                      <Button loading={runningGc} onClick={handleRunGc}>
                        Run Image GC Now
                      </Button>
                    </FormField>
                  </SpaceBetween>
                </Container>
              </Grid>
            ),
          },
        ]}
      />

      {/* Create Repository Modal */}
      <Modal
        visible={createRepoOpen}
        onDismiss={() => setCreateRepoOpen(false)}
        header="Create ECR Repository"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateRepoOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingRepo} onClick={handleCreateRepo}>
                Create Repository
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Repository Name" description="Unique namespace name for container images.">
            <Input value={repoName} onChange={({ detail }) => setRepoName(detail.value)} placeholder="my-service/backend" />
          </FormField>
          <FormField label="Tag Mutability" description="Determines whether image tags can be overwritten.">
            <Select
              selectedOption={tagMutability}
              onChange={({ detail }) => setTagMutability(detail.selectedOption as any)}
              options={[
                { label: 'MUTABLE (Tags can be overwritten)', value: 'MUTABLE' },
                { label: 'IMMUTABLE (Prevent tag overwriting)', value: 'IMMUTABLE' },
              ]}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Push Commands Modal */}
      <Modal
        visible={pushCommandsOpen}
        onDismiss={() => setPushCommandsOpen(false)}
        header={`Push commands for ${activeRepoName || 'repository'}`}
        size="large"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setPushCommandsOpen(false)}>
              Done
            </Button>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info">
            Run these terminal commands in your local Docker environment to build, tag, and push images to this repository.
          </Alert>

          <FormField label="1. Retrieve an authentication token and authenticate Docker client">
            <Textarea
              rows={2}
              readOnly
              value={
                authTokenData?.authorizationData?.[0]?.authorizationToken
                  ? `echo "${authTokenData.authorizationData[0].authorizationToken}" | docker login --username AWS --password-stdin ${activeRepoUri.split('/')[0]}`
                  : `aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin ${activeRepoUri.split('/')[0]}`
              }
            />
          </FormField>

          <FormField label="2. Build your Docker image">
            <Textarea rows={1} readOnly value={`docker build -t ${activeRepoName || 'my-app'} .`} />
          </FormField>

          <FormField label="3. Tag your image to match repository URI">
            <Textarea rows={1} readOnly value={`docker tag ${activeRepoName || 'my-app'}:latest ${activeRepoUri}:latest`} />
          </FormField>

          <FormField label="4. Push the image to Amazon ECR">
            <Textarea rows={1} readOnly value={`docker push ${activeRepoUri}:latest`} />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};
