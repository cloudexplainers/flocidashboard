import Alert from '@cloudscape-design/components/alert';
import Header from '@cloudscape-design/components/header';
import Table from './ResourceTable';

export function PolicySummary({ document }: { document: unknown }) {
  let policy: any;
  try { policy = typeof document === 'string' ? JSON.parse(document) : document; }
  catch { return <Alert type="info">Policy summary unavailable. Inspect the policy document below.</Alert>; }
  if (!policy) return <Alert type="info">Policy document not reported.</Alert>;
  const text = (value: unknown): string => Array.isArray(value) ? value.map(text).join(', ') : value && typeof value === 'object' ? Object.entries(value).map(([key, item]) => `${key}: ${text(item)}`).join('; ') : value == null ? 'Not specified' : String(value);
  const statements = Array.isArray(policy.Statement) ? policy.Statement : policy.Statement ? [policy.Statement] : [];
  return <Table header={<Header variant="h3" description="Statement summary. Conditions and the complete document remain authoritative.">Policy statement summary</Header>} items={statements} columnDefinitions={[
    { id: 'effect', header: 'Effect', cell: (item: any) => item.Effect || 'Not reported' },
    { id: 'principal', header: 'Principal', cell: (item: any) => `${item.NotPrincipal ? 'Except: ' : ''}${text(item.Principal ?? item.NotPrincipal)}` },
    { id: 'action', header: 'Actions', cell: (item: any) => `${item.NotAction ? 'Except: ' : ''}${text(item.Action ?? item.NotAction)}` },
    { id: 'resource', header: 'Resources', cell: (item: any) => `${item.NotResource ? 'Except: ' : ''}${text(item.Resource ?? item.NotResource)}` },
    { id: 'conditions', header: 'Conditions', cell: (item: any) => item.Condition ? text(item.Condition) : 'None' },
  ]} empty={<span>No policy statements reported.</span>} />;
}
