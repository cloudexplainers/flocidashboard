import React, { useState, useEffect } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Badge from '@cloudscape-design/components/badge';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Grid from '@cloudscape-design/components/grid';
import Alert from '@cloudscape-design/components/alert';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import { fetchSettings, saveEndpoint, resetEndpoint, testConnection, resetFlociState } from '../api/client';


/** A Floci auth flag as set in the dashboard's environment: true, false or not set. */
function authFlag(value: boolean | null | undefined, onLabel: string) {
  if (value === true) return <StatusIndicator type="success">{onLabel}</StatusIndicator>;
  if (value === false) return <StatusIndicator type="info">Off</StatusIndicator>;
  return <StatusIndicator type="info">Not known (not set for the dashboard)</StatusIndicator>;
}

export const SettingsConsole: React.FC = () => {
  const [settings, setSettings] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [endpointInput, setEndpointInput] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any | null>(null);
  const [notification, setNotification] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [showResetModal, setShowResetModal] = useState(false);
  const [resetting, setResetting] = useState(false);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const data = await fetchSettings();
      setSettings(data);
      setEndpointInput(data.runtime_endpoint_url || data.endpoint_url || '');
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleSaveEndpoint = async () => {
    if (!endpointInput.trim()) return;
    setSaving(true);
    setNotification(null);
    try {
      const updated = await saveEndpoint(endpointInput.trim());
      setSettings(updated);
      setNotification({ type: 'success', message: `Runtime endpoint successfully updated to ${endpointInput.trim()}` });
    } catch (err: any) {
      setNotification({ type: 'error', message: err.message || 'Failed to save endpoint override' });
    } finally {
      setSaving(false);
    }
  };

  const handleResetEndpoint = async () => {
    setSaving(true);
    setNotification(null);
    try {
      const updated = await resetEndpoint();
      setSettings(updated);
      setEndpointInput(updated.default_endpoint_url || updated.endpoint_url || '');
      setNotification({ type: 'info', message: 'Endpoint reset to system default' });
    } catch (err: any) {
      setNotification({ type: 'error', message: err.message || 'Failed to reset endpoint' });
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setNotification(null);
    try {
      const probe = await testConnection(endpointInput.trim() || undefined);
      setTestResult(probe);
      setNotification({ type: 'success', message: 'Connection test completed successfully' });
    } catch (err: any) {
      setNotification({ type: 'error', message: err.message || 'Connection test failed' });
    } finally {
      setTesting(false);
    }
  };

  const handleFlociReset = async () => {
    setResetting(true);
    try {
      const res = await resetFlociState();
      setShowResetModal(false);
      setSettings(res);
      setNotification({ type: 'success', message: 'Local Floci state and all database mock resources have been successfully wiped and reset' });
    } catch (err: any) {
      setNotification({ type: 'error', message: err.message || 'Failed to reset Floci' });
    } finally {
      setResetting(false);
    }
  };

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h1"
            description="Manage local Floci endpoint connection, inspect security enforcement flags, and execute system resets."
            actions={
              <Button iconName="refresh" onClick={loadSettings} loading={loading}>
                Refresh
              </Button>
            }
          >
            Dashboard Settings & Endpoint Configuration
          </Header>
        }
      >
        {notification && (
          <Box margin={{ bottom: 'l' }}>
            <Alert
              type={notification.type}
              dismissible
              onDismiss={() => setNotification(null)}
            >
              {notification.message}
            </Alert>
          </Box>
        )}

        <Grid gridDefinition={[{ colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }, { colspan: { default: 12, s: 4 } }]}>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Active Endpoint URL</Box>
            <Box variant="h2" color="text-status-info">
              <code>{settings.endpoint_url || 'http://localhost:4566'}</code>
            </Box>
            <Box variant="small">Source: {settings.endpoint_source || 'default'}</Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Configured Region</Box>
            <Box variant="h2" color="text-status-info">
              <Badge color="blue">{settings.region || 'us-east-1'}</Badge>
            </Box>
          </Box>
          <Box padding="m" textAlign="center">
            <Box variant="awsui-key-label">Credential Source</Box>
            <Box variant="h2" color="text-status-info">
              <Badge color="green">{settings.credential_source || 'session'}</Badge>
            </Box>
          </Box>
        </Grid>
      </Container>

      {/* Endpoint Configuration */}
      <Container
        header={
          <Header
            variant="h2"
            description="Direct the Floci Dashboard to a custom local daemon or remote Floci instance."
          >
            Endpoint Override
          </Header>
        }
      >
        <SpaceBetween size="m">
          <FormField
            label="Floci Endpoint URL"
            description="Default: http://localhost:4566. Enter an http/https URL pointing to your local Floci service."
          >
            <Input
              value={endpointInput}
              onChange={({ detail }) => setEndpointInput(detail.value)}
              placeholder="http://localhost:4566"
            />
          </FormField>

          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="primary" onClick={handleSaveEndpoint} loading={saving}>
              Save Endpoint Override
            </Button>
            <Button onClick={handleTestConnection} loading={testing}>
              Test Connection
            </Button>
            <Button onClick={handleResetEndpoint} loading={saving}>
              Reset to Default
            </Button>
          </SpaceBetween>

          {testResult && (
            <Container
              header={
                <Header variant="h3">
                  <StatusIndicator type={!testResult.health?.ok ? 'error' : testResult.identity_resolved === true ? 'success' : 'warning'}>
                    Connection test results
                  </StatusIndicator>
                </Header>
              }
            >
              <KeyValuePairs
                columns={2}
                items={[
                  { label: 'Endpoint Tested', value: testResult.endpoint_url },
                  { label: 'Target Region', value: testResult.region },
                  { label: 'Health status', value: testResult.health?.ok ? <StatusIndicator type="success">Healthy</StatusIndicator> : <StatusIndicator type="error">{testResult.health?.error || 'Unreachable'}</StatusIndicator> },
                  { label: 'Resolved account ID', value: testResult.identity?.Account || (testResult.identity_error ? `Not resolved: ${testResult.identity_error}` : '-') },
                  { label: 'Resolved caller ARN', value: testResult.identity?.Arn || '-' },
                  ...(testResult.identity_resolved === false ? [{ label: 'Identity source', value: 'Local hint only. sts:GetCallerIdentity failed, so the account and ARN above are not confirmed.' }] : []),
                ]}
              />
            </Container>
          )}
        </SpaceBetween>
      </Container>

      {/* Security Enforcement Flags */}
      <Container
        header={
          <Header
            variant="h2"
            description="Floci doesn't report its configuration. These are the values set in the dashboard's own environment; they match Floci only when the same variables are passed to both. This project's Compose file sets them for Floci only, so they show as not known there."
          >
            Service Authentication & Security Flags
          </Header>
        }
      >
        <KeyValuePairs
          columns={3}
          items={[
            {
              label: 'S3 Authentication Enforcement',
              value: authFlag(settings.service_auth?.s3_enforce_auth, 'Enforced'),
            },
            {
              label: 'IAM Policy Enforcement',
              value: authFlag(settings.service_auth?.iam_enforcement, 'Enforced'),
            },
            {
              label: 'SigV4 Signature Validation',
              value: authFlag(settings.service_auth?.validate_signatures, 'Strict'),
            },
          ]}
        />
      </Container>

      {/* System Factory Reset */}
      <Container
        header={
          <Header
            variant="h2"
            description="Cleanly flush all in-memory and persisted resources across all 65 AWS mock services."
          >
            System Factory Reset
          </Header>
        }
      >
        <SpaceBetween size="m">
          <p style={{ margin: 0, color: '#879596', lineHeight: '1.5' }}>
            Performing a Floci reset will delete all created S3 buckets, DynamoDB tables, IAM users, Lambda functions, SQS queues, and other resources, restoring the environment to a pristine state.
          </p>
          <Button variant="normal" iconName="delete-marker" onClick={() => setShowResetModal(true)}>
            Reset Floci Database State
          </Button>
        </SpaceBetween>
      </Container>

      {/* Reset Confirmation Modal */}
      <Modal
        visible={showResetModal}
        onDismiss={() => setShowResetModal(false)}
        header="Confirm Floci Database Reset"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowResetModal(false)}>
                Cancel
              </Button>
              <Button variant="primary" loading={resetting} onClick={handleFlociReset}>
                Confirm Wipe & Reset
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="warning">
          <strong>Warning: </strong> This action is irreversible. All local AWS resources created during your sessions will be deleted.
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};
