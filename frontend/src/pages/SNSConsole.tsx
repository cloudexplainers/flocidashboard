import { PolicySummary } from '../components/PolicySummary';
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
import Checkbox from '@cloudscape-design/components/checkbox';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import {
  fetchServiceInventory,
  publishSnsMessage,
  createSnsSubscription,
  executeServiceAction,
} from '../api/client';

interface SubscriptionItem {
  SubscriptionArn: string;
  Protocol: string;
  Endpoint: string;
  TopicArn: string;
  Owner?: string;
  attributes?: Record<string, any>;
}

interface TopicItem {
  TopicArn: string;
  TopicName: string;
  SubscriptionsPending?: number;
  SubscriptionsConfirmed?: number;
  Type?: string;
  DisplayName?: string;
  Policy?: string;
  subscriptions?: SubscriptionItem[];
  attributes?: Record<string, any>;
}

const PROTOCOL_OPTIONS = [
  { label: 'Amazon SQS', value: 'sqs', description: 'Deliver messages to an SQS queue ARN' },
  { label: 'AWS Lambda', value: 'lambda', description: 'Invoke a Lambda function ARN' },
  { label: 'Email', value: 'email', description: 'Send email notification to an address' },
  { label: 'HTTP / HTTPS Webhook', value: 'https', description: 'Deliver HTTP POST payload to a URL' },
];

