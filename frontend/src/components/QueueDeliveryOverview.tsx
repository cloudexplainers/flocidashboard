import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Button from '@cloudscape-design/components/button';
import Alert from '@cloudscape-design/components/alert';

export function QueueDeliveryOverview({ queue, onNavigate }: { queue: any; onNavigate?: (path: string) => void }) {
  let redrive: any;
  let invalid = false;
  try { redrive = queue.RedrivePolicy ? JSON.parse(queue.RedrivePolicy) : null; } catch { invalid = true; }
  const destination = redrive?.deadLetterTargetArn;
  const href = destination ? `/sqs/attributes?${new URLSearchParams({ queue: destination })}` : '';
  return <Container header={<Header variant="h3">Queue delivery overview</Header>}>
    <KeyValuePairs columns={3} items={[
      { label: 'Available messages (approximate)', value: String(queue.ApproximateNumberOfMessages ?? 'Not reported') },
      { label: 'In flight (approximate)', value: String(queue.ApproximateNumberOfMessagesNotVisible ?? 'Not reported') },
      { label: 'Delayed messages (approximate)', value: String(queue.ApproximateNumberOfMessagesDelayed ?? 'Not reported') },
      { label: 'Dead-letter destination', value: destination ? <Button variant="inline-link" href={href} onClick={event => { if (onNavigate) { event.preventDefault(); onNavigate(href.slice(1)); } }}>{destination}</Button> : 'Not reported' },
      { label: 'Maximum receives before dead-lettering', value: String(redrive?.maxReceiveCount ?? 'Not reported') },
    ]} />
    {invalid && <Alert type="warning">The redrive policy could not be read. Inspect the original policy in the Dead-letter queue tab.</Alert>}
  </Container>;
}
