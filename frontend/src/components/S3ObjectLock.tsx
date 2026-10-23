import { useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import Container from '@cloudscape-design/components/container';
import FormField from '@cloudscape-design/components/form-field';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Modal from '@cloudscape-design/components/modal';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import {
  fetchS3ObjectLock,
  putS3ObjectLegalHold,
  putS3ObjectLock,
  putS3ObjectRetention,
  S3ObjectHead,
  S3ObjectLockConfiguration,
} from '../api/client';
import { formatAwsDate } from '../format';
import { FlociNote } from './FlociNote';

type Notify = (type: 'success' | 'error', text: string) => void;

export const OBJECT_LOCK_NOTE = 'Floci enforces Object Lock as AWS does: a retention period or legal hold stops that object version from being deleted, compliance retention can’t be shortened or removed, and governance retention needs the bypass. Floci doesn’t stop Object Lock being turned on for a bucket without versioning, or versioning being suspended on a locked bucket; AWS refuses both, so this console does too.';

const MODE_LABELS: Record<string, string> = { GOVERNANCE: 'Governance', COMPLIANCE: 'Compliance' };

export function describeDefaultRetention(configuration: S3ObjectLockConfiguration | null): string {
  const retention = configuration?.Rule?.DefaultRetention;
  if (!retention) return 'Disabled';
  const period = retention.Days ? `${retention.Days} ${retention.Days === 1 ? 'day' : 'days'}` : `${retention.Years} ${retention.Years === 1 ? 'year' : 'years'}`;
  return `${MODE_LABELS[retention.Mode] || retention.Mode} · ${period}`;
}

/** Bucket Properties → Object Lock: shows the setting and the "Edit Object Lock" form. */
export function S3BucketObjectLock({ bucket, versioningStatus, onLockChange, notify }: {
  bucket: string;
  versioningStatus: string;
  onLockChange: (enabled: boolean) => void;
  notify: Notify;
}) {
  const [configuration, setConfiguration] = useState<S3ObjectLockConfiguration | null | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [retentionOn, setRetentionOn] = useState(false);
  const [mode, setMode] = useState('GOVERNANCE');
  const [period, setPeriod] = useState('1');
  const [unit, setUnit] = useState({ label: 'Days', value: 'Days' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let current = true;
    setConfiguration(undefined);
    fetchS3ObjectLock(bucket)
      .then(config => { if (current) { setConfiguration(config); onLockChange(config?.ObjectLockEnabled === 'Enabled'); } })
      .catch(() => { if (current) setConfiguration(undefined); });
    return () => { current = false; };
  }, [bucket]);

  const enabled = configuration?.ObjectLockEnabled === 'Enabled';
  const openEditor = () => {
    const retention = configuration?.Rule?.DefaultRetention;
    setRetentionOn(Boolean(retention));
    setMode(retention?.Mode || 'GOVERNANCE');
    setPeriod(String(retention?.Days ?? retention?.Years ?? 1));
    setUnit(retention?.Years ? { label: 'Years', value: 'Years' } : { label: 'Days', value: 'Days' });
    setAcknowledged(false);
    setError('');
    setEditing(true);
  };

  const limit = unit.value === 'Days' ? 36500 : 100;
  const periodValue = Number(period);
  const periodError = retentionOn && (!/^\d+$/.test(period) || periodValue < 1 || periodValue > limit)
    ? `Enter a whole number from 1 to ${limit}.` : '';
  const needsVersioning = !enabled && versioningStatus !== 'Enabled';

  const save = async () => {
    if (periodError || needsVersioning || (!enabled && !acknowledged)) return;
    setSaving(true);
    setError('');
    try {
      await putS3ObjectLock(bucket, retentionOn ? { Mode: mode, [unit.value]: periodValue } : null);
      const config = await fetchS3ObjectLock(bucket);
      setConfiguration(config);
      onLockChange(true);
      setEditing(false);
      notify('success', 'Successfully edited Object Lock.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to edit Object Lock');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Container
      header={
        <Header
          variant="h3"
          description="Store objects using a write-once-read-many (WORM) model to help you prevent objects from being deleted or overwritten for a fixed amount of time or indefinitely. Object Lock works only in versioned buckets, and once on it can’t be turned off."
          actions={<Button ariaLabel="Edit Object Lock" onClick={openEditor} disabled={configuration === undefined && versioningStatus === 'Unavailable'}>Edit</Button>}
        >
          Object Lock
        </Header>
      }
    >
      <KeyValuePairs
        columns={2}
        items={[
          { label: 'Object Lock', value: configuration === undefined ? <StatusIndicator type="loading">Loading</StatusIndicator> : enabled ? <StatusIndicator type="success">Enabled</StatusIndicator> : <StatusIndicator type="stopped">Disabled</StatusIndicator> },
          { label: 'Default retention', value: enabled ? describeDefaultRetention(configuration ?? null) : '-' },
        ]}
      />
      <Modal
        visible={editing}
        onDismiss={() => setEditing(false)}
        header="Edit Object Lock"
        size="medium"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setEditing(false)}>Cancel</Button>
              <Button variant="primary" loading={saving} disabled={Boolean(periodError) || needsVersioning || (!enabled && !acknowledged)} onClick={save}>Save changes</Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <FlociNote>{OBJECT_LOCK_NOTE}</FlociNote>
          {error && <Alert type="error">{error}</Alert>}
          {needsVersioning && (
            <Alert type="warning" header="Bucket Versioning is required">
              Object Lock can be turned on only for a bucket with Bucket Versioning enabled. Enable versioning in Properties first.
            </Alert>
          )}
          {!enabled && !needsVersioning && (
            <Checkbox checked={acknowledged} onChange={({ detail }) => setAcknowledged(detail.checked)}>
              I acknowledge that turning on Object Lock is permanent: it can’t be turned off and Bucket Versioning can’t be suspended.
            </Checkbox>
          )}
          <FormField label="Default retention" description="Automatically protect new objects put into this bucket from being deleted or overwritten.">
            <RadioGroup
              value={retentionOn ? 'enable' : 'disable'}
              onChange={({ detail }) => setRetentionOn(detail.value === 'enable')}
              items={[{ value: 'disable', label: 'Disable' }, { value: 'enable', label: 'Enable' }]}
            />
          </FormField>
          {retentionOn && (
            <>
              <FormField label="Default retention mode">
                <RadioGroup
                  value={mode}
                  onChange={({ detail }) => setMode(detail.value)}
                  items={[
                    { value: 'GOVERNANCE', label: 'Governance', description: 'Users with specific IAM permissions can overwrite or delete protected object versions during the retention period.' },
                    { value: 'COMPLIANCE', label: 'Compliance', description: 'No users can overwrite or delete protected object versions during the retention period.' },
                  ]}
                />
              </FormField>
              <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                <FormField label="Default retention period" errorText={periodError}>
                  <Input type="number" value={period} onChange={({ detail }) => setPeriod(detail.value)} ariaLabel="Default retention period" />
                </FormField>
                <FormField label="Unit">
                  <Select selectedOption={unit} onChange={({ detail }) => setUnit(detail.selectedOption as typeof unit)} options={[{ label: 'Days', value: 'Days' }, { label: 'Years', value: 'Years' }]} />
                </FormField>
              </Grid>
            </>
          )}
        </SpaceBetween>
      </Modal>
    </Container>
  );
}

/** Splits an ISO date into the date (YYYY-MM-DD) and time (hh:mm:ss) fields, in UTC. */
function splitIso(value?: string): [string, string] {
  if (!value) return ['', '00:00:00'];
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return ['', '00:00:00'];
  const iso = date.toISOString();
  return [iso.slice(0, 10), iso.slice(11, 19)];
}

/**
 * Object properties → Object Lock retention and legal hold, for one object version. Shown inside
 * the object dialog, so it reports results itself rather than on the page behind the dialog.
 */
export function S3ObjectLockSettings({ bucket, objectKey, versionId, head, onChanged }: {
  bucket: string;
  objectKey: string;
  versionId?: string;
  head: S3ObjectHead | null;
  onChanged: () => void;
}) {
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const notify: Notify = (type, text) => setMessage({ type, text });
  const [editingRetention, setEditingRetention] = useState(false);
  const [mode, setMode] = useState('none');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('00:00:00');
  const [bypass, setBypass] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const currentMode = head?.object_lock_mode;
  const currentUntil = head?.object_lock_retain_until_date;
  const active = Boolean(currentMode && currentUntil && new Date(currentUntil).getTime() > Date.now());
  const legalHold = head?.object_lock_legal_hold_status === 'ON';

  const openRetention = () => {
    setMode(currentMode || 'none');
    const [d, t] = splitIso(currentUntil);
    setDate(d);
    setTime(t);
    setBypass(false);
    setError('');
    setEditingRetention(true);
  };

  const target = /^\d{4}-\d{2}-\d{2}$/.test(date) && /^\d{2}:\d{2}:\d{2}$/.test(time) ? new Date(`${date}T${time}Z`) : null;
  const dateError = mode === 'none' ? ''
    : !target || Number.isNaN(target.getTime()) ? 'Enter a date as YYYY-MM-DD and a time as hh:mm:ss (UTC).'
    : target.getTime() <= Date.now() ? 'The retain until date must be in the future.'
    : '';
  const shortens = active && (mode === 'none' || (target !== null && currentUntil !== undefined && target.getTime() < new Date(currentUntil).getTime()));
  const complianceBlocked = active && currentMode === 'COMPLIANCE' && (mode !== 'COMPLIANCE' || shortens);
  const needsBypass = active && currentMode === 'GOVERNANCE' && shortens;

  const saveRetention = async () => {
    if (dateError || complianceBlocked) return;
    setSaving(true);
    setError('');
    try {
      await putS3ObjectRetention(bucket, objectKey, versionId, mode === 'none' ? null : mode, mode === 'none' ? null : target!.toISOString(), bypass);
      setEditingRetention(false);
      notify('success', 'Successfully edited Object Lock retention.');
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to edit Object Lock retention');
    } finally {
      setSaving(false);
    }
  };

  const toggleLegalHold = async () => {
    setSaving(true);
    setMessage(null);
    try {
      await putS3ObjectLegalHold(bucket, objectKey, versionId, legalHold ? 'OFF' : 'ON');
      notify('success', `Successfully turned ${legalHold ? 'off' : 'on'} the legal hold.`);
      onChanged();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Failed to edit Object Lock legal hold');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Container
      header={
        <Header
          variant="h3"
          description={`Retention and legal hold apply to ${versionId ? `version ${versionId}` : 'the current version'} of this object.`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button loading={saving && !editingRetention} onClick={toggleLegalHold}>{legalHold ? 'Turn off legal hold' : 'Turn on legal hold'}</Button>
              <Button onClick={openRetention}>Edit retention</Button>
            </SpaceBetween>
          }
        >
          Object Lock
        </Header>
      }
    >
      <SpaceBetween size="m">
        {message && <Alert type={message.type} dismissible onDismiss={() => setMessage(null)}>{message.text}</Alert>}
        <KeyValuePairs
          columns={3}
          items={[
            { label: 'Retention mode', value: currentMode ? MODE_LABELS[currentMode] || currentMode : 'None' },
            { label: 'Retain until date', value: currentUntil ? `${formatAwsDate(currentUntil)}${active ? '' : ' (expired)'}` : '-' },
            { label: 'Legal hold', value: legalHold ? <StatusIndicator type="warning">On</StatusIndicator> : 'Off' },
          ]}
        />
        {editingRetention && (
          <Container header={<Header variant="h3">Edit Object Lock retention</Header>}>
            <SpaceBetween size="m">
              {error && <Alert type="error">{error}</Alert>}
              <FormField label="Retention mode">
                <RadioGroup
                  value={mode}
                  onChange={({ detail }) => setMode(detail.value)}
                  items={[
                    { value: 'none', label: 'Disable', description: 'Removes the retention. Active governance retention needs the bypass; active compliance retention can’t be removed.' },
                    { value: 'GOVERNANCE', label: 'Governance mode' },
                    { value: 'COMPLIANCE', label: 'Compliance mode', description: 'Once set, the mode can’t change and the date can only be extended until it passes.' },
                  ]}
                />
              </FormField>
              {mode !== 'none' && (
                <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                  <FormField label="Retain until date" constraintText="YYYY-MM-DD" errorText={dateError}>
                    <Input value={date} onChange={({ detail }) => setDate(detail.value)} placeholder="YYYY-MM-DD" ariaLabel="Retain until date" />
                  </FormField>
                  <FormField label="Time (UTC)" constraintText="hh:mm:ss">
                    <Input value={time} onChange={({ detail }) => setTime(detail.value)} placeholder="00:00:00" ariaLabel="Retain until time" />
                  </FormField>
                </Grid>
              )}
              {complianceBlocked && <Alert type="warning">This version is under compliance retention until {formatAwsDate(currentUntil)}. Its mode can’t change and the date can only be extended.</Alert>}
              {needsBypass && (
                <Checkbox checked={bypass} onChange={({ detail }) => setBypass(detail.checked)} description="Needs the s3:BypassGovernanceRetention permission.">
                  Bypass governance retention
                </Checkbox>
              )}
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setEditingRetention(false)}>Cancel</Button>
                <Button variant="primary" loading={saving} disabled={Boolean(dateError) || complianceBlocked} onClick={saveRetention}>Save changes</Button>
              </SpaceBetween>
            </SpaceBetween>
          </Container>
        )}
      </SpaceBetween>
    </Container>
  );
}
