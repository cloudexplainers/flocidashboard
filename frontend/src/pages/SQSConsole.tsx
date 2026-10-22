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
import Tiles from '@cloudscape-design/components/tiles';
import ConfirmActionModal from '../components/ConfirmActionModal';
import { formatAwsDate } from '../format';
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
  RedriveAllowPolicy?: string;
  Type?: string;
  ContentBasedDeduplication?: string;
  SqsManagedSseEnabled?: string;
  KmsMasterKeyId?: string;
  DeadLetterSourceQueues?: string[];
}

/** Seconds as the SQS console shows durations ("30 seconds", "4 days"). */
export function formatSqsDuration(value?: string | number): string {
  if (value === undefined || value === '') return '-';
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return String(value);
  const units: Array<[number, string]> = [[86400, 'day'], [3600, 'hour'], [60, 'minute']];
  for (const [size, unit] of units) {
    if (seconds >= size && seconds % size === 0) return `${seconds / size} ${unit}${seconds / size === 1 ? '' : 's'}`;
  }
  return `${seconds} second${seconds === 1 ? '' : 's'}`;
}

function describeEncryption(queue: QueueItem): string {
  if (queue.KmsMasterKeyId) return `SSE-KMS (${queue.KmsMasterKeyId})`;
  if (queue.SqsManagedSseEnabled === 'true') return 'Amazon SQS key (SSE-SQS)';
  if (queue.SqsManagedSseEnabled === 'false') return 'Disabled';
  return '-';
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
  const [retentionInput, setRetentionInput] = useState('345600');
  const [delayInput, setDelayInput] = useState('0');
  const [waitTimeInput, setWaitTimeInput] = useState('0');
  const [contentDedup, setContentDedup] = useState(false);
  const [creating, setCreating] = useState(false);

  // Send Message state
  const [sendBody, setSendBody] = useState('');
  const [sendDelay, setSendDelay] = useState('0');
  const [messageGroupId, setMessageGroupId] = useState('');
  const [messageDeduplicationId, setMessageDeduplicationId] = useState('');
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<any>(null);

  // Receive / Poll Messages state
  const [maxReceiveCount, setMaxReceiveCount] = useState('10');
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
            Type: name.endsWith('.fifo') ? 'FIFO' : 'Standard',
          };
        }
        const name = q.QueueName || q.name || (q.QueueUrl || '').split('/').pop() || 'Unnamed';
        const attrs = q.attributes || q.Attributes || {};
        const count = (value: unknown) => (value === undefined || value === null || value === '' ? undefined : Number(value));
        return {
          QueueName: name,
          QueueUrl: q.QueueUrl || q.url || '',
          QueueArn: attrs.QueueArn || q.QueueArn || q.arn,
          ApproximateNumberOfMessages: count(attrs.ApproximateNumberOfMessages ?? q.ApproximateNumberOfMessages ?? q.approximate_messages),
          ApproximateNumberOfMessagesNotVisible: count(attrs.ApproximateNumberOfMessagesNotVisible ?? q.approximate_not_visible),
          ApproximateNumberOfMessagesDelayed: count(attrs.ApproximateNumberOfMessagesDelayed ?? q.approximate_delayed),
          CreatedTimestamp: attrs.CreatedTimestamp ? new Date(Number(attrs.CreatedTimestamp) * 1000).toISOString() : undefined,
          VisibilityTimeout: attrs.VisibilityTimeout,
          MessageRetentionPeriod: attrs.MessageRetentionPeriod,
          MaximumMessageSize: attrs.MaximumMessageSize,
          DelaySeconds: attrs.DelaySeconds,
          ReceiveMessageWaitTimeSeconds: attrs.ReceiveMessageWaitTimeSeconds,
          Policy: attrs.Policy || '',
          RedrivePolicy: attrs.RedrivePolicy || '',
          RedriveAllowPolicy: attrs.RedriveAllowPolicy || '',
          Type: attrs.FifoQueue === 'true' || q.fifo || name.endsWith('.fifo') ? 'FIFO' : 'Standard',
          ContentBasedDeduplication: attrs.ContentBasedDeduplication,
          SqsManagedSseEnabled: attrs.SqsManagedSseEnabled,
          KmsMasterKeyId: attrs.KmsMasterKeyId,
          DeadLetterSourceQueues: q.dead_letter_source_queues || [],
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
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(finalName.replace(/\.fifo$/, ''))) {
      setActionAlert({ type: 'error', message: 'A queue name can have up to 80 characters: alphanumeric characters, hyphens (-), and underscores (_).' });
      return;
    }
    setCreating(true);
    setActionAlert(null);
    try {
      const number = (value: string) => (value.trim() === '' ? undefined : Number(value));
      await createSqsQueue(finalName, isFifo, {
        visibilityTimeout: number(visibilityTimeoutInput),
        messageRetentionPeriod: number(retentionInput),
        delaySeconds: number(delayInput),
        receiveWaitTime: number(waitTimeInput),
        contentBasedDeduplication: isFifo ? contentDedup : undefined,
      });
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
    if (!sendBody) {
      setActionAlert({ type: 'error', message: 'Message body is required.' });
      return;
    }
    if (activeQueue.Type === 'FIFO' && !messageGroupId.trim()) {
      setActionAlert({ type: 'error', message: 'Message group ID is required for FIFO queues.' });
      return;
    }
    if (activeQueue.Type === 'FIFO' && activeQueue.ContentBasedDeduplication !== 'true' && !messageDeduplicationId.trim()) {
      setActionAlert({ type: 'error', message: 'Message deduplication ID is required because content-based deduplication is off for this queue.' });
      return;
    }
    const generation = selectionGeneration.current;
    setSending(true);
    setSendResult(null);
    try {
      const delay = activeQueue.Type === 'FIFO' || !sendDelay ? undefined : parseInt(sendDelay, 10);
      const res = await sendSqsMessage(
        activeQueue.QueueName,
        sendBody,
        delay,
        activeQueue.Type === 'FIFO' ? messageGroupId : undefined,
        activeQueue.Type === 'FIFO' && messageDeduplicationId ? messageDeduplicationId : undefined
      );
      if (generation !== selectionGeneration.current) return;
      setSendResult(res);
      setActionAlert({ type: 'success', message: `Your message has been sent and is ready to be received. Message ID: ${res.message_id}` });
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
      const maxNum = maxReceiveCount ? parseInt(maxReceiveCount, 10) : 10;
      if (!(maxNum >= 1 && maxNum <= 10)) throw new Error('Maximum message count must be between 1 and 10.');
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
            cell: (item) => item.Type || 'Standard',
          },
          {
            id: 'created',
            header: 'Created',
            cell: (item) => formatAwsDate(item.CreatedTimestamp),
          },
          {
            id: 'messages',
            header: 'Messages available',
            cell: (item) => item.ApproximateNumberOfMessages ?? '-',
          },
          {
            id: 'inFlight',
            header: 'Messages in flight',
            cell: (item) => item.ApproximateNumberOfMessagesNotVisible ?? '-',
          },
          {
            id: 'encryption',
            header: 'Encryption',
            cell: (item) => describeEncryption(item),
          },
          {
            id: 'dedup',
            header: 'Content-based deduplication',
            cell: (item) => (item.Type === 'FIFO' ? (item.ContentBasedDeduplication === 'true' ? 'Enabled' : 'Disabled') : '-'),
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
                <Badge color={activeQueue.Type === 'FIFO' ? 'blue' : 'grey'}>{activeQueue.Type}</Badge>
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
                          <FormField label="Message body" description="Up to the queue's maximum message size. Plain text; JSON is sent as text.">
                            <Textarea
                              value={sendBody}
                              onChange={({ detail }) => setSendBody(detail.value)}
                              rows={6}
                              placeholder="Enter message payload..."
                            />
                          </FormField>
                          <ColumnLayout columns={2}>
                            <FormField label="Delivery delay (seconds)" description={activeQueue.Type === 'FIFO' ? 'FIFO queues support only a queue-level delay; per-message delay is not allowed.' : '0 to 900 seconds'}>
                              <Input
                                value={activeQueue.Type === 'FIFO' ? '' : sendDelay}
                                disabled={activeQueue.Type === 'FIFO'}
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
                            <FormField label="Message deduplication ID" description={activeQueue.ContentBasedDeduplication === 'true' ? 'Optional: content-based deduplication is on for this queue.' : 'Required: content-based deduplication is off for this queue.'}>
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
                            <FormField label="Visibility timeout override (s)" description="0 makes messages visible again immediately (the console's default behavior). Receiving still increments ApproximateReceiveCount.">
                              <Input
                                value={pollVisibilityTimeout}
                                onChange={({ detail }) => setPollVisibilityTimeout(detail.value)}
                                type="number"
                              />
                            </FormField>
                            <FormField label="Wait time (s)" description="0 to 20. Above 0 is long polling.">
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
                            header={<Header variant="h3" counter={`(${receivedMessages.length})`}>Messages</Header>}
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
                                header: 'Body',
                                cell: (msg) => (
                                  <code style={{ fontSize: '11px' }}>
                                    {msg.Body ? (msg.Body.length > 35 ? `${msg.Body.substring(0, 35)}...` : msg.Body) : ''}
                                  </code>
                                ),
                              },
                              {
                                id: 'sent',
                                header: 'Sent',
                                cell: (msg) => (msg.Attributes?.SentTimestamp ? formatAwsDate(Number(msg.Attributes.SentTimestamp)) : '-'),
                              },
                              {
                                id: 'receives',
                                header: 'Receive count',
                                cell: (msg) => msg.Attributes?.ApproximateReceiveCount ?? '-',
                              },
                              {
                                id: 'actions',
                                header: 'Actions',
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
                            empty={<Box textAlign="center" color="inherit">No messages received. Choose "Poll for messages".</Box>}
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
                        { label: 'URL', value: activeQueue.QueueUrl },
                        { label: 'ARN', value: activeQueue.QueueArn || '-' },
                        { label: 'Type', value: activeQueue.Type || 'Standard' },
                        { label: 'Created', value: formatAwsDate(activeQueue.CreatedTimestamp) },
                        { label: 'Encryption', value: describeEncryption(activeQueue) },
                        { label: 'Content-based deduplication', value: activeQueue.Type === 'FIFO' ? (activeQueue.ContentBasedDeduplication === 'true' ? 'Enabled' : 'Disabled') : '-' },
                        { label: 'Visibility timeout', value: formatSqsDuration(activeQueue.VisibilityTimeout) },
                        { label: 'Message retention period', value: formatSqsDuration(activeQueue.MessageRetentionPeriod) },
                        { label: 'Maximum message size', value: activeQueue.MaximumMessageSize ? `${Number(activeQueue.MaximumMessageSize) / 1024} KB` : '-' },
                        { label: 'Delivery delay', value: formatSqsDuration(activeQueue.DelaySeconds) },
                        { label: 'Receive message wait time', value: formatSqsDuration(activeQueue.ReceiveMessageWaitTimeSeconds) },
                        { label: 'Messages available', value: String(activeQueue.ApproximateNumberOfMessages ?? '-') },
                        { label: 'Messages in flight', value: String(activeQueue.ApproximateNumberOfMessagesNotVisible ?? '-') },
                        { label: 'Messages delayed', value: String(activeQueue.ApproximateNumberOfMessagesDelayed ?? '-') },
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
                    <Box>A dead-letter queue receives messages that consumers failed to process after the maximum number of receives.</Box>
                    {(() => {
                      let redrive: any = null;
                      try { redrive = activeQueue.RedrivePolicy ? JSON.parse(activeQueue.RedrivePolicy) : null; } catch { redrive = null; }
                      let allow: any = null;
                      try { allow = activeQueue.RedriveAllowPolicy ? JSON.parse(activeQueue.RedriveAllowPolicy) : null; } catch { allow = null; }
                      return (
                        <KeyValuePairs
                          columns={2}
                          items={[
                            { label: 'Dead-letter queue', value: redrive ? <StatusIndicator type="success">Enabled</StatusIndicator> : <StatusIndicator type="stopped">Disabled</StatusIndicator> },
                            { label: 'Dead-letter queue ARN', value: redrive?.deadLetterTargetArn || '-' },
                            { label: 'Maximum receives', value: redrive?.maxReceiveCount ?? '-' },
                            { label: 'Redrive allow policy', value: allow ? `${allow.redrivePermission}${allow.sourceQueueArns ? `: ${allow.sourceQueueArns.join(', ')}` : ''}` : 'Not set (allow all)' },
                            { label: 'Source queues using this queue as a DLQ', value: activeQueue.DeadLetterSourceQueues?.length ? activeQueue.DeadLetterSourceQueues.join(', ') : '-' },
                          ]}
                        />
                      );
                    })()}
                  </SpaceBetween>
                ),
              },
              {
                id: 'policy',
                label: 'Access policy & permissions',
                content: (
                  <SpaceBetween size="m">
                    <Header variant="h3" description="Defines who can access the queue.">Access policy</Header>
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
          header={`Message: ${inspectMessage.MessageId}`}
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
                  Delete
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
                { label: 'MD5 of message body', value: inspectMessage.MD5OfBody },
                { label: 'Sent', value: inspectMessage.Attributes?.SentTimestamp ? formatAwsDate(Number(inspectMessage.Attributes.SentTimestamp)) : '-' },
                { label: 'First received', value: inspectMessage.Attributes?.ApproximateFirstReceiveTimestamp ? formatAwsDate(Number(inspectMessage.Attributes.ApproximateFirstReceiveTimestamp)) : '-' },
                { label: 'Receive count', value: inspectMessage.Attributes?.ApproximateReceiveCount ?? '-' },
                { label: 'Message group ID', value: inspectMessage.Attributes?.MessageGroupId || '-' },
              ]}
            />
            <FormField label="Body">
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
              <FormField label="Attributes">
                <pre style={{ fontSize: '11px', background: '#0e1823', padding: '8px', borderRadius: '4px' }}>
                  {JSON.stringify(inspectMessage.Attributes, null, 2)}
                </pre>
              </FormField>
            )}
          </SpaceBetween>
        </Modal>
      )}

      <Modal
        visible={createModalOpen}
        onDismiss={() => setCreateModalOpen(false)}
        header="Create queue"
        size="large"
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateModalOpen(false)}>Cancel</Button>
              <Button variant="primary" onClick={handleCreateQueue} loading={creating} disabled={!newQueueName.trim()}>Create queue</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Type" description="You can't change the queue type after you create a queue.">
            <Tiles
              value={isFifo ? 'fifo' : 'standard'}
              onChange={({ detail }) => setIsFifo(detail.value === 'fifo')}
              items={[
                { value: 'standard', label: 'Standard', description: 'At-least-once delivery, message ordering isn\'t preserved.' },
                { value: 'fifo', label: 'FIFO', description: 'First-in-first-out delivery, message ordering is preserved. Exactly-once processing.' },
              ]}
            />
          </FormField>
          <FormField label="Name" constraintText={`A queue name is case-sensitive and can have up to 80 characters. You can use alphanumeric characters, hyphens (-), and underscores (_).${isFifo ? ' The name of a FIFO queue must end with the .fifo suffix (added for you).' : ''}`}>
            <Input value={newQueueName} onChange={({ detail }) => setNewQueueName(detail.value)} placeholder={isFifo ? 'MyQueue.fifo' : 'MyQueue'} />
          </FormField>
          <ColumnLayout columns={2}>
            <FormField label="Visibility timeout (seconds)" constraintText="Should be between 0 seconds and 12 hours (43200).">
              <Input value={visibilityTimeoutInput} onChange={({ detail }) => setVisibilityTimeoutInput(detail.value)} type="number" />
            </FormField>
            <FormField label="Message retention period (seconds)" constraintText="Should be between 1 minute (60) and 14 days (1209600).">
              <Input value={retentionInput} onChange={({ detail }) => setRetentionInput(detail.value)} type="number" />
            </FormField>
            <FormField label="Delivery delay (seconds)" constraintText="Should be between 0 seconds and 15 minutes (900).">
              <Input value={delayInput} onChange={({ detail }) => setDelayInput(detail.value)} type="number" />
            </FormField>
            <FormField label="Receive message wait time (seconds)" constraintText="Should be between 0 and 20 seconds.">
              <Input value={waitTimeInput} onChange={({ detail }) => setWaitTimeInput(detail.value)} type="number" />
            </FormField>
          </ColumnLayout>
          {isFifo && (
            <Checkbox checked={contentDedup} onChange={({ detail }) => setContentDedup(detail.checked)} description="SQS uses a SHA-256 hash of the message body as the deduplication ID, so senders don't have to provide one.">
              Content-based deduplication
            </Checkbox>
          )}
        </SpaceBetween>
      </Modal>

      <ConfirmActionModal
        visible={purgeModalOpen}
        header={`Purge queue: ${activeQueue?.QueueName}`}
        confirmLabel="Purge"
        confirmationText="purge"
        loading={purging}
        onConfirm={handlePurgeQueue}
        onDismiss={() => setPurgeModalOpen(false)}
      >
        Purging deletes all messages in the queue, including in-flight and delayed messages, and can't be undone. SQS allows one purge every 60 seconds per queue.
      </ConfirmActionModal>

      <ConfirmActionModal
        visible={deleteModalOpen}
        header={`Delete queue: ${activeQueue?.QueueName}`}
        confirmLabel="Delete"
        confirmationText="confirm"
        loading={deletingQueue}
        onConfirm={handleDeleteQueue}
        onDismiss={() => setDeleteModalOpen(false)}
      >
        Deleting this queue also deletes the messages in it. Lambda event source mappings or SNS subscriptions that use the queue stop working.
      </ConfirmActionModal>
    </SpaceBetween>
  );
};
