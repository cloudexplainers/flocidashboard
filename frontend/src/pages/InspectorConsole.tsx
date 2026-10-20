import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Table from '../components/ResourceTable';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Tabs from '@cloudscape-design/components/tabs';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import Select from '@cloudscape-design/components/select';
import FormField from '@cloudscape-design/components/form-field';
import TextFilter from '@cloudscape-design/components/text-filter';
import Grid from '@cloudscape-design/components/grid';
import {
  fetchSesMessages,
  clearSesMessages,
  fetchInspectorSqsQueues,
  fetchInspectorSqsMessages,
  fetchInspectorLogGroups,
  fetchInspectorLogEvents,
} from '../api/client';
import { CodeSnippet } from '../components/CodeSnippet';

export const InspectorConsole: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('ses');

  // SES State
  const [sesMessages, setSesMessages] = useState<any[]>([]);
  const [sesLoading, setSesLoading] = useState(false);
  const [selectedEmail, setSelectedEmail] = useState<any | null>(null);
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  const [sesFilter, setSesFilter] = useState('');

  // SQS State
  const [sqsQueues, setSqsQueues] = useState<Array<{ name: string; url: string; arn: string; available: number; in_flight: number }>>([]);
  const [selectedQueueUrl, setSelectedQueueUrl] = useState<string>('');
  const [sqsMessages, setSqsMessages] = useState<any[]>([]);
  const [sqsLoading, setSqsLoading] = useState(false);
  const [selectedSqsMessage, setSelectedSqsMessage] = useState<any | null>(null);
  const [sqsFilter, setSqsFilter] = useState('');

  // Lambda Logs State
  const [logGroups, setLogGroups] = useState<Array<{ logGroupName: string; creationTime: number; storedBytes: number }>>([]);
  const [selectedLogGroup, setSelectedLogGroup] = useState<string>('');
  const [logEvents, setLogEvents] = useState<any[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsFilter, setLogsFilter] = useState('');

  const loadSes = async () => {
    setSesLoading(true);
    try {
      const data = await fetchSesMessages();
      setSesMessages(data.messages || []);
    } catch (err) {
      console.error(err);
    } finally {
      setSesLoading(false);
    }
  };

  const handleClearSes = async () => {
    try {
      await clearSesMessages();
      setShowClearConfirm(false);
      await loadSes();
    } catch (err) {
      console.error(err);
    }
  };

  const loadSqs = async () => {
    try {
      const data = await fetchInspectorSqsQueues();
      setSqsQueues(data.queues || []);
      if (data.queues?.length > 0 && !selectedQueueUrl) {
        setSelectedQueueUrl(data.queues[0].url);
        peekSqsMessages(data.queues[0].url);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const peekSqsMessages = async (queueUrl: string) => {
    if (!queueUrl) return;
    setSqsLoading(true);
    try {
      const data = await fetchInspectorSqsMessages(queueUrl, 10);
      setSqsMessages(data.messages || []);
    } catch (err) {
      console.error(err);
    } finally {
      setSqsLoading(false);
    }
  };

  const loadLogGroups = async () => {
    try {
      const data = await fetchInspectorLogGroups();
      setLogGroups(data.log_groups || []);
      if (data.log_groups?.length > 0 && !selectedLogGroup) {
        setSelectedLogGroup(data.log_groups[0].logGroupName);
        loadLogEvents(data.log_groups[0].logGroupName);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const loadLogEvents = async (groupName: string) => {
    if (!groupName) return;
    setLogsLoading(true);
    try {
      const data = await fetchInspectorLogEvents(groupName, 50);
      setLogEvents(data.events || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLogsLoading(false);
    }
  };

  useEffect(() => {
    loadSes();
    loadSqs();
    loadLogGroups();
  }, []);

  const filteredSes = sesMessages.filter((m) => {
    const text = JSON.stringify(m).toLowerCase();
    return text.includes(sesFilter.toLowerCase());
  });

  const filteredSqs = sqsMessages.filter((m) => {
    const text = JSON.stringify(m).toLowerCase();
    return text.includes(sqsFilter.toLowerCase());
  });

  const filteredLogs = logEvents.filter((e) => {
    const text = (e.message || '').toLowerCase();
    return text.includes(logsFilter.toLowerCase());
  });

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Inspect mock SES email deliveries, peek live SQS messages, and stream CloudWatch Lambda logs offline in real time."
          >
            Local Developer Inspector Inbox
          </Header>
        }
      >
        <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Captured SES Emails</Box>
            <Box variant="h1" color="text-status-info">
              {sesMessages.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active SQS Queues</Box>
            <Box variant="h1" color="text-status-info">
              {sqsQueues.length}
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Lambda Log Groups</Box>
            <Box variant="h1" color="text-status-info">
              {logGroups.length}
            </Box>
          </Box>
        </Grid>
      </Container>

      <Tabs
        activeTabId={activeTab}
        onChange={({ detail }) => setActiveTab(detail.activeTabId)}
        tabs={[
          {
            label: `Amazon SES Mailbox (${sesMessages.length})`,
            id: 'ses',
            content: (
              <SpaceBetween size="m">
                <Table
                  header={
                    <Header
                      variant="h2"
                      description="Emails captured locally by the Floci mock SES server."
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button iconName="refresh" onClick={loadSes} loading={sesLoading}>
                            Refresh
                          </Button>
                          <Button
                            variant="normal"
                            iconName="remove"
                            disabled={sesMessages.length === 0}
                            onClick={() => setShowClearConfirm(true)}
                          >
                            Clear Mailbox
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      SES Messages ({filteredSes.length})
                    </Header>
                  }
                  filter={
                    <TextFilter
                      filteringText={sesFilter}
                      filteringPlaceholder="Search emails by sender, recipient, subject, or content..."
                      onChange={({ detail }) => setSesFilter(detail.filteringText)}
                    />
                  }
                  columnDefinitions={[
                    {
                      id: 'subject',
                      header: 'Subject',
                      cell: (item: any) => (
                        <Button variant="inline-link" onClick={() => setSelectedEmail(item)}>
                          <strong>{item.subject || item.Subject || '(No Subject)'}</strong>
                        </Button>
                      ),
                    },
                    {
                      id: 'from',
                      header: 'From (Sender)',
                      cell: (item: any) => item.from || item.From || item.source || item.Source || '—',
                    },
                    {
                      id: 'to',
                      header: 'To (Recipients)',
                      cell: (item: any) => {
                        const to = item.to || item.To || item.destination || item.Destination || [];
                        const display = Array.isArray(to) ? to.join(', ') : String(to);
                        return <Badge color="blue">{display || '—'}</Badge>;
                      },
                    },
                    {
                      id: 'timestamp',
                      header: 'Received At',
                      cell: (item: any) => item.timestamp ? new Date(item.timestamp).toLocaleString() : item.date || 'Just now',
                    },
                    {
                      id: 'actions',
                      header: 'Action',
                      cell: (item: any) => (
                        <Button variant="normal" onClick={() => setSelectedEmail(item)}>
                          View Message
                        </Button>
                      ),
                    },
                  ]}
                  items={filteredSes}
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No SES emails in mailbox</b>
                      <p>Send an email using AWS SDK `boto3.client('ses').send_email(...)` to inspect it here.</p>
                    </Box>
                  }
                />
              </SpaceBetween>
            ),
          },
          {
            label: `Amazon SQS Inbox (${sqsQueues.length} Queues)`,
            id: 'sqs',
            content: (
              <SpaceBetween size="m">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            variant="primary"
                            iconName="refresh"
                            loading={sqsLoading}
                            disabled={!selectedQueueUrl}
                            onClick={() => peekSqsMessages(selectedQueueUrl)}
                          >
                            Peek Messages
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      Select SQS Queue to Inspect
                    </Header>
                  }
                >
                  <FormField label="Target Queue">
                    <Select
                      selectedOption={
                        sqsQueues.find((q) => q.url === selectedQueueUrl)
                          ? { label: `${sqsQueues.find((q) => q.url === selectedQueueUrl)?.name} (${sqsQueues.find((q) => q.url === selectedQueueUrl)?.available} available)`, value: selectedQueueUrl }
                          : null
                      }
                      onChange={({ detail }) => {
                        setSelectedQueueUrl(detail.selectedOption.value || '');
                        if (detail.selectedOption.value) {
                          peekSqsMessages(detail.selectedOption.value);
                        }
                      }}
                      options={sqsQueues.map((q) => ({
                        label: `${q.name} (${q.available} msgs available, ${q.in_flight} in flight)`,
                        value: q.url,
                      }))}
                      placeholder="Choose a queue..."
                    />
                  </FormField>
                </Container>

                <Table
                  header={
                    <Header
                      variant="h3"
                      description="Messages peeked from the selected queue with VisibilityTimeout=0 (non-destructive)."
                    >
                      Queue Messages ({filteredSqs.length})
                    </Header>
                  }
                  filter={
                    <TextFilter
                      filteringText={sqsFilter}
                      filteringPlaceholder="Filter messages by body or ID..."
                      onChange={({ detail }) => setSqsFilter(detail.filteringText)}
                    />
                  }
                  columnDefinitions={[
                    {
                      id: 'id',
                      header: 'Message ID',
                      cell: (item: any) => (
                        <Button variant="inline-link" onClick={() => setSelectedSqsMessage(item)}>
                          <code>{item.MessageId || item.id || '—'}</code>
                        </Button>
                      ),
                    },
                    {
                      id: 'body',
                      header: 'Message Body',
                      cell: (item: any) => (
                        <div style={{ maxWidth: '400px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.Body || item.body || JSON.stringify(item)}
                        </div>
                      ),
                    },
                    {
                      id: 'md5',
                      header: 'MD5 of Body',
                      cell: (item: any) => <code>{item.MD5OfBody || '—'}</code>,
                    },
                    {
                      id: 'action',
                      header: 'Action',
                      cell: (item: any) => (
                        <Button variant="normal" onClick={() => setSelectedSqsMessage(item)}>
                          Inspect
                        </Button>
                      ),
                    },
                  ]}
                  items={filteredSqs}
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No messages received</b>
                      <p>Choose a queue above and click "Peek Messages" to view queued items.</p>
                    </Box>
                  }
                />
              </SpaceBetween>
            ),
          },
          {
            label: `AWS Lambda CloudWatch Logs (${logGroups.length} Groups)`,
            id: 'logs',
            content: (
              <SpaceBetween size="m">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            variant="primary"
                            iconName="refresh"
                            loading={logsLoading}
                            disabled={!selectedLogGroup}
                            onClick={() => loadLogEvents(selectedLogGroup)}
                          >
                            Stream Events
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      Lambda CloudWatch Log Groups
                    </Header>
                  }
                >
                  <FormField label="Target Log Group">
                    <Select
                      selectedOption={
                        selectedLogGroup
                          ? { label: selectedLogGroup, value: selectedLogGroup }
                          : null
                      }
                      onChange={({ detail }) => {
                        setSelectedLogGroup(detail.selectedOption.value || '');
                        if (detail.selectedOption.value) {
                          loadLogEvents(detail.selectedOption.value);
                        }
                      }}
                      options={logGroups.map((g) => ({
                        label: g.logGroupName,
                        value: g.logGroupName,
                      }))}
                      placeholder="Select a Lambda log group..."
                    />
                  </FormField>
                </Container>

                <Table
                  header={
                    <Header
                      variant="h3"
                      description="Recent log events streamed from CloudWatch."
                    >
                      Log Stream Events ({filteredLogs.length})
                    </Header>
                  }
                  filter={
                    <TextFilter
                      filteringText={logsFilter}
                      filteringPlaceholder="Search log events..."
                      onChange={({ detail }) => setLogsFilter(detail.filteringText)}
                    />
                  }
                  columnDefinitions={[
                    {
                      id: 'timestamp',
                      header: 'Timestamp',
                      cell: (item: any) => (
                        <code>{item.timestamp ? new Date(item.timestamp).toISOString() : '—'}</code>
                      ),
                    },
                    {
                      id: 'stream',
                      header: 'Log Stream',
                      cell: (item: any) => <Badge color="grey">{item.logStreamName || 'default'}</Badge>,
                    },
                    {
                      id: 'message',
                      header: 'Event Message',
                      cell: (item: any) => (
                        <div style={{ fontFamily: 'monospace', fontSize: '12px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                          {item.message}
                        </div>
                      ),
                    },
                  ]}
                  items={filteredLogs}
                  empty={
                    <Box textAlign="center" color="inherit">
                      <b>No log events found</b>
                      <p>Select a log group and invoke your Lambda function to see live CloudWatch logs.</p>
                    </Box>
                  }
                />
              </SpaceBetween>
            ),
          },
        ]}
      />

      {/* SES Email Modal */}
      <Modal
        visible={Boolean(selectedEmail)}
        onDismiss={() => setSelectedEmail(null)}
        header={`SES Email: ${selectedEmail?.subject || selectedEmail?.Subject || '(No Subject)'}`}
        size="large"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setSelectedEmail(null)}>
              Close
            </Button>
          </Box>
        }
      >
        {selectedEmail && (
          <SpaceBetween size="m">
            <div>
              <strong>From: </strong> {selectedEmail.from || selectedEmail.From || selectedEmail.source || selectedEmail.Source || '—'}
            </div>
            <div>
              <strong>To: </strong> {Array.isArray(selectedEmail.to || selectedEmail.To) ? (selectedEmail.to || selectedEmail.To).join(', ') : String(selectedEmail.to || selectedEmail.To || selectedEmail.destination || selectedEmail.Destination || '—')}
            </div>
            <div>
              <strong>Subject: </strong> {selectedEmail.subject || selectedEmail.Subject || '(No Subject)'}
            </div>
            <div>
              <strong>Date: </strong> {selectedEmail.timestamp ? new Date(selectedEmail.timestamp).toLocaleString() : selectedEmail.date || '—'}
            </div>

            <Header variant="h3">Message Body</Header>
            <Container>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '13px', lineHeight: '1.5' }}>
                {selectedEmail.body || selectedEmail.text || selectedEmail.html || selectedEmail.raw || JSON.stringify(selectedEmail, null, 2)}
              </pre>
            </Container>

            <Header variant="h3">Raw JSON Payload</Header>
            <CodeSnippet language="json" code={JSON.stringify(selectedEmail, null, 2)} />
          </SpaceBetween>
        )}
      </Modal>

      {/* SQS Message Modal */}
      <Modal
        visible={Boolean(selectedSqsMessage)}
        onDismiss={() => setSelectedSqsMessage(null)}
        header={`SQS Message: ${selectedSqsMessage?.MessageId || 'Message Detail'}`}
        size="large"
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setSelectedSqsMessage(null)}>
              Close
            </Button>
          </Box>
        }
      >
        {selectedSqsMessage && (
          <SpaceBetween size="m">
            <div>
              <strong>Message ID: </strong> <code>{selectedSqsMessage.MessageId}</code>
            </div>
            <div>
              <strong>MD5: </strong> <code>{selectedSqsMessage.MD5OfBody}</code>
            </div>
            <Header variant="h3">Message Body Content</Header>
            <CodeSnippet
              language="json"
              code={
                typeof selectedSqsMessage.Body === 'string' && selectedSqsMessage.Body.startsWith('{')
                  ? JSON.stringify(JSON.parse(selectedSqsMessage.Body), null, 2)
                  : selectedSqsMessage.Body || ''
              }
            />
            <Header variant="h3">Full Message Metadata</Header>
            <CodeSnippet language="json" code={JSON.stringify(selectedSqsMessage, null, 2)} />
          </SpaceBetween>
        )}
      </Modal>

      {/* SES Clear Mailbox Confirmation Modal */}
      <Modal
        visible={showClearConfirm}
        onDismiss={() => setShowClearConfirm(false)}
        header="Clear Local SES Mailbox"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowClearConfirm(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleClearSes}>
                Clear All Emails
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        Are you sure you want to delete all {sesMessages.length} captured emails from the local SES mailbox? This action cannot be undone.
      </Modal>
    </SpaceBetween>
  );
};
