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
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import TextFilter from '@cloudscape-design/components/text-filter';
import Tabs from '@cloudscape-design/components/tabs';
import Toggle from '@cloudscape-design/components/toggle';
import {
  fetchInventory,
  verifySesEmailIdentity,
  verifySesDomainIdentity,
  deleteSesIdentity,
  sendSesEmail,
  createSesTemplate,
  renderSesTemplate,
  deleteSesTemplate,
  createSesConfigurationSet,
  updateSesSendingEnabled,
  clearSesMailbox,
} from '../api/client';

interface SESConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
}

export const SESConsole: React.FC<SESConsoleProps> = ({ activeTab, onTabChange }) => {
  const [data, setData] = useState<any>({
    identities: [],
    templates: [],
    configuration_sets: [],
    messages: [],
    sending_enabled: true,
  });
  const [loading, setLoading] = useState(true);
  const [filterText, setFilterText] = useState('');
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Selected Identity
  const [selectedIdentities, setSelectedIdentities] = useState<any[]>([]);

  // Verify Identity Modals
  const [verifyEmailOpen, setVerifyEmailOpen] = useState(false);
  const [newEmail, setNewEmail] = useState('');
  const [verifyingEmail, setVerifyingEmail] = useState(false);

  const [verifyDomainOpen, setVerifyDomainOpen] = useState(false);
  const [newDomain, setNewDomain] = useState('');
  const [verifyingDomain, setVerifyingDomain] = useState(false);

  // Send Email State
  const [sendSource, setSendSource] = useState('notifications@example.com');
  const [sendTo, setSendTo] = useState('user@example.com');
  const [sendSubject, setSendSubject] = useState('Welcome to Floci Cloud');
  const [sendTextBody, setSendTextBody] = useState('Hello! This is a test email sent from Floci SES Workbench.');
  const [sendHtmlBody, setSendHtmlBody] = useState('<h1>Welcome!</h1><p>This is an authentic HTML email message.</p>');
  const [sendingEmail, setSendingEmail] = useState(false);

  // Create Template Modal
  const [createTemplateOpen, setCreateTemplateOpen] = useState(false);
  const [templateName, setTemplateName] = useState('');
  const [templateSubject, setTemplateSubject] = useState('Hello {{name}}!');
  const [templateHtml, setTemplateHtml] = useState('<h2>Hello {{name}},</h2><p>Your verification code is <strong>{{code}}</strong>.</p>');
  const [templateText, setTemplateText] = useState('Hello {{name}}, Your verification code is {{code}}.');
  const [creatingTemplate, setCreatingTemplate] = useState(false);

  // Render Template Modal
  const [renderTemplateOpen, setRenderTemplateOpen] = useState(false);
  const [selectedTemplateName, setSelectedTemplateName] = useState('');
  const [templateTestData, setTemplateTestData] = useState('{\n  "name": "Alice",\n  "code": "849201"\n}');
  const [renderedResult, setRenderedResult] = useState<string | null>(null);
  const [renderingTemplate, setRenderingTemplate] = useState(false);

  // Create Config Set Modal
  const [createConfigSetOpen, setCreateConfigSetOpen] = useState(false);
  const [configSetName, setConfigSetName] = useState('');
  const [creatingConfigSet, setCreatingConfigSet] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await fetchInventory('ses');
      setData(
        res || {
          identities: [],
          templates: [],
          configuration_sets: [],
          messages: [],
          sending_enabled: true,
        }
      );
      if (res?.identities?.length > 0 && selectedIdentities.length === 0) {
        setSelectedIdentities([res.identities[0]]);
        setSendSource(res.identities[0].identity || res.identities[0]);
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

  const identityList = useMemo(() => {
    const raw = data.identities || [];
    return raw.map((item: any) => {
      if (typeof item === 'string') {
        const isEmail = item.includes('@');
        return { identity: item, type: isEmail ? 'EmailAddress' : 'Domain', status: 'Success' };
      }
      return item;
    });
  }, [data.identities]);

  const filteredIdentities = useMemo(() => {
    if (!filterText) return identityList;
    return identityList.filter((i: any) =>
      (i.identity || '').toLowerCase().includes(filterText.toLowerCase())
    );
  }, [identityList, filterText]);

  // Actions
  const handleVerifyEmail = async () => {
    if (!newEmail.trim()) return;
    setVerifyingEmail(true);
    try {
      await verifySesEmailIdentity(newEmail.trim());
      setActionMessage({ type: 'success', text: `Verification email sent to "${newEmail.trim()}". Identity verified.` });
      setVerifyEmailOpen(false);
      setNewEmail('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to verify email identity' });
    } finally {
      setVerifyingEmail(false);
    }
  };

  const handleVerifyDomain = async () => {
    if (!newDomain.trim()) return;
    setVerifyingDomain(true);
    try {
      await verifySesDomainIdentity(newDomain.trim());
      setActionMessage({ type: 'success', text: `Domain "${newDomain.trim()}" verification initiated with TXT records.` });
      setVerifyDomainOpen(false);
      setNewDomain('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to verify domain' });
    } finally {
      setVerifyingDomain(false);
    }
  };

  const handleDeleteIdentity = async (identity: string) => {
    if (!confirm(`Delete verified identity "${identity}"?`)) return;
    try {
      await deleteSesIdentity(identity);
      setActionMessage({ type: 'success', text: `Identity "${identity}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete identity' });
    }
  };

  const handleSendEmail = async () => {
    if (!sendSource.trim() || !sendTo.trim() || !sendSubject.trim()) return;
    setSendingEmail(true);
    try {
      const toList = sendTo.split(',').map((s) => s.trim()).filter(Boolean);
      await sendSesEmail({
        source: sendSource.trim(),
        to_addresses: toList,
        subject: sendSubject.trim(),
        text: sendTextBody.trim() || undefined,
        html: sendHtmlBody.trim() || undefined,
      });
      setActionMessage({ type: 'success', text: `Email dispatched successfully to ${toList.join(', ')}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to send email' });
    } finally {
      setSendingEmail(false);
    }
  };

  const handleCreateTemplate = async () => {
    if (!templateName.trim() || !templateSubject.trim()) return;
    setCreatingTemplate(true);
    try {
      await createSesTemplate(templateName.trim(), templateSubject.trim(), templateHtml.trim(), templateText.trim());
      setActionMessage({ type: 'success', text: `Email Template "${templateName.trim()}" created.` });
      setCreateTemplateOpen(false);
      setTemplateName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create template' });
    } finally {
      setCreatingTemplate(false);
    }
  };

  const handleRenderTemplate = async () => {
    if (!selectedTemplateName) return;
    setRenderingTemplate(true);
    try {
      const res = await renderSesTemplate(selectedTemplateName, templateTestData);
      setRenderedResult(res.rendered_template || res.RenderedTemplate || JSON.stringify(res, null, 2));
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to render template' });
    } finally {
      setRenderingTemplate(false);
    }
  };

  const handleDeleteTemplate = async (name: string) => {
    try {
      await deleteSesTemplate(name);
      setActionMessage({ type: 'success', text: `Template "${name}" deleted.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to delete template' });
    }
  };

  const handleCreateConfigSet = async () => {
    if (!configSetName.trim()) return;
    setCreatingConfigSet(true);
    try {
      await createSesConfigurationSet(configSetName.trim());
      setActionMessage({ type: 'success', text: `Configuration set "${configSetName.trim()}" created.` });
      setCreateConfigSetOpen(false);
      setConfigSetName('');
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to create configuration set' });
    } finally {
      setCreatingConfigSet(false);
    }
  };

  const handleToggleSending = async (enabled: boolean) => {
    try {
      await updateSesSendingEnabled(enabled);
      setActionMessage({ type: 'success', text: `Account email sending ${enabled ? 'enabled' : 'paused'}.` });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to update sending state' });
    }
  };

  const handleClearMailbox = async () => {
    try {
      await clearSesMailbox();
      setActionMessage({ type: 'success', text: 'Simulated mailbox cleared.' });
      await loadData();
    } catch (err: any) {
      setActionMessage({ type: 'error', text: err.message || 'Failed to clear mailbox' });
    }
  };

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description="Amazon Simple Email Service (SES) is a cost-effective, flexible, and scalable email service for developers."
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            <Button variant="primary" iconName="add-plus" onClick={() => setVerifyEmailOpen(true)}>
              Verify Email Identity
            </Button>
          </SpaceBetween>
        }
      >
        Amazon Simple Email Service (SES)
      </Header>

      {actionMessage && (
        <Alert type={actionMessage.type} dismissible onDismiss={() => setActionMessage(null)}>
          {actionMessage.text}
        </Alert>
      )}

      {/* Metrics */}
      <Grid gridDefinition={[{ colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }, { colspan: { default: 12, s: 3 } }]}>
        <Container>
          <Box variant="awsui-key-label">Verified Identities</Box>
          <Box variant="awsui-value-large">{identityList.length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Email Templates</Box>
          <Box variant="awsui-value-large">{(data.templates || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Configuration Sets</Box>
          <Box variant="awsui-value-large">{(data.configuration_sets || []).length}</Box>
        </Container>
        <Container>
          <Box variant="awsui-key-label">Account Sending</Box>
          <Box variant="awsui-value-large">
            <Badge color={data.sending_enabled !== false ? 'green' : 'red'}>
              {data.sending_enabled !== false ? 'Enabled' : 'Paused'}
            </Badge>
          </Box>
        </Container>
      </Grid>

      <Tabs
        activeTabId={activeTab || 'identities'}
        onChange={({ detail }) => onTabChange && onTabChange(detail.activeTabId)}
        tabs={[
          {
            label: `Verified Identities (${identityList.length})`,
            id: 'identities',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button onClick={() => setVerifyDomainOpen(true)}>
                          Verify Domain
                        </Button>
                        <Button variant="primary" iconName="add-plus" onClick={() => setVerifyEmailOpen(true)}>
                          Verify Email Address
                        </Button>
                      </SpaceBetween>
                    }
                  >
                    Identities (Emails & Domains)
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <TextFilter
                    filteringText={filterText}
                    onChange={({ detail }) => setFilterText(detail.filteringText)}
                    filteringPlaceholder="Filter identities..."
                  />
                  <Table
                    columnDefinitions={[
                      {
                        id: 'identity',
                        header: 'Identity',
                        cell: (item: any) => <strong>{item.identity}</strong>,
                      },
                      {
                        id: 'type',
                        header: 'Identity Type',
                        cell: (item: any) => <Badge color="blue">{item.type || 'EmailAddress'}</Badge>,
                        width: 160,
                      },
                      {
                        id: 'status',
                        header: 'Status',
                        cell: (item: any) => <StatusIndicator type="success">{item.status || 'Success'}</StatusIndicator>,
                        width: 140,
                      },
                      {
                        id: 'sending',
                        header: 'Sending Status',
                        cell: () => <Badge color="green">Enabled</Badge>,
                        width: 130,
                      },
                      {
                        id: 'actions',
                        header: 'Actions',
                        cell: (item: any) => (
                          <Button
                            iconName="remove"
                            onClick={() => handleDeleteIdentity(item.identity)}
                          >
                            Delete
                          </Button>
                        ),
                        width: 110,
                      },
                    ]}
                    items={filteredIdentities}
                    empty={<Box textAlign="center">No verified email or domain identities.</Box>}
                  />
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: 'Send Email Workbench',
            id: 'send',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" loading={sendingEmail} iconName="upload" onClick={handleSendEmail}>
                        Send Email
                      </Button>
                    }
                  >
                    Compose Email
                  </Header>
                }
              >
                <SpaceBetween size="m">
                  <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                    <FormField label="From (Verified Source Address)">
                      <Input value={sendSource} onChange={({ detail }) => setSendSource(detail.value)} />
                    </FormField>
                    <FormField label="To (Destination Addresses, comma separated)">
                      <Input value={sendTo} onChange={({ detail }) => setSendTo(detail.value)} />
                    </FormField>
                  </Grid>

                  <FormField label="Subject Line">
                    <Input value={sendSubject} onChange={({ detail }) => setSendSubject(detail.value)} />
                  </FormField>

                  <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                    <FormField label="Plain Text Content">
                      <Textarea rows={8} value={sendTextBody} onChange={({ detail }) => setSendTextBody(detail.value)} />
                    </FormField>
                    <FormField label="HTML Body">
                      <Textarea rows={8} value={sendHtmlBody} onChange={({ detail }) => setSendHtmlBody(detail.value)} />
                    </FormField>
                  </Grid>
                </SpaceBetween>
              </Container>
            ),
          },
          {
            label: `Email Templates (${(data.templates || []).length})`,
            id: 'templates',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button variant="primary" iconName="add-plus" onClick={() => setCreateTemplateOpen(true)}>
                        Create Template
                      </Button>
                    }
                  >
                    Email Templates
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'name', header: 'Template Name', cell: (i: any) => <strong>{i.TemplateName || i.name}</strong> },
                    { id: 'subject', header: 'Subject', cell: (i: any) => i.SubjectPart || i.subject || '—' },
                    { id: 'created', header: 'Created', cell: (i: any) => i.CreatedTimestamp || 'Recent', width: 140 },
                    {
                      id: 'actions',
                      header: 'Actions',
                      cell: (i: any) => (
                        <SpaceBetween direction="horizontal" size="xs">
                          <Button
                            onClick={() => {
                              setSelectedTemplateName(i.TemplateName || i.name);
                              setRenderTemplateOpen(true);
                            }}
                          >
                            Preview Render
                          </Button>
                          <Button iconName="remove" onClick={() => handleDeleteTemplate(i.TemplateName || i.name)}>
                            Delete
                          </Button>
                        </SpaceBetween>
                      ),
                      width: 220,
                    },
                  ]}
                  items={data.templates || []}
                  empty={<Box textAlign="center">No email templates created.</Box>}
                />
              </Container>
            ),
          },
          {
            label: `Configuration Sets (${(data.configuration_sets || []).length})`,
            id: 'configsets',
            content: (
              <Grid gridDefinition={[{ colspan: { default: 12, s: 7 } }, { colspan: { default: 12, s: 5 } }]}>
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button variant="primary" iconName="add-plus" onClick={() => setCreateConfigSetOpen(true)}>
                          Create Configuration Set
                        </Button>
                      }
                    >
                      Configuration Sets
                    </Header>
                  }
                >
                  <Table
                    columnDefinitions={[
                      { id: 'name', header: 'Set Name', cell: (i: any) => <strong>{i.Name || i.name}</strong> },
                      { id: 'sending', header: 'Sending', cell: () => <Badge color="green">Enabled</Badge>, width: 120 },
                    ]}
                    items={data.configuration_sets || []}
                    empty={<Box textAlign="center">No configuration sets found.</Box>}
                  />
                </Container>

                <Container header={<Header variant="h2">Account Sending Controls</Header>}>
                  <SpaceBetween size="m">
                    <Toggle
                      checked={data.sending_enabled !== false}
                      onChange={({ detail }) => handleToggleSending(detail.checked)}
                    >
                      SES Account Sending Enabled
                    </Toggle>
                    <Box color="text-status-inactive">
                      When paused, all outbound email transmission through SMTP and API is suspended.
                    </Box>
                  </SpaceBetween>
                </Container>
              </Grid>
            ),
          },
          {
            label: `Test Mailbox (${(data.messages || []).length})`,
            id: 'mailbox',
            content: (
              <Container
                header={
                  <Header
                    variant="h2"
                    actions={
                      <Button iconName="remove" onClick={handleClearMailbox}>
                        Clear Mailbox
                      </Button>
                    }
                  >
                    Simulated Inbound & Outbound Messages
                  </Header>
                }
              >
                <Table
                  columnDefinitions={[
                    { id: 'id', header: 'Message ID', cell: (i: any) => <code>{i.id || 'ses-msg-12345'}</code>, width: 220 },
                    { id: 'source', header: 'Source', cell: (i: any) => i.source || 'notifications@example.com', width: 200 },
                    { id: 'dest', header: 'Destination', cell: (i: any) => (i.destination || []).join(', ') || 'user@example.com' },
                    { id: 'subject', header: 'Subject', cell: (i: any) => <strong>{i.subject || 'Test Message'}</strong> },
                  ]}
                  items={data.messages || []}
                  empty={<Box textAlign="center">No emails in simulated mailbox. Send a message to inspect it here.</Box>}
                />
              </Container>
            ),
          },
        ]}
      />

      {/* Verify Email Modal */}
      <Modal
        visible={verifyEmailOpen}
        onDismiss={() => setVerifyEmailOpen(false)}
        header="Verify Email Identity"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setVerifyEmailOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={verifyingEmail} onClick={handleVerifyEmail}>
                Verify Identity
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Email Address" description="The email address you want to send and receive emails with.">
          <Input value={newEmail} onChange={({ detail }) => setNewEmail(detail.value)} placeholder="developer@example.com" />
        </FormField>
      </Modal>

      {/* Verify Domain Modal */}
      <Modal
        visible={verifyDomainOpen}
        onDismiss={() => setVerifyDomainOpen(false)}
        header="Verify Domain Identity"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setVerifyDomainOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={verifyingDomain} onClick={handleVerifyDomain}>
                Verify Domain
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Domain Name" description="The domain you want to verify for sending authority.">
          <Input value={newDomain} onChange={({ detail }) => setNewDomain(detail.value)} placeholder="example.com" />
        </FormField>
      </Modal>

      {/* Create Template Modal */}
      <Modal
        visible={createTemplateOpen}
        onDismiss={() => setCreateTemplateOpen(false)}
        header="Create Email Template"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateTemplateOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingTemplate} onClick={handleCreateTemplate}>
                Create Template
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Template Name">
            <Input value={templateName} onChange={({ detail }) => setTemplateName(detail.value)} placeholder="WelcomeEmailTemplate" />
          </FormField>
          <FormField label="Subject Part (Handlebars format)">
            <Input value={templateSubject} onChange={({ detail }) => setTemplateSubject(detail.value)} />
          </FormField>
          <FormField label="HTML Part">
            <Textarea rows={6} value={templateHtml} onChange={({ detail }) => setTemplateHtml(detail.value)} />
          </FormField>
          <FormField label="Text Part">
            <Textarea rows={4} value={templateText} onChange={({ detail }) => setTemplateText(detail.value)} />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Render Template Modal */}
      <Modal
        visible={renderTemplateOpen}
        onDismiss={() => setRenderTemplateOpen(false)}
        header={`Render Template "${selectedTemplateName}"`}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setRenderTemplateOpen(false)}>
                Close
              </Button>
              <Button variant="primary" loading={renderingTemplate} onClick={handleRenderTemplate}>
                Render Template
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FormField label="Template Test Data (JSON)">
            <Textarea rows={4} value={templateTestData} onChange={({ detail }) => setTemplateTestData(detail.value)} />
          </FormField>
          {renderedResult && (
            <FormField label="Rendered HTML Output">
              <Textarea rows={8} value={renderedResult} readOnly />
            </FormField>
          )}
        </SpaceBetween>
      </Modal>

      {/* Create Config Set Modal */}
      <Modal
        visible={createConfigSetOpen}
        onDismiss={() => setCreateConfigSetOpen(false)}
        header="Create Configuration Set"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setCreateConfigSetOpen(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={creatingConfigSet} onClick={handleCreateConfigSet}>
                Create Set
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <FormField label="Configuration Set Name">
          <Input value={configSetName} onChange={({ detail }) => setConfigSetName(detail.value)} placeholder="TransactionalEmails" />
        </FormField>
      </Modal>
    </SpaceBetween>
  );
};
