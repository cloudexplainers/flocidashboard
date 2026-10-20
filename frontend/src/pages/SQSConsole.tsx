import { QueueDeliveryOverview } from '../components/QueueDeliveryOverview';
import { PolicySummary } from '../components/PolicySummary';
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
import Checkbox from '@cloudscape-design/components/checkbox';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Box from '@cloudscape-design/components/box';
import Alert from '@cloudscape-design/components/alert';
import Badge from '@cloudscape-design/components/badge';
import Grid from '@cloudscape-design/components/grid';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import {
  fetchServiceInventory,
  createSqsQueue,
  deleteSqsQueue,
  purgeSqsQueue,
  sendSqsMessage,
  receiveSqsMessages,
  deleteSqsMessage,
} from '../api/client';

interface QueueItem {
  QueueName: string;
  QueueUrl: string;
  QueueArn?: string;
  ApproximateNumberOfMessages?: number;
  ApproximateNumberOfMessagesNotVisible?: number;
  ApproximateNumberOfMessagesDelayed?: number;
  CreatedTimestamp?: string;
  VisibilityTimeout?: string | number;
  MessageRetentionPeriod?: string | number;
  MaximumMessageSize?: string | number;
  DelaySeconds?: string | number;
  ReceiveMessageWaitTimeSeconds?: string | number;
  Policy?: string;
  RedrivePolicy?: string;
  Type?: string;
}

interface SqsMessageItem {
  MessageId: string;
  ReceiptHandle: string;
  MD5OfBody: string;
  Body: string;
  Attributes?: Record<string, string>;
  MessageAttributes?: Record<string, any>;
}

