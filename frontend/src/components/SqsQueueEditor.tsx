import { useEffect, useState } from 'react';
import Modal from '@cloudscape-design/components/modal';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Checkbox from '@cloudscape-design/components/checkbox';
import Select from '@cloudscape-design/components/select';
import Alert from '@cloudscape-design/components/alert';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Header from '@cloudscape-design/components/header';
import RadioGroup from '@cloudscape-design/components/radio-group';
import { setSqsQueueAttributes, startSqsRedrive } from '../api/client';

export interface EditableQueue {
  QueueName: string;
  QueueArn?: string;
  Type?: string;
  VisibilityTimeout?: string | number;
  MessageRetentionPeriod?: string | number;
  MaximumMessageSize?: string | number;
  DelaySeconds?: string | number;
  ReceiveMessageWaitTimeSeconds?: string | number;
  Policy?: string;
  RedrivePolicy?: string;
  DeadLetterSourceQueues?: string[];
}

/** Attribute limits from the SQS API reference (seconds unless noted). */
const LIMITS = {
  VisibilityTimeout: [0, 43200],
  MessageRetentionPeriod: [60, 1209600],
  DelaySeconds: [0, 900],
  MaximumMessageSize: [1024, 1048576],
  ReceiveMessageWaitTimeSeconds: [0, 20],
} as const;
type NumericAttribute = keyof typeof LIMITS;
const LABELS: Record<NumericAttribute, string> = {
  VisibilityTimeout: 'Visibility timeout (seconds)',
  MessageRetentionPeriod: 'Message retention period (seconds)',
  DelaySeconds: 'Delivery delay (seconds)',
  MaximumMessageSize: 'Maximum message size (bytes)',
  ReceiveMessageWaitTimeSeconds: 'Receive message wait time (seconds)',
};

const parseRedrive = (value?: string) => {
  try { return value ? JSON.parse(value) : null; } catch { return null; }
};

