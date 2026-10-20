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
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Badge from '@cloudscape-design/components/badge';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import {
  fetchInventory,
  createCloudFormationStack,
  updateCloudFormationStack,
  deleteCloudFormationStack,
  validateCloudFormationTemplate,
  createCloudFormationChangeSet,
  executeCloudFormationChangeSet,
  deleteCloudFormationChangeSet,
} from '../api/client';

const SAMPLE_TEMPLATE = `AWSTemplateFormatVersion: '2010-09-09'
Description: Floci Serverless Application Stack
Parameters:
  EnvironmentName:
    Type: String
    Default: dev
    Description: Deployment target environment
Resources:
  ApplicationBucket:
    Type: 'AWS::S3::Bucket'
    Properties:
      BucketName: !Sub 'floci-\${EnvironmentName}-app-assets'
  EventQueue:
    Type: 'AWS::SQS::Queue'
    Properties:
      QueueName: !Sub 'floci-\${EnvironmentName}-events'
      VisibilityTimeout: 60
  NotificationTopic:
    Type: 'AWS::SNS::Topic'
    Properties:
      TopicName: !Sub 'floci-\${EnvironmentName}-alerts'
Outputs:
  BucketArn:
    Description: 'ARN of created S3 bucket'
    Value: !GetAtt ApplicationBucket.Arn
  QueueUrl:
    Description: 'URL of created SQS queue'
    Value: !Ref EventQueue
  TopicArn:
    Description: 'ARN of created SNS topic'
    Value: !Ref NotificationTopic
`;