export const SQSConsole: React.FC<{ resourcePath?: string; onNavigate?: (path: string) => void }> = ({ resourcePath, onNavigate }) => {
  const requested = new URLSearchParams((resourcePath || '').split('?')[1] || '').get('queue');
  const resourceTab = (resourcePath || '').split('?')[0].split('/')[1] || 'send-receive';
  const navigateResource = (item: QueueItem, tab = activeTabId) => {
    if (onNavigate) onNavigate(`sqs/${tab}?${new URLSearchParams({ queue: item.QueueName })}`);
  };
  const [queues, setQueues] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedItems, setSelectedItems] = useState<QueueItem[]>([]);
  const [filterText, setFilterText] = useState('');
  const [activeTabId, setActiveTabId] = useState(resourceTab);

  // Modals
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [purgeModalOpen, setPurgeModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [inspectMessage, setInspectMessage] = useState<SqsMessageItem | null>(null);

  // Create Queue state
  const [newQueueName, setNewQueueName] = useState('');
  const [isFifo, setIsFifo] = useState(false);
  const [visibilityTimeoutInput, setVisibilityTimeoutInput] = useState('30');
  const [creating, setCreating] = useState(false);

  // Send Message state
  const [sendBody, setSendBody] = useState('{\n  "event": "order_placed",\n  "order_id": "ord-9821",\n  "amount": 49.99\n}');
  const [sendDelay, setSendDelay] = useState('0');
  const [messageGroupId, setMessageGroupId] = useState('group-1');
  const [messageDeduplicationId, setMessageDeduplicationId] = useState('');
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<any>(null);

  // Receive / Poll Messages state
  const [maxReceiveCount, setMaxReceiveCount] = useState('5');
  const [pollVisibilityTimeout, setPollVisibilityTimeout] = useState('0'); // default 0 for peek
  const [pollWaitTime, setPollWaitTime] = useState('0');
  const [polling, setPolling] = useState(false);
  const [receivedMessages, setReceivedMessages] = useState<SqsMessageItem[]>([]);
  const [deletingMessageId, setDeletingMessageId] = useState<string | null>(null);

  // Purging / Action alerts
  const [purging, setPurging] = useState(false);
  const [deletingQueue, setDeletingQueue] = useState(false);
  const [actionAlert, setActionAlert] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);

  const selectionGeneration = useRef(0);
  const activeQueue = selectedItems[0] || null;

  useEffect(() => { setActiveTabId(resourceTab); }, [resourceTab]);
  useEffect(() => {
    if (requested) setSelectedItems(queues.filter(item => item.QueueName === requested || item.QueueArn === requested));
  }, [requested, queues]);

  useEffect(() => {
    selectionGeneration.current += 1;
    setReceivedMessages([]); setInspectMessage(null); setSendResult(null); setActionAlert(null); setPolling(false); setSending(false);
    return () => { selectionGeneration.current += 1; };
  }, [activeQueue?.QueueName]);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchServiceInventory('sqs');
      const rawList = data.queues || data.QueueUrls || [];
      const list: QueueItem[] = rawList.map((q: any) => {
        if (typeof q === 'string') {
          const name = q.split('/').pop() || q;
          return {
            QueueName: name,
            QueueUrl: q,
            ApproximateNumberOfMessages: 0,
            Type: name.endsWith('.fifo') ? 'FIFO' : 'Standard',
          };
        }
        const name = q.QueueName || q.name || (q.QueueUrl || '').split('/').pop() || 'Unnamed';
        const attrs = q.attributes || q.Attributes || {};
        return {
          QueueName: name,
          QueueUrl: q.QueueUrl || q.url || '',
          QueueArn: attrs.QueueArn || q.QueueArn || q.arn,
          ApproximateNumberOfMessages: Number(attrs.ApproximateNumberOfMessages || q.ApproximateNumberOfMessages || 0),
          ApproximateNumberOfMessagesNotVisible: Number(attrs.ApproximateNumberOfMessagesNotVisible || 0),
          ApproximateNumberOfMessagesDelayed: Number(attrs.ApproximateNumberOfMessagesDelayed || 0),
          CreatedTimestamp: attrs.CreatedTimestamp ? new Date(Number(attrs.CreatedTimestamp) * 1000).toISOString() : undefined,
          VisibilityTimeout: attrs.VisibilityTimeout || '30',
          MessageRetentionPeriod: attrs.MessageRetentionPeriod || '345600',
          MaximumMessageSize: attrs.MaximumMessageSize || '262144',
          DelaySeconds: attrs.DelaySeconds || '0',
          ReceiveMessageWaitTimeSeconds: attrs.ReceiveMessageWaitTimeSeconds || '0',
          Policy: attrs.Policy || '',
          RedrivePolicy: attrs.RedrivePolicy || '',
          Type: name.endsWith('.fifo') ? 'FIFO' : 'Standard',
        };
      });
      setQueues(list);
      if (list.length > 0 && !selectedItems.length) {
        setSelectedItems([list[0]]);
      } else if (selectedItems.length > 0) {
        const refreshed = list.find((item) => item.QueueName === selectedItems[0].QueueName);
        setSelectedItems(refreshed ? [refreshed] : []);
      }
    } catch (err: any) {
      console.error(err);
      setActionAlert({ type: 'error', message: err.message || 'Failed to load SQS queues.' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateQueue = async () => {
    if (!newQueueName.trim()) return;
    let finalName = newQueueName.trim();
    if (isFifo && !finalName.endsWith('.fifo')) {
      finalName += '.fifo';
    }
    setCreating(true);
    setActionAlert(null);
    try {
      const timeout = visibilityTimeoutInput ? parseInt(visibilityTimeoutInput, 10) : 30;
      await createSqsQueue(finalName, isFifo, timeout);
      setActionAlert({ type: 'success', message: `Queue "${finalName}" created successfully.` });
      setCreateModalOpen(false);
      setNewQueueName('');
      setIsFifo(false);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to create queue.' });
    } finally {
      setCreating(false);
    }
  };

  const handlePurgeQueue = async () => {
    if (!activeQueue) return;
    setPurging(true);
    setActionAlert(null);
    try {
      await purgeSqsQueue(activeQueue.QueueName);
      setActionAlert({ type: 'success', message: `Queue "${activeQueue.QueueName}" purged successfully.` });
      setPurgeModalOpen(false);
      setReceivedMessages([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to purge queue.' });
    } finally {
      setPurging(false);
    }
  };

  const handleDeleteQueue = async () => {
    if (!activeQueue) return;
    setDeletingQueue(true);
    setActionAlert(null);
    try {
      await deleteSqsQueue(activeQueue.QueueName);
      setActionAlert({ type: 'success', message: `Queue "${activeQueue.QueueName}" deleted.` });
      setDeleteModalOpen(false);
      setSelectedItems([]);
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete queue.' });
    } finally {
      setDeletingQueue(false);
    }
  };

  const handleSendMessage = async () => {
    if (!activeQueue) return;
    const generation = selectionGeneration.current;
    setSending(true);
    setSendResult(null);
    try {
      const delay = sendDelay ? parseInt(sendDelay, 10) : undefined;
      const res = await sendSqsMessage(
        activeQueue.QueueName,
        sendBody,
        delay,
        activeQueue.Type === 'FIFO' ? messageGroupId : undefined,
        activeQueue.Type === 'FIFO' && messageDeduplicationId ? messageDeduplicationId : undefined
      );
      if (generation !== selectionGeneration.current) return;
      setSendResult(res);
      setActionAlert({ type: 'success', message: `Message published to "${activeQueue.QueueName}" (ID: ${res.message_id || 'OK'}).` });
      await loadData();
    } catch (err: any) {
      if (generation === selectionGeneration.current) setActionAlert({ type: 'error', message: err.message || 'Failed to send message.' });
    } finally {
      if (generation === selectionGeneration.current) setSending(false);
    }
  };

  const handlePollMessages = async () => {
    if (!activeQueue) return;
    const generation = selectionGeneration.current;
    setPolling(true);
    try {
      const maxNum = maxReceiveCount ? parseInt(maxReceiveCount, 10) : 5;
      const visTimeout = pollVisibilityTimeout ? parseInt(pollVisibilityTimeout, 10) : 0;
      const waitTime = pollWaitTime ? parseInt(pollWaitTime, 10) : 0;
      const res = await receiveSqsMessages(activeQueue.QueueName, maxNum, visTimeout, waitTime);
      if (generation !== selectionGeneration.current) return;
      setReceivedMessages(res.messages || []);
      if (!res.messages || res.messages.length === 0) {
        setActionAlert({ type: 'info', message: `Polled queue "${activeQueue.QueueName}". No messages available.` });
      } else {
        setActionAlert({ type: 'success', message: `Received ${res.messages.length} message(s) from "${activeQueue.QueueName}".` });
      }
    } catch (err: any) {
      if (generation === selectionGeneration.current) setActionAlert({ type: 'error', message: err.message || 'Failed to poll messages.' });
    } finally {
      if (generation === selectionGeneration.current) setPolling(false);
    }
  };

  const handleDeleteMessage = async (msg: SqsMessageItem) => {
    if (!activeQueue) return;
    setDeletingMessageId(msg.MessageId);
    try {
      await deleteSqsMessage(activeQueue.QueueName, msg.ReceiptHandle);
      setReceivedMessages((prev) => prev.filter((m) => m.MessageId !== msg.MessageId));
      if (inspectMessage?.MessageId === msg.MessageId) {
        setInspectMessage(null);
      }
      setActionAlert({ type: 'success', message: `Message ${msg.MessageId.substring(0, 8)}... deleted from queue.` });
      await loadData();
    } catch (err: any) {
      setActionAlert({ type: 'error', message: err.message || 'Failed to delete message.' });
    } finally {
      setDeletingMessageId(null);
    }
  };

  const filteredQueues = queues.filter((q) =>
    q.QueueName.toLowerCase().includes(filterText.toLowerCase())
  );

  return (
    <SpaceBetween size="l">
      {actionAlert && (
        <Alert
          type={actionAlert.type}
          dismissible
          onDismiss={() => setActionAlert(null)}
          header={actionAlert.type === 'error' ? 'SQS Operation Error' : 'SQS Notification'}
        >
          {actionAlert.message}
        </Alert>
      )}

      <Table
        header={
          <Header
            variant="h1"
            counter={`(${queues.length})`}
            description="Fully managed message queuing service for decoupling microservices, distributed systems, and serverless applications."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button iconName="refresh" onClick={loadData} loading={loading}>
                  Refresh
                </Button>
                <Button
                  disabled={!activeQueue}
                  iconName="status-warning"
                  onClick={() => setPurgeModalOpen(true)}
                >
                  Purge queue
                </Button>
                <Button
                  disabled={!activeQueue}
                  iconName="remove"
                  onClick={() => setDeleteModalOpen(true)}
                >
                  Delete queue
                </Button>
                <Button
                  variant="primary"
                  iconName="add-plus"
                  onClick={() => setCreateModalOpen(true)}
                >
                  Create queue
                </Button>
              </SpaceBetween>
            }
          >
            Queues
          </Header>
        }
        columnDefinitions={[
          {
            id: 'name',
            header: 'Queue name',
            cell: (item) => (
              <Button variant="inline-link" onClick={() => { setSelectedItems([item]); navigateResource(item); }}>
                <strong>{item.QueueName}</strong>
              </Button>
            ),
            sortingField: 'QueueName',
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
            id: 'messages',
            header: 'Messages available',
            cell: (item) => item.ApproximateNumberOfMessages ?? 0,
          },
          {
            id: 'inFlight',
            header: 'In flight',
            cell: (item) => item.ApproximateNumberOfMessagesNotVisible ?? 0,
          },
          {
            id: 'delayed',
            header: 'Delayed',
            cell: (item) => item.ApproximateNumberOfMessagesDelayed ?? 0,
          },
          {
            id: 'created',
            header: 'Created',
            cell: (item) => item.CreatedTimestamp ? item.CreatedTimestamp.split('T')[0] : 'N/A',
          },
        ]}
        items={filteredQueues}
        loading={loading}
        loadingText="Loading SQS queues..."
        selectionType="single"
        trackBy="QueueName"
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => { setSelectedItems(detail.selectedItems); if (detail.selectedItems[0]) navigateResource(detail.selectedItems[0]); }}
        filter={
          <TextFilter
            filteringText={filterText}
            filteringPlaceholder="Filter queues by name..."
            onChange={({ detail }) => setFilterText(detail.filteringText)}
          />
        }
        empty={
          <Box textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
            <SpaceBetween size="m">
              <b>No queues found</b>
              <p>Create an SQS standard or FIFO queue to begin producing and consuming messages.</p>
              <Button variant="primary" onClick={() => setCreateModalOpen(true)}>
                Create queue
              </Button>
            </SpaceBetween>
          </Box>
        }
      />

      {requested && !loading && !activeQueue && <Alert type="warning">Requested queue was not found. Select an available resource.</Alert>}
      {activeQueue && (
        <Container
          header={
            <Header
              variant="h2"
              description={`URL: ${activeQueue.QueueUrl}`}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Badge color={activeQueue.Type === 'FIFO' ? 'blue' : 'grey'}>{activeQueue.Type}</Badge>
                </SpaceBetween>
              }
            >
              Queue: {activeQueue.QueueName}
            </Header>
          }
        >
          <QueueDeliveryOverview queue={activeQueue} onNavigate={onNavigate} />
          <Tabs
            activeTabId={activeTabId}
            onChange={({ detail }) => { setActiveTabId(detail.activeTabId); navigateResource(activeQueue, detail.activeTabId); }}
            tabs={[
              {
                id: 'send-receive',
                label: 'Send and receive messages',
                content: (
                  <SpaceBetween size="l">
                    <Grid gridDefinition={[{ colspan: { default: 12, l: 6 } }, { colspan: { default: 12, l: 6 } }]}>
                      {/* Send Message Card */}
                      <Container header={<Header variant="h3">Send message</Header>}>
                        <SpaceBetween size="m">
                          <FormField label="Message body" description="JSON or text payload for the consumer.">
                            <Textarea
                              value={sendBody}
                              onChange={({ detail }) => setSendBody(detail.value)}
                              rows={6}
                              placeholder="Enter message payload..."
                            />
                          </FormField>
                          <ColumnLayout columns={2}>
                            <FormField label="Delivery delay (seconds)" description="0 to 900 seconds">
                              <Input
                                value={sendDelay}
                                onChange={({ detail }) => setSendDelay(detail.value)}
                                type="number"
                              />
                            </FormField>
                            {activeQueue.Type === 'FIFO' && (
                              <FormField label="Message group ID" description="Required for FIFO queues">
                                <Input
                                  value={messageGroupId}
                                  onChange={({ detail }) => setMessageGroupId(detail.value)}
                                  placeholder="e.g. order-processing"
                                />
                              </FormField>
                            )}
                          </ColumnLayout>
                          {activeQueue.Type === 'FIFO' && (
                            <FormField label="Deduplication ID" description="Optional if Content-Based Deduplication is on">
                              <Input
                                value={messageDeduplicationId}
                                onChange={({ detail }) => setMessageDeduplicationId(detail.value)}
                                placeholder="e.g. dedup-tx-1002"
                              />
                            </FormField>
                          )}
                          <Button
                            variant="primary"
                            iconName="envelope"
                            onClick={handleSendMessage}
                            loading={sending}
                          >
                            Send message
                          </Button>
                          {sendResult && (
                            <Alert type="success" header="Message Delivered">
                              Message ID: <code>{sendResult.message_id}</code>
                              {sendResult.sequence_number && (
                                <div>Sequence: <code>{sendResult.sequence_number}</code></div>
                              )}
                            </Alert>
                          )}
                        </SpaceBetween>
                      </Container>

                      {/* Receive Messages Card */}
                      <Container header={<Header variant="h3">Receive messages</Header>}>
                        <SpaceBetween size="m">
                          <ColumnLayout columns={3}>
                            <FormField label="Max messages" description="1 to 10">
                              <Input
                                value={maxReceiveCount}
                                onChange={({ detail }) => setMaxReceiveCount(detail.value)}
                                type="number"
                              />
                            </FormField>
                            <FormField label="Visibility timeout (s)" description="0 makes messages immediately visible again; receiving still increments the receive count.">
                              <Input
                                value={pollVisibilityTimeout}
                                onChange={({ detail }) => setPollVisibilityTimeout(detail.value)}
                                type="number"
                              />
                            </FormField>
                            <FormField label="Wait time (s)" description="0-20 (Long polling)">
                              <Input
                                value={pollWaitTime}
                                onChange={({ detail }) => setPollWaitTime(detail.value)}
                                type="number"
                              />
                            </FormField>
                          </ColumnLayout>

                          <Button
                            iconName="download"
                            onClick={handlePollMessages}
                            loading={polling}
                          >
                            Poll for messages
                          </Button>

                          <Table
                            header={<Header variant="h3" counter={`(${receivedMessages.length})`}>Received Messages</Header>}
                            columnDefinitions={[
                              {
                                id: 'id',
                                header: 'Message ID',
                                cell: (msg) => (
                                  <Button variant="inline-link" onClick={() => setInspectMessage(msg)}>
                                    {msg.MessageId ? `${msg.MessageId.substring(0, 12)}...` : 'Unknown'}
                                  </Button>
                                ),
                              },
                              {
                                id: 'body',
                                header: 'Body Preview',
                                cell: (msg) => (
                                  <code style={{ fontSize: '11px' }}>
                                    {msg.Body ? (msg.Body.length > 35 ? `${msg.Body.substring(0, 35)}...` : msg.Body) : ''}
                                  </code>
                                ),
                              },
                              {
                                id: 'actions',
                                header: 'Action',
                                cell: (msg) => (
                                  <Button
                                    iconName="remove"
                                    onClick={() => handleDeleteMessage(msg)}
                                    loading={deletingMessageId === msg.MessageId}
                                  >
                                    Delete
                                  </Button>
                                ),
                              },
                            ]}
                            items={receivedMessages}
                            empty={<Box textAlign="center" color="inherit">No messages in local peek buffer. Click "Poll for messages".</Box>}
                          />
                        </SpaceBetween>
                      </Container>
                    </Grid>
                  </SpaceBetween>
                ),
              },
              {
                id: 'attributes',
                label: 'Queue attributes & configuration',
                content: (
                  <SpaceBetween size="l">
                    <KeyValuePairs
                      columns={3}
                      items={[
                        { label: 'Queue URL', value: activeQueue.QueueUrl },
                        { label: 'Queue ARN', value: activeQueue.QueueArn || 'Not reported' },
                        { label: 'Queue Type', value: activeQueue.Type || 'Standard' },
                        { label: 'Visibility timeout', value: `${activeQueue.VisibilityTimeout ?? 30} seconds` },
                        { label: 'Message retention period', value: `${Number(activeQueue.MessageRetentionPeriod || 345600) / 86400} days (${activeQueue.MessageRetentionPeriod || 345600}s)` },
                        { label: 'Maximum message size', value: `${Number(activeQueue.MaximumMessageSize || 262144) / 1024} KB` },
                        { label: 'Delivery delay', value: `${activeQueue.DelaySeconds || 0} seconds` },
                        { label: 'Receive message wait time', value: `${activeQueue.ReceiveMessageWaitTimeSeconds || 0} seconds (Long polling)` },
                        { label: 'Messages available', value: String(activeQueue.ApproximateNumberOfMessages ?? 0) },
                        { label: 'Messages in flight', value: String(activeQueue.ApproximateNumberOfMessagesNotVisible ?? 0) },
                        { label: 'Messages delayed', value: String(activeQueue.ApproximateNumberOfMessagesDelayed ?? 0) },
                      ]}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'dlq',
                label: 'Dead-letter queue (DLQ)',
                content: (
                  <SpaceBetween size="m">
                    <Alert header="Dead-Letter Queue Redrive">
                      When enabled, unconsumed messages exceeding the maximum receive count are redirected to a designated DLQ for debugging and redrive processing.
                    </Alert>
                    <KeyValuePairs
                      columns={2}
                      items={[
                        {
                          label: 'DLQ Redrive Status',
                          value: activeQueue.RedrivePolicy ? (
                            <StatusIndicator type="success">Configured</StatusIndicator>
                          ) : (
                            <StatusIndicator type="stopped">Disabled</StatusIndicator>
                          ),
                        },
                        {
                          label: 'Redrive Policy Details',
                          value: activeQueue.RedrivePolicy || 'None configured on this queue.',
                        },
                      ]}
                    />
                  </SpaceBetween>
                ),
              },
              {
                id: 'policy',
                label: 'Access policy & permissions',
                content: (
                  <SpaceBetween size="m">
                    <Header variant="h3">Queue Access Policy (JSON)</Header>
                    <PolicySummary document={activeQueue.Policy || undefined} />
                    <textarea
                      readOnly
                      rows={8}
                      value={
                        activeQueue.Policy || 'No access policy reported for this queue.'
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

      {/* Message Inspection Drawer / Modal */}
      {inspectMessage && (
        <Modal
          visible={true}
          onDismiss={() => setInspectMessage(null)}
          header={`Message Details: ${inspectMessage.MessageId}`}
          closeAriaLabel="Close modal"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="primary"
                  iconName="remove"
                  onClick={() => handleDeleteMessage(inspectMessage)}
                  loading={deletingMessageId === inspectMessage.MessageId}
                >
                  Delete message from queue
                </Button>
                <Button variant="link" onClick={() => setInspectMessage(null)}>
                  Close
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <SpaceBetween size="m">
            <KeyValuePairs
              columns={2}
              items={[
                { label: 'Message ID', value: inspectMessage.MessageId },
                { label: 'MD5 Of Body', value: inspectMessage.MD5OfBody },
              ]}
            />
            <FormField label="Raw Message Body">
              <textarea
                readOnly
                rows={6}
                value={inspectMessage.Body}
                style={{
                  width: '100%',
                  fontFamily: 'monospace',
                  background: '#1b2a3a',
                  color: '#fff',
                  padding: '8px',
                  borderRadius: '4px',
                }}
              />
            </FormField>
            {inspectMessage.Attributes && (
              <FormField label="System Attributes">
                <pre style={{ fontSize: '11px', background: '#0e1823', padding: '8px', borderRadius: '4px' }}>
                  {JSON.stringify(inspectMessage.Attributes, null, 2)}
                </pre>
              </FormField>
            )}
          </SpaceBetween>
        </Modal>
      )}

      {/* Create Queue Modal */}
      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create SQS Queue"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleCreateQueue} loading={creating}>
                Create queue
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Queue name" description="Letters, numbers, hyphens, and underscores. (For FIFO queues, .fifo is auto-appended).">
            <Input
              value={newQueueName}
              onChange={({ detail }) => setNewQueueName(detail.value)}
              placeholder="e.g. checkout-orders"
            />
          </FormField>
          <Checkbox
            checked={isFifo}
            onChange={({ detail }) => setIsFifo(detail.checked)}
          >
            FIFO queue (First-In-First-Out, exactly-once processing)
          </Checkbox>
          <FormField label="Default visibility timeout (seconds)" description="Time message is hidden after consumer receives it (default 30s).">
            <Input
              value={visibilityTimeoutInput}
              onChange={({ detail }) => setVisibilityTimeoutInput(detail.value)}
              type="number"
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Purge Queue Modal */}
      <Modal
        visible={purgeModalOpen}
        onDismiss={() => setPurgeModalOpen(false)}
        header="Purge SQS Queue"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setPurgeModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handlePurgeQueue} loading={purging}>
                Purge all messages
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning" header="Destructive Operation">
            Are you sure you want to purge all messages in <strong>{activeQueue?.QueueName}</strong>? All pending, in-flight, and delayed messages will be permanently deleted.
          </Alert>
        </SpaceBetween>
      </Modal>

      {/* Delete Queue Modal */}
      <Modal
        visible={deleteModalOpen}
        onDismiss={() => setDeleteModalOpen(false)}
        header="Delete SQS Queue"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setDeleteModalOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleDeleteQueue} loading={deletingQueue}>
                Delete queue
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="error" header="Permanent Deletion">
            Are you sure you want to delete queue <strong>{activeQueue?.QueueName}</strong>? Any remaining messages will be lost.
          </Alert>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};