export const SNSConsole: React.FC<{ resourcePath?: string; onNavigate?: (path: string) => void }> = ({ resourcePath, onNavigate }) => {
  const requested = new URLSearchParams((resourcePath || '').split('?')[1] || '').get('topic');
  const resourceTab = (resourcePath || '').split('?')[0].split('/')[1] || 'subscriptions';
  const navigateResource = (item: TopicItem, tab = activeTabId) => {
    if (onNavigate) onNavigate(`sns/${tab}?${new URLSearchParams({ topic: item.TopicArn })}`);
  };
  const [topics, setTopics] = useState<TopicItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedItems, setSelectedItems] = useState<TopicItem[]>([]);
  const [filterText, setFilterText] = useState('');
  const [activeTabId, setActiveTabId] = useState(resourceTab);

  // Modals
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [publishModalOpen, setPublishModalOpen] = useState(false);
  const [subscribeModalOpen, setSubscribeModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);

  // Create Topic state
  const [newTopicName, setNewTopicName] = useState('');
  const [isFifo, setIsFifo] = useState(false);
  const [displayName, setDisplayName] = useState('');
  const [creating, setCreating] = useState(false);

  // Publish Message state
  const [subject, setSubject] = useState('');
  const [messageBody, setMessageBody] = useState('{\n  "alert": "Order Processed",\n  "order_id": "ord-1029",\n  "status": "APPROVED"\n}');
  const [messageGroupId, setMessageGroupId] = useState('group-1');
  const [messageDeduplicationId, setMessageDeduplicationId] = useState('');
  const [publishing, setPublishing] = useState(false);

  // Create Subscription state
  const [subProtocol, setSubProtocol] = useState(PROTOCOL_OPTIONS[0]);
  const [subEndpoint, setSubEndpoint] = useState('');
  const [subscribing, setSubscribing] = useState(false);

  // Deleting Subscription / Topic
  const [deletingSubArn, setDeletingSubArn] = useState<string | null>(null);
  const [deletingTopic, setDeletingTopic] = useState(false);
  const [actionAlert, setActionAlert] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);

  const activeTopic = selectedItems[0] || null;

  useEffect(() => { setActiveTabId(resourceTab); }, [resourceTab]);
  useEffect(() => {
    if (requested) setSelectedItems(topics.filter(item => item.TopicArn === requested || item.TopicName === requested));
  }, [requested, topics]);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchServiceInventory('sns');
      const rawTopics = data.topics || data.Topics || [];
      const list: TopicItem[] = rawTopics.map((t: any) => {
        const arn = typeof t === 'string' ? t : t.TopicArn || t.arn || '';
        const name = t.name || arn.split(':').pop() || 'topic';
        const attrs = t.attributes || t.Attributes || {};
        const isFifoTopic = name.endsWith('.fifo') || attrs.FifoTopic === 'true';
        return {
          TopicArn: arn,
          TopicName: name,
          SubscriptionsPending: Number(attrs.SubscriptionsPending ?? t.SubscriptionsPending ?? (Array.isArray(t.subscriptions) ? t.subscriptions.filter((sub: any) => sub.SubscriptionArn === 'PendingConfirmation').length : 0)),
          SubscriptionsConfirmed: Number(attrs.SubscriptionsConfirmed ?? t.SubscriptionsConfirmed ?? (Array.isArray(t.subscriptions) ? t.subscriptions.filter((sub: any) => sub.SubscriptionArn?.startsWith('arn:')).length : 0)),
          Type: isFifoTopic ? 'FIFO' : 'Standard',
          DisplayName: attrs.DisplayName || name,
          Policy: attrs.Policy || '',
          subscriptions: (Array.isArray(t.subscriptions) ? t.subscriptions : []).map((sub: any) => ({
            ...sub, attributes: sub.attributes || (data.subscriptions || []).find((detail: any) => (detail.arn || detail.SubscriptionArn) === sub.SubscriptionArn)?.attributes,
          })),
          attributes: attrs,
        };
      });
      setTopics(list);
      if (list.length > 0 && !selectedItems.length) {
        setSelectedItems([list[0]]);
      } else if (selectedItems.length > 0) {
        const refreshed = list.find((item) => item.TopicArn === selectedItems[0].TopicArn);
        setSelectedItems(refreshed ? [refreshed] : []);
      }
    } catch (err: any) {
      console.error(err);
      setActionAlert({ type: 'error', message: err.message || 'Failed to load SNS topics.' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateTopic = async () => {
    if (!newTopicName.trim()) return;
    let finalName = newTopicName.trim();
    if (isFifo && !finalName.endsWith('.fifo')) {
      finalName += '.fifo';
    }
    setCreating(true);
    setActionAlert(null);
    try {
      const params: any = { Name: finalName };
      if (displayName) params.DisplayName = displayName;
      if (isFifo) {
        params.Attributes = { FifoTopic: 'true', ContentBasedDeduplication: 'true' };
      }
      await executeServiceAction('sns', 'create_topic', params);
      setActionAlert({ type: 'success', message: `Topic "${finalName}" created successfully.` });
      setCreateModalOpen(false);
      setNewTopicName('');
      setDisplayName('');
      setIsFifo(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create topic.' });
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteTopic = async () => {
    if (!activeTopic) return;
    setDeletingTopic(true);
    setActionAlert(null);
    try {
      await executeServiceAction('sns', 'delete_topic', { TopicArn: activeTopic.TopicArn });
      setActionAlert({ type: 'success', message: `Topic "${activeTopic.TopicName}" deleted.` });
      setDeleteModalOpen(false);
      setSelectedItems([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete topic.' });
    } finally {
      setDeletingTopic(false);
    }
  };

  const handlePublishMessage = async () => {
    if (!activeTopic) return;
    setPublishing(true);
    setActionAlert(null);
    try {
      const res = await publishSnsMessage(
        activeTopic.TopicArn,
        messageBody,
        subject || undefined,
        undefined,
        undefined,
        activeTopic.Type === 'FIFO' ? messageGroupId : undefined,
        activeTopic.Type === 'FIFO' && messageDeduplicationId ? messageDeduplicationId : undefined
      );
      setActionAlert({ type: 'success', message: `Message published to topic (Message ID: ${res.message_id || 'OK'}).` });
      setPublishModalOpen(false);
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to publish message.' });
    } finally {
      setPublishing(false);
    }
  };

  const handleCreateSubscription = async () => {
    if (!activeTopic || !subEndpoint.trim()) return;
    setSubscribing(true);
    setActionAlert(null);
    try {
      await createSnsSubscription(activeTopic.TopicArn, subProtocol.value, subEndpoint.trim());
      setActionAlert({ type: 'success', message: `Subscribed ${subEndpoint} (${subProtocol.label}) to ${activeTopic.TopicName}.` });
      setSubscribeModalOpen(false);
      setSubEndpoint('');
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create subscription.' });
    } finally {
      setSubscribing(false);
    }
  };

  const handleDeleteSubscription = async (subArn: string) => {
    if (!subArn || subArn === 'PendingConfirmation') return;
    setDeletingSubArn(subArn);
    try {
      await executeServiceAction('sns', 'unsubscribe', { SubscriptionArn: subArn });
      setActionAlert({ type: 'success', message: 'Subscription removed.' });
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to unsubscribe.' });
    } finally {
      setDeletingSubArn(null);
    }
  };

  const filteredTopics = topics.filter((t) =>
    t.TopicName.toLowerCase().includes(filterText.toLowerCase()) ||
    t.TopicArn.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {actionAlert && (
        <Alert
          type={actionAlert.type}
          dismissible
          onDismiss={() => setActionAlert(null)}
          header={actionAlert.type === 'error' ? 'SNS Operation Error' : 'SNS Notification'}
        >
          {actionAlert.message}
        </Alert>
      )}

      <Table
        header={
          <Header
            variant="h1"
            counter={`(${topics.length})`}
            description="Fully managed pub/sub messaging service for microservices, distributed systems, and serverless applications."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button
                  disabled={!activeTopic}
                  iconName="share"
                  onClick={() => setPublishModalOpen(true)}
                >
                  Publish message
                </Button>
                <Button
                  disabled={!activeTopic}
                  iconName="remove"
                  onClick={() => setDeleteModalOpen(true)}
                >
                  Delete topic
                </Button>
                <Button
                  variant="primary"
                  iconName="add-plus"
                  onClick={() => setCreateModalOpen(true)}
                >
                  Create topic
                </Button>
              </SpaceBetween>
            }
          >
            Topics
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Topic name',
            cell: (item) => (
              <Button variant="inline-link" onClick={() => { setSelectedItems([item]); navigateResource(item); }}>
                <strong>{item.TopicName}</strong>
              </Button>
            ),
            sortingField: 'TopicName',
            isRowHeader: true,
          },
          {
            id: 'type',
            header: 'Type',
            cell: (item) => (
              <Badge color={item.Type === 'FIFO' ? 'blue' : 'grey'}>{item.Type || 'Standard'}</Badge>
            ),
          },
          {
            id: 'arn',
            header: 'Topic ARN',
            cell: (item) => (
              <code style={{ fontSize: '11px' }}>{item.TopicArn}</code>
            ),
          },
          {
            id: 'subscriptions',
            header: 'Subscriptions',
            cell: (item) => (
              <span>{item.SubscriptionsConfirmed ?? 0} confirmed · {item.SubscriptionsPending ?? 0} pending</span>
            ),
          },
        ]}
        items={filteredTopics}
        loading={loading}
        loadingText="Loading SNS topics..."
        selectionType="single"
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => { setSelectedItems(detail.selectedItems); if (detail.selectedItems[0]) navigateResource(detail.selectedItems[0]); }}
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter topics by name or ARN..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          <Box textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
            <SpaceBetween size="m">
              <b>No SNS topics found</b>
              <p>Create a topic to publish messages to multiple subscribers simultaneously.</p>
              <Button variant="primary" onClick={() => setCreateModalOpen(true)}>
                Create topic
              </Button>
            </SpaceBetween>
          </Box>
        }
      />

      {requested && !loading && !activeTopic && <Alert type="warning">Requested topic was not found. Select an available resource.</Alert>}
      {activeTopic && (
        <Container
          header={
            <Header
              variant="h2"
              description={`ARN: ${activeTopic.TopicArn}`}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button iconName="share" onClick={() => setPublishModalOpen(true)}>
                    Publish message
                  </Button>
                </SpaceBetween>
              }
            >
              Topic: {activeTopic.TopicName}
            </Header>
          }
        >
          <Tabs
            activeTabId={activeTabId}
            onChange={({ detail }) => { setActiveTabId(detail.activeTabId); navigateResource(activeTopic, detail.activeTabId); }}
            tabs={[
              {
                id: 'subscriptions',
                label: `Subscriptions (${activeTopic.subscriptions?.length || activeTopic.SubscriptionsConfirmed || 0})`,
                content: (
                  <SpaceBetween size="l">
                    <Table
                      header={
                        <Header
                          variant="h3"
                          counter={`(${activeTopic.subscriptions?.length || 0})`}
                          actions={
                            <Button
                              variant="primary"
                              iconName="add-plus"
                              onClick={() => setSubscribeModalOpen(true)}
                            >
                              Create subscription
                            </Button>
                          }
                        >
                          Topic Subscriptions
                        </Header>
                      }
                      columnDefinitions={[
                        {
                          id: 'protocol',
                          header: 'Protocol',
                          cell: (sub) => <Badge color="blue">{sub.Protocol ? sub.Protocol.toUpperCase() : 'UNKNOWN'}</Badge>,
                        },
                        {
                          id: 'endpoint',
                          header: 'Endpoint',
                          cell: (sub) => sub.Protocol === 'sqs' && sub.Endpoint?.startsWith('arn:') ? <Button variant="inline-link" href={`/sqs/attributes?${new URLSearchParams({ queue: sub.Endpoint })}`} onClick={event => { if (onNavigate) { event.preventDefault(); onNavigate(`sqs/attributes?${new URLSearchParams({ queue: sub.Endpoint })}`); } }}>{sub.Endpoint}</Button> : <span style={{ overflowWrap: 'anywhere' }}>{sub.Endpoint || 'Not reported'}</span>,
                        },
                        {
                          id: 'arn',
                          header: 'Subscription ARN',
                          cell: (sub) => (
                            <code style={{ fontSize: '11px', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>
                              {sub.SubscriptionArn || 'Not reported'}
                            </code>
                          ),
                        },
                        {
                          id: 'status',
                          header: 'Status',
                          cell: (sub) => (
                            <StatusIndicator type={sub.SubscriptionArn === 'PendingConfirmation' ? 'pending' : sub.SubscriptionArn?.startsWith('arn:') ? 'success' : 'info'}>
                              {sub.SubscriptionArn === 'PendingConfirmation' ? 'Pending' : sub.SubscriptionArn?.startsWith('arn:') ? 'Confirmed' : 'Not reported'}
                            </StatusIndicator>
                          ),
                        },
                        {
                          id: 'delivery',
                          header: 'Delivery settings',
                          cell: (sub) => <div style={{ overflowWrap: 'anywhere' }}>Raw delivery: {sub.attributes?.RawMessageDelivery ?? 'Not reported'}<br />Filter policy: {sub.attributes?.FilterPolicy || 'Not reported'}<br />Filter scope: {sub.attributes?.FilterPolicyScope || 'Not reported'}</div>,
                        },
                        {
                          id: 'actions',
                          header: 'Action',
                          cell: (sub) => (
                            <Button
                              iconName="remove"
                              disabled={!sub.SubscriptionArn?.startsWith('arn:')}
                              onClick={() => handleDeleteSubscription(sub.SubscriptionArn)}
                              loading={deletingSubArn === sub.SubscriptionArn}
                            >
                              Unsubscribe
                            </Button>
                          ),
                        },
                      ]}
                      items={activeTopic.subscriptions || []}
                      empty={
                        <Box textAlign="center" color="inherit" padding={{ vertical: 'm' }}>
                          <SpaceBetween size="s">
                            <b>No subscribers yet</b>
                            <p>Subscribe an SQS queue, Lambda function, or HTTPS webhook to receive topic messages.</p>
                            <Button onClick={() => setSubscribeModalOpen(true)}>Create subscription</Button>
                          </SpaceBetween>
                        </Box>
                      }
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'details',
                label: 'Topic details & configuration',
                content: (
                  <SpaceBetween size="l">
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Topic Name', value: activeTopic.TopicName },
                        { label: 'Topic ARN', value: activeTopic.TopicArn },
                        { label: 'Topic Type', value: activeTopic.Type || 'Standard' },
                        { label: 'Display Name', value: activeTopic.DisplayName || activeTopic.TopicName },
                        { label: 'Subscriptions Confirmed', value: String(activeTopic.SubscriptionsConfirmed ?? 0) },
                        { label: 'Subscriptions Pending', value: String(activeTopic.SubscriptionsPending ?? 0) },
                        { label: 'Encryption', value: activeTopic.attributes?.KmsMasterKeyId || 'Unavailable locally' },
                        { label: 'Delivery Retry Policy', value: 'Unavailable locally' },
                      ]}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'filtering',
                label: 'Subscription filter policies',
                content: (
                  <SpaceBetween size="m">
                    <Alert header="Attribute-Based Message Filtering">
                      SNS subscription filter policies evaluate message attributes and payload bodies, routing only relevant notifications to specific subscribers.
                    </Alert>
                    <Table header={<Header variant="h3">Subscription delivery policies</Header>}
                      items={activeTopic.subscriptions || []}
                      columnDefinitions={[
                        { id: 'endpoint', header: 'Endpoint', cell: sub => <span style={{ overflowWrap: 'anywhere' }}>{sub.Endpoint || 'Not reported'}</span> },
                        { id: 'raw', header: 'Raw delivery', cell: sub => sub.attributes?.RawMessageDelivery ?? 'Not reported' },
                        { id: 'scope', header: 'Filter scope', cell: sub => sub.attributes?.FilterPolicyScope || 'Not reported' },
                        { id: 'filter', header: 'Filter policy', cell: sub => <code style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{sub.attributes?.FilterPolicy || 'Not reported'}</code> },
                      ]}
                      empty={<span>No subscription delivery policies reported.</span>}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'policy',
                label: 'Access policy',
                content: (
                  <SpaceBetween size="m">
                    <Header variant="h3">Topic Access Policy (JSON)</Header>
                    <PolicySummary document={activeTopic.Policy || undefined} />
                    <textarea
                      readOnly
                      rows={8}
                      value={
                        activeTopic.Policy || 'No access policy reported for this topic.'
                      }
                      style={{
                        width: '100%',
                        boxSizing: 'border-box',
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

      {/* Create Topic Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create SNS Topic"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateTopic} loading={creating}>
                Create topic
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Topic name" description="Enter a unique name (append .fifo for FIFO topics).">
            <Input
              value={newTopicName}
              onChange={({ detail }) => setNewTopicName(detail.value)}
              placeholder="e.g. order-notifications"
            />
          </FormField>
          <FormField label="Display name (optional)" description="Used as the sender name in SMS and email delivery.">
            <Input
              value={displayName}
              onChange={({ detail }) => setDisplayName(detail.value)}
              placeholder="e.g. Order Alerts"
            />
          </FormField>
          <Checkbox
            checked={isFifo}
            onChange={({ detail }) => setIsFifo(detail.checked)}
          >
            FIFO topic (Strict message ordering and deduplication)
          </Checkbox>
        </SpaceBetween>
      </Modal>

      {/* Create Subscription Modal */}
      <Modal
        visible={subscribeModalOpen}
        onDismiss={() => setSubscribeModalOpen(false)}
        header={`Create Subscription for ${activeTopic?.TopicName || 'topic'}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setSubscribeModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateSubscription} loading={subscribing}>
                Create subscription
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Protocol">
            <Select
              selectedOption={subProtocol}
              onChange={({ detail }) => setSubProtocol(detail.selectedOption as any)}
              options={PROTOCOL_OPTIONS}
            />
          </FormField>
          <FormField
            label="Endpoint"
            description={
              subProtocol.value === 'sqs'
                ? 'Enter the ARN of the target SQS queue (e.g. arn:aws:sqs:us-east-1:000000000000:my-queue)'
                : subProtocol.value === 'lambda'
                ? 'Enter the ARN of the Lambda function'
                : subProtocol.value === 'email'
                ? 'Enter the destination email address'
                : 'Enter the webhook URL (e.g. https://api.example.com/webhooks/sns)'
            }
          >
            <Input
              value={subEndpoint}
              onChange={({ detail }) => setSubEndpoint(detail.value)}
              placeholder="Enter endpoint ARN, URL, or email..."
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Publish Message Modal */}
      <Modal
        visible={publishModalOpen}
        onDismiss={() => setPublishModalOpen(false)}
        header={`Publish Message to ${activeTopic?.TopicName || 'Topic'}`}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setPublishModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handlePublishMessage} loading={publishing}>
                Publish message
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Subject (optional)" description="Appears in email subjects or event metadata.">
            <Input
              value={subject}
              onChange={({ detail }) => setSubject(detail.value)}
              placeholder="e.g. New Order Available"
            />
          </FormField>
          <FormField label="Message body" description="Enter JSON payload or plaintext notification.">
            <Textarea
              value={messageBody}
              onChange={({ detail }) => setMessageBody(detail.value)}
              rows={6}
              placeholder="Enter message body..."
            />
          </FormField>
          {activeTopic?.Type === 'FIFO' && (
            <ColumnLayout columns={2}>
              <FormField label="Message group ID" description="Required for FIFO topics">
                <Input
                  value={messageGroupId}
                  onChange={({ detail }) => setMessageGroupId(detail.value)}
                  placeholder="e.g. group-1"
                />
              </FormField>
              <FormField label="Deduplication ID" description="Optional if Content-Based Deduplication is on">
                <Input
                  value={messageDeduplicationId}
                  onChange={({ detail }) => setMessageDeduplicationId(detail.value)}
                  placeholder="e.g. dedup-tx-1002"
                />
              </FormField>
            </ColumnLayout>
          )}
        </SpaceBetween>
      </Modal>

      {/* Delete Topic Modal */}
      <Modal
        visible={deleteModalOpen}
        onDismiss={() => setDeleteModalOpen(false)}
        header="Delete SNS Topic"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDeleteTopic} loading={deletingTopic}>
                Delete topic
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="error" header="Permanent Deletion">
            Are you sure you want to delete topic <strong>{activeTopic?.TopicName}</strong>? All associated subscriptions will be removed.
          </Alert>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};

