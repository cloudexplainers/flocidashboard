import { useRef, useState } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Textarea from '@cloudscape-design/components/textarea';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Alert from '@cloudscape-design/components/alert';
import { CodeSnippet } from './CodeSnippet';

const pretty = (value: string) => { try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; } };

export function EventPatternTester({ pattern: initialPattern }: { pattern?: string }) {
  const [pattern, setPattern] = useState(initialPattern && initialPattern !== '—' ? pretty(initialPattern) : '{\n  "source": ["ecommerce.orders"],\n  "detail-type": ["OrderPlaced"]\n}');
  const [sample, setSample] = useState(JSON.stringify({ version: '0', id: 'sample-event', account: '000000000000', time: '2026-01-01T00:00:00Z', region: 'us-east-1', source: 'ecommerce.orders', 'detail-type': 'OrderPlaced', resources: [], detail: { orderId: 'example' } }, null, 2));
  const [source, setSource] = useState('ecommerce.orders');
  const [detailType, setDetailType] = useState('OrderPlaced');
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const revision = useRef(0);
  const changed = () => { revision.current++; setResult(null); setError(''); };
  return <Container header={<Header variant="h2" description="Test with Floci’s EventBridge matching engine. This does not publish an event or invoke targets.">Event pattern tester</Header>}><SpaceBetween size="m">
    <FormField label="Event source"><Input value={source} onChange={({ detail }) => setSource(detail.value)} /></FormField>
    <FormField label="Detail type"><Input value={detailType} onChange={({ detail }) => setDetailType(detail.value)} /></FormField>
    <Button disabled={!source.trim() || !detailType.trim()} onClick={() => { changed(); setPattern(JSON.stringify({ source: [source.trim()], 'detail-type': [detailType.trim()] }, null, 2)); }}>Build pattern from fields</Button>
    <FormField label="Pattern JSON" description="Use the builder above for an exact source/type match, or edit the complete pattern here."><Textarea rows={8} value={pattern} onChange={({ detail }) => { changed(); setPattern(detail.value); }} /></FormField>
    <FormField label="Sample event JSON"><Textarea rows={10} value={sample} onChange={({ detail }) => { changed(); setSample(detail.value); }} /></FormField>
    <Button variant="primary" loading={busy} onClick={async () => {
      changed(); const requestRevision = revision.current;
      try {
        const eventPattern = JSON.parse(pattern), event = JSON.parse(sample);
        if (!eventPattern || typeof eventPattern !== 'object' || Array.isArray(eventPattern) || !event || typeof event !== 'object' || Array.isArray(event)) throw new Error('Pattern and sample event must be JSON objects.');
        setBusy(true);
        const response = await fetch('/api/eventbridge/patterns/test/', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '' }, body: JSON.stringify({ event_pattern: eventPattern, event, engine: 'emulator' }) });
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || 'Unable to test event pattern');
        if (revision.current === requestRevision) setResult(payload);
      } catch (error: any) { if (revision.current === requestRevision) setError(error.message); }
      finally { setBusy(false); }
    }}>Test pattern</Button>
    {error && <Alert type="error">{error}</Alert>}
    {result && <><Alert type={result.result ? 'success' : 'info'} header={result.result ? 'Pattern matches sample event' : 'Pattern does not match sample event'}>{result.summary}</Alert><CodeSnippet language="json" code={JSON.stringify(result, null, 2)} /></>}
  </SpaceBetween></Container>;
}