/** The console's "Edit queue" page: configuration, dead-letter queue and access policy. */
export function SqsQueueEditor({ queue, queues, visible, onDismiss, onSaved }: {
  queue: EditableQueue;
  queues: EditableQueue[];
  visible: boolean;
  onDismiss: () => void;
  onSaved: (message: string) => void;
}) {
  const [values, setValues] = useState<Record<NumericAttribute, string>>({} as Record<NumericAttribute, string>);
  const [dlqEnabled, setDlqEnabled] = useState(false);
  const [dlqArn, setDlqArn] = useState('');
  const [maxReceives, setMaxReceives] = useState('10');
  const [policy, setPolicy] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    setValues(Object.fromEntries((Object.keys(LIMITS) as NumericAttribute[]).map(name => [name, queue[name] == null ? '' : String(queue[name])])) as Record<NumericAttribute, string>);
    const redrive = parseRedrive(queue.RedrivePolicy);
    setDlqEnabled(Boolean(redrive));
    setDlqArn(redrive?.deadLetterTargetArn || '');
    setMaxReceives(String(redrive?.maxReceiveCount ?? 10));
    setPolicy(queue.Policy || '');
    setError('');
  }, [visible, queue]);

  const rangeError = (name: NumericAttribute) => {
    const value = values[name];
    if (value === '' || value === undefined) return undefined;
    const [min, max] = LIMITS[name];
    return /^\d+$/.test(value) && Number(value) >= min && Number(value) <= max ? undefined : `Enter a whole number from ${min} to ${max}.`;
  };
  const receivesError = /^\d+$/.test(maxReceives) && Number(maxReceives) >= 1 && Number(maxReceives) <= 1000 ? undefined : 'Enter a whole number from 1 to 1000.';
  let policyError: string | undefined;
  if (policy.trim()) {
    try { JSON.parse(policy); } catch { policyError = 'The access policy must be valid JSON.'; }
  }
  // A DLQ must be the same type as the source queue (FIFO with FIFO, standard with standard).
  const dlqOptions = queues
    .filter(item => item.QueueArn && item.QueueArn !== queue.QueueArn && (item.Type === 'FIFO') === (queue.Type === 'FIFO'))
    .map(item => ({ value: item.QueueArn!, label: item.QueueName, description: item.QueueArn }));
  const invalid = (Object.keys(LIMITS) as NumericAttribute[]).some(name => rangeError(name))
    || Boolean(policyError) || (dlqEnabled && (!dlqArn || Boolean(receivesError)));

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const attributes: Record<string, string> = {};
      for (const name of Object.keys(LIMITS) as NumericAttribute[]) {
        if (values[name] !== '' && values[name] !== String(queue[name] ?? '')) attributes[name] = values[name];
      }
      const current = parseRedrive(queue.RedrivePolicy);
      if (dlqEnabled) {
        const next = JSON.stringify({ deadLetterTargetArn: dlqArn, maxReceiveCount: Number(maxReceives) });
        if (!current || current.deadLetterTargetArn !== dlqArn || Number(current.maxReceiveCount) !== Number(maxReceives)) attributes.RedrivePolicy = next;
      } else if (current) {
        attributes.RedrivePolicy = ''; // An empty RedrivePolicy removes the dead-letter queue.
      }
      if (policy.trim() !== (queue.Policy || '').trim()) attributes.Policy = policy.trim();
      if (!Object.keys(attributes).length) {
        onDismiss();
        return;
      }
      await setSqsQueueAttributes(queue.QueueName, attributes);
      onSaved(`Queue "${queue.QueueName}" updated.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update queue.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      visible={visible}
      size="large"
      onDismiss={() => !saving && onDismiss()}
      header={`Edit ${queue.QueueName}`}
      footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
        <Button variant="link" disabled={saving} onClick={onDismiss}>Cancel</Button>
        <Button variant="primary" loading={saving} disabled={invalid} onClick={save}>Save</Button>
      </SpaceBetween></Box>}
    >
      <SpaceBetween size="l">
        {error && <Alert type="error">{error}</Alert>}
        <SpaceBetween size="m">
          <Header variant="h3">Configuration</Header>
          <ColumnLayout columns={2}>
            {(Object.keys(LIMITS) as NumericAttribute[]).map(name => (
              <FormField key={name} label={LABELS[name]} constraintText={`${LIMITS[name][0]} to ${LIMITS[name][1]}.`} errorText={rangeError(name)}>
                <Input type="number" value={values[name] ?? ''} onChange={({ detail }) => setValues(previous => ({ ...previous, [name]: detail.value }))} />
              </FormField>
            ))}
          </ColumnLayout>
        </SpaceBetween>
        <SpaceBetween size="m">
          <Header variant="h3" description="Send undeliverable messages to a dead-letter queue.">Dead-letter queue</Header>
          <Checkbox checked={dlqEnabled} onChange={({ detail }) => setDlqEnabled(detail.checked)}>Enabled</Checkbox>
          {dlqEnabled && (
            <ColumnLayout columns={2}>
              <FormField label="Queue ARN" description={`Only ${queue.Type === 'FIFO' ? 'FIFO' : 'standard'} queues can be the dead-letter queue for this queue.`}>
                <Select
                  ariaLabel="Dead-letter queue"
                  selectedOption={dlqArn ? { value: dlqArn, label: dlqOptions.find(option => option.value === dlqArn)?.label || dlqArn } : null}
                  placeholder="Choose a queue"
                  options={dlqOptions}
                  empty="No other queues of the same type"
                  onChange={({ detail }) => setDlqArn(detail.selectedOption.value || '')}
                />
              </FormField>
              <FormField label="Maximum receives" constraintText="1 to 1000." errorText={receivesError}>
                <Input type="number" value={maxReceives} onChange={({ detail }) => setMaxReceives(detail.value)} />
              </FormField>
            </ColumnLayout>
          )}
        </SpaceBetween>
        <FormField label="Access policy" description="JSON policy document. Leave empty for no queue policy." errorText={policyError}>
          <Textarea ariaLabel="Access policy JSON" rows={8} value={policy} onChange={({ detail }) => setPolicy(detail.value)} />
        </FormField>
      </SpaceBetween>
    </Modal>
  );
}

/** "Start DLQ redrive": moves messages from a dead-letter queue back to its source queues or to another queue. */
export function SqsRedriveModal({ queue, queues, visible, onDismiss, onStarted }: {
  queue: EditableQueue;
  queues: EditableQueue[];
  visible: boolean;
  onDismiss: () => void;
  onStarted: (message: string) => void;
}) {
  const [destination, setDestination] = useState<'source' | 'custom'>('source');
  const [customArn, setCustomArn] = useState('');
  const [velocity, setVelocity] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (visible) { setDestination('source'); setCustomArn(''); setVelocity(''); setError(''); } }, [visible]);
  const velocityError = !velocity || (/^\d+$/.test(velocity) && Number(velocity) >= 1 && Number(velocity) <= 500) ? undefined : 'Enter a whole number from 1 to 500.';
  const start = async () => {
    setStarting(true);
    setError('');
    try {
      await startSqsRedrive(queue.QueueName, {
        source_arn: queue.QueueArn,
        ...(destination === 'custom' ? { destination_arn: customArn } : {}),
        ...(velocity ? { max_number_of_messages_per_second: Number(velocity) } : {}),
      });
      onStarted(`DLQ redrive started for "${queue.QueueName}". Messages move ${destination === 'custom' ? 'to the queue you chose' : 'back to their source queues'}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start DLQ redrive.');
    } finally {
      setStarting(false);
    }
  };
  return (
    <Modal visible={visible} onDismiss={() => !starting && onDismiss()} header="Start DLQ redrive"
      footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
        <Button variant="link" disabled={starting} onClick={onDismiss}>Cancel</Button>
        <Button variant="primary" loading={starting} disabled={(destination === 'custom' && !customArn) || Boolean(velocityError)} onClick={start}>DLQ redrive</Button>
      </SpaceBetween></Box>}>
      <SpaceBetween size="m">
        {error && <Alert type="error">{error}</Alert>}
        <FormField label="Redrive configuration">
          <RadioGroup value={destination} onChange={({ detail }) => setDestination(detail.value as 'source' | 'custom')} items={[
            { value: 'source', label: 'Redrive to source queue(s)', description: 'Each message returns to the queue it came from.' },
            { value: 'custom', label: 'Redrive to a custom destination' },
          ]} />
        </FormField>
        {destination === 'custom' && (
          <FormField label="Destination queue">
            <Select ariaLabel="Destination queue" placeholder="Choose a queue" selectedOption={customArn ? { value: customArn, label: queues.find(item => item.QueueArn === customArn)?.QueueName || customArn } : null}
              options={queues.filter(item => item.QueueArn && item.QueueArn !== queue.QueueArn).map(item => ({ value: item.QueueArn!, label: item.QueueName }))}
              onChange={({ detail }) => setCustomArn(detail.selectedOption.value || '')} />
          </FormField>
        )}
        <FormField label="Velocity control - optional" description="Maximum messages per second. Leave empty for system optimized." errorText={velocityError}>
          <Input type="number" value={velocity} onChange={({ detail }) => setVelocity(detail.value)} />
        </FormField>
      </SpaceBetween>
    </Modal>
  );
}