export const CloudFormationConsole: React.FC = () => {
  const [stacks, setStacks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [selectedStacks, setSelectedStacks] = useState<any[]>([]);
  const [actionAlert, setActionAlert] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [activeTabId, setActiveTabId] = useState('events');

  // Create Stack Modal
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [stackName, setStackName] = useState('');
  const [templateBody, setTemplateBody] = useState(SAMPLE_TEMPLATE);
  const [creating, setCreating] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validationResult, setValidationResult] = useState<any | null>(null);

  // Update Stack Modal
  const [updateModalOpen, setUpdateModalOpen] = useState(false);
  const [updateTemplateBody, setUpdateTemplateBody] = useState('');
  const [updating, setUpdating] = useState(false);

  // Change Set Modal
  const [changeSetModalOpen, setChangeSetModalOpen] = useState(false);
  const [changeSetName, setChangeSetName] = useState('');
  const [creatingChangeSet, setCreatingChangeSet] = useState(false);

  // Delete Modal
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('cloudformation');
      const rawStacks = res.stacks || [];
      const list = rawStacks.map((s: any) => ({
        ...s,
        StackName: s.name || s.StackName || s.stack_name || 'Unnamed stack',
        StackId: s.id || s.StackId || '',
        StackStatus: s.status || s.StackStatus || s.stack_status || 'CREATE_COMPLETE',
        StackStatusReason: s.status_reason || s.StackStatusReason || '',
        CreationTime: s.created || s.CreationTime || s.creation_time,
        UpdatedTime: s.updated || s.LastUpdatedTime,
        Description: s.description || s.Description || '—',
        Outputs: s.outputs || s.Outputs || [],
        Parameters: s.parameters || s.Parameters || [],
        Resources: s.resources || s.StackResources || [],
        Events: s.events || s.StackEvents || [],
        ChangeSets: s.change_sets || s.Summaries || [],
        Template: s.template?.TemplateBody || s.template || s.TemplateBody || '',
        DisableRollback: s.disable_rollback ?? false,
      }));
      setStacks(list);
      if (list.length > 0 && selectedStacks.length === 0) {
        setSelectedStacks([list[0]]);
      } else if (selectedStacks.length > 0) {
        const refreshed = list.find((s: any) => s.StackName === selectedStacks[0].StackName);
        if (refreshed) setSelectedStacks([refreshed]);
      }
    } catch (err: any) {
      console.error(err);
      setActionAlert({ type: 'error', message: err.message || 'Failed to load CloudFormation stacks.' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const activeStack = selectedStacks.length > 0 ? selectedStacks[0] : null;

  const handleValidateTemplate = async () => {
    setValidating(true);
    setValidationResult(null);
    try {
      const res = await validateCloudFormationTemplate(templateBody);
      setValidationResult({ valid: true, description: res.Description, params: res.Parameters });
      setActionAlert({ type: 'success', message: 'Template syntax validated successfully.' });
    } catch (err: any) {
      setValidationResult({ valid: false, error: err.message || 'Template validation failed.' });
    } finally {
      setValidating(false);
    }
  };

  const handleCreateStack = async () => {
    if (!stackName.trim()) return;
    setCreating(true);
    setActionAlert(null);
    try {
      await createCloudFormationStack(stackName.trim(), templateBody);
      setActionAlert({ type: 'success', message: `Stack "${stackName.trim()}" created successfully.` });
      setCreateModalOpen(false);
      setStackName('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create stack.' });
    } finally {
      setCreating(false);
    }
  };

  const handleUpdateStack = async () => {
    if (!activeStack || !updateTemplateBody.trim()) return;
    setUpdating(true);
    setActionAlert(null);
    try {
      await updateCloudFormationStack(activeStack.StackName, updateTemplateBody);
      setActionAlert({ type: 'success', message: `Stack "${activeStack.StackName}" update initiated.` });
      setUpdateModalOpen(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to update stack.' });
    } finally {
      setUpdating(false);
    }
  };

  const handleCreateChangeSet = async () => {
    if (!activeStack || !changeSetName.trim()) return;
    setCreatingChangeSet(true);
    setActionAlert(null);
    try {
      await createCloudFormationChangeSet(
        activeStack.StackName,
        changeSetName.trim(),
        typeof activeStack.Template === 'string' ? activeStack.Template : undefined
      );
      setActionAlert({ type: 'success', message: `Change Set "${changeSetName}" created.` });
      setChangeSetModalOpen(false);
      setChangeSetName('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create change set.' });
    } finally {
      setCreatingChangeSet(false);
    }
  };

  const handleExecuteChangeSet = async (csName: string) => {
    if (!activeStack) return;
    try {
      await executeCloudFormationChangeSet(activeStack.StackName, csName);
      setActionAlert({ type: 'success', message: `Change set "${csName}" executed.` });
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to execute change set.' });
    }
  };

  const handleDeleteChangeSet = async (csName: string) => {
    if (!activeStack) return;
    try {
      await deleteCloudFormationChangeSet(activeStack.StackName, csName);
      setActionAlert({ type: 'success', message: `Change set "${csName}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete change set.' });
    }
  };

  const handleDeleteStack = async () => {
    if (!activeStack) return;
    setDeleting(true);
    try {
      await deleteCloudFormationStack(activeStack.StackName);
      setActionAlert({ type: 'success', message: `Stack "${activeStack.StackName}" deleted.` });
      setDeleteModalOpen(false);
      setSelectedStacks([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete stack.' });
    } finally {
      setDeleting(false);
    }
  };

  const filteredStacks = stacks.filter((s: any) => {
    const text = `${s.StackName} ${s.StackStatus} ${s.Description}`.toLowerCase();
    return text.includes(filterText.toLowerCase());
  });

  return (
    <SpaceBetween size="l">
      {actionAlert && (
        <Alert
          type={actionAlert.type}
          dismissible
          onDismiss={() => setActionAlert(null)}
          header={actionAlert.type === 'error' ? 'CloudFormation Error' : 'CloudFormation Notification'}
        >
          {actionAlert.message}
        </Alert>
      )}

      {/* Header */}
      <Container
        header={
          <Header
            variant="h1"
            counter={`(${stacks.length})`}
            description="Infrastructure as Code workbench: Provision, trace events, inspect resources, and apply change sets in your local cloud."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button
                  disabled={!activeStack}
                  iconName="edit"
                  onClick={() => {
                    setUpdateTemplateBody(typeof activeStack?.Template === 'string' ? activeStack.Template : JSON.stringify(activeStack?.Template, null, 2));
                    setUpdateModalOpen(true);
                  }}
                >
                  Update Stack
                </Button>
                <Button
                  disabled={!activeStack}
                  iconName="remove"
                  onClick={() => setDeleteModalOpen(true)}
                >
                  Delete Stack
                </Button>
                <Button
                  variant="primary"
                  iconName="add-plus"
                  onClick={() => setCreateModalOpen(true)}
                >
                  Create Stack
                </Button>
              </SpaceBetween>
            }
          >
            AWS CloudFormation
          </Header>
        }
      >
        <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Total Stacks</Box>
            <Box variant="h1" color="text-status-info">
              {stacks.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active / Complete</Box>
            <Box variant="h1" color="text-status-info">
              {stacks.filter((s: any) => s.StackStatus.includes('COMPLETE')).length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Engine Status</Box>
            <Box variant="h2" color="text-status-info">
              <StatusIndicator type="success">CloudFormation Ready</StatusIndicator>
            </Box>
          </Box>
        </Grid>
      </Container>

      {/* Stacks Table */}
      <Table
        header={
          <Header
            variant="h2"
            description="CloudFormation stacks provisioned in Floci."
            counter={`(${stacks.length})`}
          >
            Stacks
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Stack name',
            cell: (item) => (
              <Button variant="inline-link" onClick={() => setSelectedStacks([item])}>
                <strong>{item.StackName}</strong>
              </Button>
            ),
            sortingField: 'StackName',
            isRowHeader: true,
          },
          {
            id: 'status',
            header: 'Status',
            cell: (item) => (
              <StatusIndicator
                type={
                  item.StackStatus.includes('COMPLETE')
                    ? 'success'
                    : item.StackStatus.includes('IN_PROGRESS')
                    ? 'in-progress'
                    : 'error'
                }
              >
                {item.StackStatus}
              </StatusIndicator>
            ),
          },
          {
            id: 'resources',
            header: 'Resources',
            cell: (item) => (
              <Badge color="blue">{item.Resources?.length || 0} resources</Badge>
            ),
          },
          {
            id: 'created',
            header: 'Created Time',
            cell: (item) => (
              <span style={{ color: '#879596', fontSize: '12px' }}>
                {item.CreationTime ? new Date(item.CreationTime).toLocaleString() : 'Just now'}
              </span>
            ),
          },
          {
            id: 'description',
            header: 'Description',
            cell: (item) => item.Description,
          },
        ]}
        items={filteredStacks}
        loading={loading}
        loadingText="Loading CloudFormation stacks..."
        selectionType="single"
        selectedItems={selectedStacks}
        onSelectionChange={({ detail }) => setSelectedStacks(detail.selectedItems)}
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter stacks by name, status, or description..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          <Box textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
            <SpaceBetween size="m">
              <b>No CloudFormation stacks found</b>
              <p>Deploy a CloudFormation YAML or JSON template to provision resources.</p>
              <Button variant="primary" onClick={() => setCreateModalOpen(true)}>
                Create Stack
              </Button>
            </SpaceBetween>
          </Box>
        }
      />

      {/* Stack Inspector Workspace */}
      {activeStack && (
        <Container
          header={
            <Header
              variant="h2"
              description={`Status: ${activeStack.StackStatus} | ID: ${activeStack.StackId || 'N/A'}`}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button onClick={() => setChangeSetModalOpen(true)}>
                    Create Change Set
                  </Button>
                  <Button
                    onClick={() => {
                      setUpdateTemplateBody(typeof activeStack?.Template === 'string' ? activeStack.Template : JSON.stringify(activeStack?.Template, null, 2));
                      setUpdateModalOpen(true);
                    }}
                  >
                    Update Stack
                  </Button>
                </SpaceBetween>
              }
            >
              Stack: {activeStack.StackName}
            </Header>
          }
        >
          <Tabs
            activeTabId={activeTabId}
            onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
            tabs={[
              {
                id: 'events',
                label: `Events (${activeStack.Events?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={<Header variant="h3">Deployment Timeline & Events</Header>}
                      columnDefinitions={[
                        {
                          id: 'timestamp',
                          header: 'Timestamp',
                          cell: (ev: any) => (
                            <span style={{ fontSize: '11px', color: '#879596' }}>
                              {ev.Timestamp || ev.EventTime ? new Date(ev.Timestamp || ev.EventTime).toLocaleTimeString() : '—'}
                            </span>
                          ),
                          width: 140,
                        },
                        {
                          id: 'logicalId',
                          header: 'Logical ID',
                          cell: (ev: any) => <strong>{ev.LogicalResourceId || 'Stack'}</strong>,
                        },
                        {
                          id: 'type',
                          header: 'Resource Type',
                          cell: (ev: any) => <Badge color="grey">{ev.ResourceType || 'AWS::CloudFormation::Stack'}</Badge>,
                        },
                        {
                          id: 'status',
                          header: 'Resource Status',
                          cell: (ev: any) => {
                            const st = ev.ResourceStatus || 'UNKNOWN';
                            return (
                              <StatusIndicator type={st.includes('COMPLETE') ? 'success' : st.includes('PROGRESS') ? 'in-progress' : 'error'}>
                                {st}
                              </StatusIndicator>
                            );
                          },
                        },
                        {
                          id: 'reason',
                          header: 'Status Reason',
                          cell: (ev: any) => ev.ResourceStatusReason || '—',
                        },
                      ]}
                      items={activeStack.Events || []}
                      empty={<Box textAlign="center" padding={{ vertical: 'm' }}>No events recorded for this stack.</Box>}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'resources',
                label: `Resources (${activeStack.Resources?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={<Header variant="h3">Provisioned Stack Resources</Header>}
                      columnDefinitions={[
                        {
                          id: 'logicalId',
                          header: 'Logical ID',
                          cell: (r: any) => <strong>{r.LogicalResourceId || r.logical_resource_id}</strong>,
                        },
                        {
                          id: 'physicalId',
                          header: 'Physical ID / Resource Name',
                          cell: (r: any) => (
                            <code style={{ fontSize: '11px' }}>
                              {r.PhysicalResourceId || r.physical_resource_id || '—'}
                            </code>
                          ),
                        },
                        {
                          id: 'type',
                          header: 'Type',
                          cell: (r: any) => <Badge color="blue">{r.ResourceType || r.resource_type}</Badge>,
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: (r: any) => (
                            <StatusIndicator type={(r.ResourceStatus || '').includes('COMPLETE') ? 'success' : 'in-progress'}>
                              {r.ResourceStatus || 'CREATE_COMPLETE'}
                            </StatusIndicator>
                          ),
                        },
                      ]}
                      items={activeStack.Resources || []}
                      empty={<Box textAlign="center" padding={{ vertical: 'm' }}>No resources tracked in this stack.</Box>}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'outputs_params',
                label: 'Parameters & Outputs',
                content: (
                  <SpaceBetween size="l">
                    <Container header={<Header variant="h3">Stack Parameters ({activeStack.Parameters?.length || 0})</Header>}>
                      <Table
                        columnDefinitions={[
                          { id: 'key', header: 'Parameter Key', cell: (p: any) => <strong>{p.ParameterKey || p.key}</strong> },
                          { id: 'value', header: 'Parameter Value', cell: (p: any) => <code>{p.ParameterValue || p.value}</code> },
                        ]}
                        items={activeStack.Parameters || []}
                        empty={<Box textAlign="center">No input parameters configured for this stack.</Box>}
                      />
                    </Container>

                    <Container header={<Header variant="h3">Stack Outputs ({activeStack.Outputs?.length || 0})</Header>}>
                      <Table
                        columnDefinitions={[
                          { id: 'key', header: 'Output Key', cell: (i: any) => <strong>{i.OutputKey || i.key}</strong> },
                          { id: 'value', header: 'Output Value', cell: (i: any) => <code>{i.OutputValue || i.value}</code> },
                          { id: 'desc', header: 'Description', cell: (i: any) => i.Description || i.description || '—' },
                        ]}
                        items={activeStack.Outputs || []}
                        empty={<Box textAlign="center">No outputs declared in this stack template.</Box>}
                      />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                id: 'change_sets',
                label: `Change Sets (${activeStack.ChangeSets?.length || 0})`,
                content: (
                  <SpaceBetween size="m">
                    <Table
                      header={
                        <Header
                          variant="h3"
                          actions={
                            <Button variant="primary" iconName="add-plus" onClick={() => setChangeSetModalOpen(true)}>
                              Create Change Set
                            </Button>
                          }
                        >
                          Change Sets
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: 'name',
                          header: 'Change Set Name',
                          cell: (cs: any) => <strong>{cs.ChangeSetName || cs.change_set_name}</strong>,
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: (cs: any) => <StatusIndicator type="success">{cs.Status || 'CREATE_COMPLETE'}</StatusIndicator>,
                        },
                        {
                          id: 'created',
                          header: 'Creation Time',
                          cell: (cs: any) => cs.CreationTime ? new Date(cs.CreationTime).toLocaleString() : 'Just now',
                        },
                        {
                          id: 'actions',
                          header: 'Actions',
                          cell: (cs: any) => (
                            <SpaceBetween direction="horizontal" size="xs">
                              <Button
                                onClick={() => handleExecuteChangeSet(cs.ChangeSetName || cs.change_set_name)}
                              >
                                Execute
                              </Button>
                              <Button
                                iconName="remove"
                                onClick={() => handleDeleteChangeSet(cs.ChangeSetName || cs.change_set_name)}
                              >
                                Delete
                              </Button>
                            </SpaceBetween>
                          ),
                        },
                      ]}
                      items={activeStack.ChangeSets || []}
                      empty={<Box textAlign="center" padding={{ vertical: 'm' }}>No change sets created for this stack.</Box>}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'template',
                label: 'Template & Metadata',
                content: (
                  <SpaceBetween size="m">
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Stack ID', value: activeStack.StackId || 'N/A' },
                        { label: 'Disable Rollback', value: activeStack.DisableRollback ? 'Yes' : 'No' },
                        { label: 'Status Reason', value: activeStack.StackStatusReason || 'N/A' },
                      ]}
                    />

                    <Header variant="h3">Template Body</Header>
                    <textarea
                      readOnly
                      rows={14}
                      value={typeof activeStack.Template === 'string' ? activeStack.Template : JSON.stringify(activeStack.Template, null, 2)}
                      style={{
                        width: '100%',
                        fontFamily: 'monospace',
                        fontSize: '12px',
                        background: '#1b2a3a',
                        color: '#4af',
                        padding: '12px',
                        borderRadius: '4px',
                        border: '1px solid #23395b',
                      }}
                    />
                  </SpaceBetween>
                ),
              },
            ]}
          />
        </Container>
      )}

      {/* Create Stack Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create CloudFormation Stack"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button onClick={handleValidateTemplate} loading={validating}>
                Validate Template
              </Button>
              <Button variant="primary" loading={creating} onClick={handleCreateStack}>
                Deploy Stack
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          {validationResult && (
            <Alert type={validationResult.valid ? 'success' : 'error'} header={validationResult.valid ? 'Template Valid' : 'Validation Error'}>
              {validationResult.valid ? validationResult.description || 'Template syntax is valid.' : validationResult.error}
            </Alert>
          )}

          <FormField label="Stack Name" description="Unique alphanumeric identifier for the CloudFormation stack.">
            <Input
              value={stackName}
              onChange={({ detail }) => setStackName(detail.value)}
              placeholder="e.g. dev-infrastructure"
            />
          </FormField>

          <FormField
            label="Template Body (YAML or JSON)"
            description="Paste your CloudFormation template definition."
          >
            <Textarea
              rows={12}
              value={templateBody}
              onChange={({ detail }) => setTemplateBody(detail.value)}
              placeholder="AWSTemplateFormatVersion: '2010-09-09'..."
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Update Stack Modal */}
      <Modal
        visible={updateModalOpen}
        onDismiss={() => setUpdateModalOpen(false)}
        header={`Update Stack: ${activeStack?.StackName}`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setUpdateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={updating} onClick={handleUpdateStack}>
                Update Stack
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Updated Template Body (YAML or JSON)">
            <Textarea
              rows={14}
              value={updateTemplateBody}
              onChange={({ detail }) => setUpdateTemplateBody(detail.value)}
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Create Change Set Modal */}
      <Modal
        visible={changeSetModalOpen}
        onDismiss={() => setChangeSetModalOpen(false)}
        header={`Create Change Set for ${activeStack?.StackName}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setChangeSetModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingChangeSet} onClick={handleCreateChangeSet}>
                Create Change Set
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Change Set Name">
            <Input
              value={changeSetName}
              onChange={({ detail }) => setChangeSetName(detail.value)}
              placeholder="e.g. update-queues-cs"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        visible={deleteModalOpen}
        onDismiss={() => setDeleteModalOpen(false)}
        header={`Delete Stack: ${activeStack?.StackName}`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={deleting} onClick={handleDeleteStack}>
                Confirm Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="warning" header="Resource Deletion">
          Are you sure you want to delete stack <strong>{activeStack?.StackName}</strong>? All resources provisioned by this stack template will be terminated.
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};

