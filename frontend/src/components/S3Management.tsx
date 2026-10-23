import { useEffect, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import FormField from '@cloudscape-design/components/form-field';
import Grid from '@cloudscape-design/components/grid';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Textarea from '@cloudscape-design/components/textarea';
import Toggle from '@cloudscape-design/components/toggle';
import Table from './ResourceTable';
import ConfirmActionModal from './ConfirmActionModal';
import { FlociNote } from './FlociNote';
import {
  deleteS3IntelligentTiering,
  fetchS3IntelligentTiering,
  fetchS3Lifecycle,
  fetchS3Replication,
  putS3IntelligentTiering,
  putS3Lifecycle,
  putS3Replication,
} from '../api/client';

type Notify = (type: 'success' | 'error', text: string) => void;

export const LIFECYCLE_NOTE = 'Floci stores lifecycle rules and returns them, but never runs them: no object moves to another storage class or expires, and incomplete multipart uploads aren’t aborted. Floci also accepts rules AWS rejects, such as a transition to Standard-IA before 30 days; this console checks those rules itself.';
export const REPLICATION_NOTE = 'Floci stores the replication configuration and returns it, but replicates nothing: new objects never reach the destination bucket and get no replication status. Floci also accepts a configuration when either bucket has no versioning; AWS requires versioning on both, and so does this console.';
export const INTELLIGENT_TIERING_NOTE = 'Floci stores these configurations and checks the day limits, but never moves objects between access tiers: an object uploaded with the Intelligent-Tiering storage class stays readable at once, however long it goes unread.';

const STORAGE_CLASSES = [
  { label: 'Standard-IA', value: 'STANDARD_IA' },
  { label: 'Intelligent-Tiering', value: 'INTELLIGENT_TIERING' },
  { label: 'One Zone-IA', value: 'ONEZONE_IA' },
  { label: 'Glacier Instant Retrieval', value: 'GLACIER_IR' },
  { label: 'Glacier Flexible Retrieval (formerly Glacier)', value: 'GLACIER' },
  { label: 'Glacier Deep Archive', value: 'DEEP_ARCHIVE' },
];
const IA_CLASSES = new Set(['STANDARD_IA', 'ONEZONE_IA']);
const classLabel = (value: string) => STORAGE_CLASSES.find(option => option.value === value)?.label || value;
const isWholeNumber = (value: string) => /^\d+$/.test(value.trim());

// ───────────────────────────── Lifecycle rules ─────────────────────────────

interface TransitionRow { storageClass: string; days: string }

export interface LifecycleForm {
  id: string;
  enabled: boolean;
  scope: 'all' | 'filter';
  prefix: string;
  tags: string;
  transitionCurrent: boolean;
  transitions: TransitionRow[];
  transitionNoncurrent: boolean;
  noncurrentTransitions: TransitionRow[];
  expireCurrent: boolean;
  expireDays: string;
  deleteNoncurrent: boolean;
  noncurrentDays: string;
  newerVersions: string;
  deleteMarkers: boolean;
  abortMultipart: boolean;
  abortDays: string;
}

const EMPTY_LIFECYCLE: LifecycleForm = {
  id: '', enabled: true, scope: 'filter', prefix: '', tags: '',
  transitionCurrent: false, transitions: [{ storageClass: 'STANDARD_IA', days: '30' }],
  transitionNoncurrent: false, noncurrentTransitions: [{ storageClass: 'STANDARD_IA', days: '30' }],
  expireCurrent: false, expireDays: '',
  deleteNoncurrent: false, noncurrentDays: '', newerVersions: '',
  deleteMarkers: false, abortMultipart: false, abortDays: '7',
};

function parseTags(text: string): Array<{ Key: string; Value: string }> {
  return text.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
    const at = line.indexOf('=');
    return at < 0 ? { Key: line, Value: '' } : { Key: line.slice(0, at).trim(), Value: line.slice(at + 1).trim() };
  });
}

function ruleFilter(rule: any): { prefix: string; tags: Array<{ Key: string; Value: string }> } {
  const filter = rule.Filter || {};
  if (filter.And) return { prefix: filter.And.Prefix || '', tags: filter.And.Tags || [] };
  return { prefix: filter.Prefix ?? rule.Prefix ?? '', tags: filter.Tag ? [filter.Tag] : [] };
}

/** Lifecycle rules the form can't show without losing settings (dates, object size filters). */
export function lifecycleRuleIsEditable(rule: any): boolean {
  const filter = rule.Filter || {};
  const sizeFilter = 'ObjectSizeGreaterThan' in filter || 'ObjectSizeLessThan' in filter
    || 'ObjectSizeGreaterThan' in (filter.And || {}) || 'ObjectSizeLessThan' in (filter.And || {});
  const dated = Boolean(rule.Expiration?.Date) || (rule.Transitions || []).some((t: any) => t.Date);
  return !sizeFilter && !dated;
}

export function lifecycleFormFromRule(rule: any): LifecycleForm {
  const { prefix, tags } = ruleFilter(rule);
  const transitions = (rule.Transitions || []).map((t: any) => ({ storageClass: t.StorageClass, days: String(t.Days ?? '') }));
  const noncurrent = (rule.NoncurrentVersionTransitions || []).map((t: any) => ({ storageClass: t.StorageClass, days: String(t.NoncurrentDays ?? '') }));
  return {
    id: rule.ID || '',
    enabled: rule.Status === 'Enabled',
    scope: prefix || tags.length ? 'filter' : 'all',
    prefix,
    tags: tags.map(tag => `${tag.Key}=${tag.Value}`).join('\n'),
    transitionCurrent: transitions.length > 0,
    transitions: transitions.length ? transitions : EMPTY_LIFECYCLE.transitions,
    transitionNoncurrent: noncurrent.length > 0,
    noncurrentTransitions: noncurrent.length ? noncurrent : EMPTY_LIFECYCLE.noncurrentTransitions,
    expireCurrent: rule.Expiration?.Days !== undefined,
    expireDays: rule.Expiration?.Days !== undefined ? String(rule.Expiration.Days) : '',
    deleteNoncurrent: rule.NoncurrentVersionExpiration?.NoncurrentDays !== undefined,
    noncurrentDays: rule.NoncurrentVersionExpiration?.NoncurrentDays !== undefined ? String(rule.NoncurrentVersionExpiration.NoncurrentDays) : '',
    newerVersions: rule.NoncurrentVersionExpiration?.NewerNoncurrentVersions !== undefined ? String(rule.NoncurrentVersionExpiration.NewerNoncurrentVersions) : '',
    deleteMarkers: Boolean(rule.Expiration?.ExpiredObjectDeleteMarker),
    abortMultipart: Boolean(rule.AbortIncompleteMultipartUpload),
    abortDays: String(rule.AbortIncompleteMultipartUpload?.DaysAfterInitiation ?? 7),
  };
}

/** The checks the S3 console and API make on one lifecycle rule. Returns '' when the rule is valid. */
export function lifecycleFormError(form: LifecycleForm, otherIds: string[]): string {
  const id = form.id.trim();
  if (!id) return 'Lifecycle rule name is required.';
  if (id.length > 255) return 'Lifecycle rule names can be up to 255 characters long.';
  if (otherIds.includes(id)) return `A lifecycle rule named "${id}" already exists.`;
  const tags = parseTags(form.tags);
  if (form.scope === 'filter' && !form.prefix.trim() && !tags.length) return 'Enter a prefix or at least one object tag, or apply the rule to all objects in the bucket.';
  if (tags.some(tag => !tag.Key)) return 'Each object tag needs a key (one key=value per line).';
  if (![form.transitionCurrent, form.transitionNoncurrent, form.expireCurrent, form.deleteNoncurrent, form.deleteMarkers, form.abortMultipart].some(Boolean)) {
    return 'Choose at least one lifecycle rule action.';
  }
  const checkRows = (rows: TransitionRow[], what: string): string => {
    const seen = new Set<string>();
    for (const row of rows) {
      if (!isWholeNumber(row.days)) return `Enter the number of days for each ${what} transition.`;
      if (seen.has(row.storageClass)) return `Each ${what} transition needs a different storage class.`;
      seen.add(row.storageClass);
      if (IA_CLASSES.has(row.storageClass) && Number(row.days) < 30) return `Transitions of ${what} versions to ${classLabel(row.storageClass)} need at least 30 days.`;
    }
    return '';
  };
  if (form.transitionCurrent) { const error = checkRows(form.transitions, 'current'); if (error) return error; }
  if (form.transitionNoncurrent) { const error = checkRows(form.noncurrentTransitions, 'noncurrent'); if (error) return error; }
  if (form.expireCurrent) {
    if (!isWholeNumber(form.expireDays) || Number(form.expireDays) < 1) return 'Days after object creation must be a whole number of 1 or more.';
    if (form.deleteMarkers) return 'Delete expired object delete markers can’t be selected with Expire current versions of objects.';
    const latest = form.transitionCurrent ? Math.max(...form.transitions.map(row => Number(row.days))) : -1;
    if (Number(form.expireDays) <= latest) return 'Expiration must come after the last transition of current versions.';
  }
  if (form.deleteNoncurrent) {
    if (!isWholeNumber(form.noncurrentDays) || Number(form.noncurrentDays) < 1) return 'Days after objects become noncurrent must be a whole number of 1 or more.';
    if (form.newerVersions.trim() && (!isWholeNumber(form.newerVersions) || Number(form.newerVersions) < 1 || Number(form.newerVersions) > 100)) return 'Number of newer versions to retain must be from 1 to 100.';
    const latest = form.transitionNoncurrent ? Math.max(...form.noncurrentTransitions.map(row => Number(row.days))) : -1;
    if (Number(form.noncurrentDays) <= latest) return 'Permanent deletion must come after the last transition of noncurrent versions.';
  }
  if (form.abortMultipart) {
    if (!isWholeNumber(form.abortDays) || Number(form.abortDays) < 1) return 'Days for incomplete multipart uploads must be a whole number of 1 or more.';
    if (tags.length && form.scope === 'filter') return 'Delete incomplete multipart uploads can’t be used with object tag filters.';
  }
  return '';
}

export function lifecycleRuleFromForm(form: LifecycleForm): any {
  const tags = form.scope === 'filter' ? parseTags(form.tags) : [];
  const prefix = form.scope === 'filter' ? form.prefix.trim() : '';
  let filter: any = {};
  if (tags.length + (prefix ? 1 : 0) > 1) filter = { And: { ...(prefix ? { Prefix: prefix } : {}), Tags: tags } };
  else if (tags.length) filter = { Tag: tags[0] };
  else if (prefix) filter = { Prefix: prefix };
  const rule: any = { ID: form.id.trim(), Status: form.enabled ? 'Enabled' : 'Disabled', Filter: filter };
  if (form.transitionCurrent) rule.Transitions = form.transitions.map(row => ({ Days: Number(row.days), StorageClass: row.storageClass }));
  if (form.transitionNoncurrent) rule.NoncurrentVersionTransitions = form.noncurrentTransitions.map(row => ({ NoncurrentDays: Number(row.days), StorageClass: row.storageClass }));
  if (form.expireCurrent) rule.Expiration = { Days: Number(form.expireDays) };
  else if (form.deleteMarkers) rule.Expiration = { ExpiredObjectDeleteMarker: true };
  if (form.deleteNoncurrent) {
    rule.NoncurrentVersionExpiration = { NoncurrentDays: Number(form.noncurrentDays), ...(form.newerVersions.trim() ? { NewerNoncurrentVersions: Number(form.newerVersions) } : {}) };
  }
  if (form.abortMultipart) rule.AbortIncompleteMultipartUpload = { DaysAfterInitiation: Number(form.abortDays) };
  return rule;
}

function describeScope(rule: any): string {
  const filter = rule.Filter || {};
  if ('ObjectSizeGreaterThan' in filter || 'ObjectSizeLessThan' in filter || filter.And) {
    const parts = [];
    const { prefix, tags } = ruleFilter(rule);
    if (prefix) parts.push(`Prefix ${prefix}`);
    if (tags.length) parts.push(`${tags.length} ${tags.length === 1 ? 'tag' : 'tags'}`);
    const size = filter.And || filter;
    if (size.ObjectSizeGreaterThan !== undefined) parts.push(`> ${size.ObjectSizeGreaterThan} bytes`);
    if (size.ObjectSizeLessThan !== undefined) parts.push(`< ${size.ObjectSizeLessThan} bytes`);
    return parts.join(', ') || 'Entire bucket';
  }
  const { prefix, tags } = ruleFilter(rule);
  if (prefix) return `Prefix ${prefix}`;
  if (tags.length) return `Tag ${tags[0].Key}=${tags[0].Value}`;
  return 'Entire bucket';
}

export function describeLifecycleActions(rule: any): string[] {
  const actions: string[] = [];
  (rule.Transitions || []).forEach((t: any) => actions.push(`Transition to ${classLabel(t.StorageClass)} after ${t.Days ?? t.Date} ${t.Days !== undefined ? 'days' : ''}`.trim()));
  if (rule.Expiration?.Days !== undefined) actions.push(`Expire current versions after ${rule.Expiration.Days} days`);
  if (rule.Expiration?.Date) actions.push(`Expire current versions on ${rule.Expiration.Date}`);
  (rule.NoncurrentVersionTransitions || []).forEach((t: any) => actions.push(`Transition noncurrent versions to ${classLabel(t.StorageClass)} after ${t.NoncurrentDays} days`));
  if (rule.NoncurrentVersionExpiration) actions.push(`Delete noncurrent versions after ${rule.NoncurrentVersionExpiration.NoncurrentDays} days`);
  if (rule.Expiration?.ExpiredObjectDeleteMarker) actions.push('Delete expired object delete markers');
  if (rule.AbortIncompleteMultipartUpload) actions.push(`Delete incomplete multipart uploads after ${rule.AbortIncompleteMultipartUpload.DaysAfterInitiation} days`);
  return actions;
}

function TransitionRows({ rows, onChange, label }: { rows: TransitionRow[]; onChange: (rows: TransitionRow[]) => void; label: string }) {
  return (
    <SpaceBetween size="xs">
      {rows.map((row, index) => (
        <Grid key={index} gridDefinition={[{ colspan: 6 }, { colspan: 5 }, { colspan: 1 }]}>
          <FormField label={index === 0 ? 'Choose storage class transitions' : undefined}>
            <Select
              ariaLabel={`${label} storage class ${index + 1}`}
              selectedOption={STORAGE_CLASSES.find(option => option.value === row.storageClass) || null}
              onChange={({ detail }) => onChange(rows.map((r, i) => i === index ? { ...r, storageClass: detail.selectedOption.value! } : r))}
              options={STORAGE_CLASSES}
            />
          </FormField>
          <FormField label={index === 0 ? label : undefined}>
            <Input type="number" ariaLabel={`${label} ${index + 1}`} value={row.days} onChange={({ detail }) => onChange(rows.map((r, i) => i === index ? { ...r, days: detail.value } : r))} />
          </FormField>
          <Box margin={{ top: index === 0 ? 'xl' : 'n' }}>
            <Button variant="icon" iconName="remove" ariaLabel={`Remove transition ${index + 1}`} disabled={rows.length === 1} onClick={() => onChange(rows.filter((_, i) => i !== index))} />
          </Box>
        </Grid>
      ))}
      <Button disabled={rows.length >= STORAGE_CLASSES.length} onClick={() => onChange([...rows, { storageClass: STORAGE_CLASSES.find(option => !rows.some(r => r.storageClass === option.value))!.value, days: '90' }])}>Add transition</Button>
    </SpaceBetween>
  );
}

/** Management → Lifecycle rules: table, create/edit form and delete. */
export function S3LifecycleRules({ bucket, notify }: { bucket: string; notify: Notify }) {
  const [rules, setRules] = useState<any[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<any[]>([]);
  const [editing, setEditing] = useState<{ index: number | null; form: LifecycleForm } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoadError('');
    try { setRules(await fetchS3Lifecycle(bucket)); } catch (err) { setRules([]); setLoadError(err instanceof Error ? err.message : 'Failed to read lifecycle rules'); }
  };
  useEffect(() => { setRules(null); setSelected([]); load(); }, [bucket]);

  const save = async (next: any[], message: string) => {
    setSaving(true);
    setError('');
    try {
      await putS3Lifecycle(bucket, next);
      notify('success', message);
      setSelected([]);
      await load();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save lifecycle rules');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const otherIds = (rules || []).filter((_, index) => index !== editing?.index).map(rule => rule.ID).filter(Boolean);
  const formError = editing ? lifecycleFormError(editing.form, otherIds) : '';
  const set = (patch: Partial<LifecycleForm>) => setEditing(current => current && { ...current, form: { ...current.form, ...patch } });
  const selectedIndex = selected[0] ? (rules || []).indexOf(selected[0]) : -1;

  const submit = async () => {
    if (!editing || formError) return;
    const rule = lifecycleRuleFromForm(editing.form);
    const next = editing.index === null ? [...(rules || []), rule] : (rules || []).map((r, i) => (i === editing.index ? rule : r));
    if (await save(next, `Successfully ${editing.index === null ? 'created' : 'edited'} lifecycle rule "${rule.ID}".`)) setEditing(null);
  };

  const form = editing?.form;
  return (
    <SpaceBetween size="m">
      <FlociNote>{LIFECYCLE_NOTE}</FlociNote>
      {loadError && <Alert type="error">{loadError}</Alert>}
      {error && !editing && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
      <Table
        header={
          <Header
            variant="h3"
            counter={rules ? `(${rules.length})` : undefined}
            description="Use lifecycle rules to define actions you want Amazon S3 to take during an object’s lifetime, such as transitioning objects to another storage class, archiving them, or deleting them after a specified period of time."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={selectedIndex < 0 || !lifecycleRuleIsEditable(selected[0])} onClick={() => { setError(''); setEditing({ index: selectedIndex, form: lifecycleFormFromRule(selected[0]) }); }}>Edit</Button>
                <Button disabled={selectedIndex < 0} onClick={() => setDeleting(true)}>Delete</Button>
                <Button variant="primary" onClick={() => { setError(''); setEditing({ index: null, form: { ...EMPTY_LIFECYCLE } }); }}>Create lifecycle rule</Button>
              </SpaceBetween>
            }
          >
            Lifecycle rules
          </Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Lifecycle rule name', cell: rule => rule.ID || '-' },
          { id: 'status', header: 'Status', cell: rule => <StatusIndicator type={rule.Status === 'Enabled' ? 'success' : 'stopped'}>{rule.Status === 'Enabled' ? 'Enabled' : 'Disabled'}</StatusIndicator>, width: 130 },
          { id: 'scope', header: 'Scope', cell: rule => describeScope(rule) },
          { id: 'actions', header: 'Actions', cell: rule => describeLifecycleActions(rule).map(action => <div key={action}>{action}</div>) },
        ]}
        wrapLines
        items={rules || []}
        loading={rules === null}
        loadingText="Loading lifecycle rules"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy={rule => rule.ID || JSON.stringify(rule)}
        empty={<Box textAlign="center" color="inherit"><b>No lifecycle rules</b><p>There are no lifecycle rules for this bucket.</p></Box>}
      />
      {selected[0] && !lifecycleRuleIsEditable(selected[0]) && <Box variant="small" color="text-body-secondary">This rule uses a date or an object size filter, which this form doesn’t edit. Use the CLI to change it.</Box>}

      <ConfirmActionModal
        visible={deleting}
        header="Delete lifecycle rule"
        confirmLabel="Delete"
        confirmationText="delete"
        loading={saving}
        onConfirm={async () => { await save((rules || []).filter((_, i) => i !== selectedIndex), `Successfully deleted lifecycle rule "${selected[0]?.ID}".`); setDeleting(false); }}
        onDismiss={() => setDeleting(false)}
      >
        Delete lifecycle rule <strong>{selected[0]?.ID}</strong>? The other rules are kept.
      </ConfirmActionModal>

      <Modal
        visible={Boolean(editing)}
        onDismiss={() => setEditing(null)}
        header={editing?.index === null ? 'Create lifecycle rule' : 'Edit lifecycle rule'}
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setEditing(null)}>Cancel</Button>
              <Button variant="primary" loading={saving} disabled={Boolean(formError)} onClick={submit}>{editing?.index === null ? 'Create rule' : 'Save changes'}</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {form && (
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Lifecycle rule name" constraintText="Up to 255 characters">
              <Input value={form.id} disabled={editing?.index !== null} onChange={({ detail }) => set({ id: detail.value })} placeholder="Enter a rule name" ariaLabel="Lifecycle rule name" />
            </FormField>
            <Toggle checked={form.enabled} onChange={({ detail }) => set({ enabled: detail.checked })}>Enabled</Toggle>
            <FormField label="Choose a rule scope">
              <RadioGroup
                value={form.scope}
                onChange={({ detail }) => set({ scope: detail.value as LifecycleForm['scope'] })}
                items={[
                  { value: 'filter', label: 'Limit the scope of this rule using one or more filters' },
                  { value: 'all', label: 'Apply to all objects in the bucket' },
                ]}
              />
            </FormField>
            {form.scope === 'all' && <Alert type="warning">This rule applies to all objects in the bucket.</Alert>}
            {form.scope === 'filter' && (
              <Grid gridDefinition={[{ colspan: { default: 12, s: 6 } }, { colspan: { default: 12, s: 6 } }]}>
                <FormField label="Prefix" description="Don’t include the bucket name in the prefix.">
                  <Input value={form.prefix} onChange={({ detail }) => set({ prefix: detail.value })} placeholder="logs/" ariaLabel="Prefix" />
                </FormField>
                <FormField label={<>Object tags <i>- optional</i></>} description="One key=value per line.">
                  <Textarea rows={2} value={form.tags} onChange={({ detail }) => set({ tags: detail.value })} placeholder="project=blue" ariaLabel="Object tags" />
                </FormField>
              </Grid>
            )}
            <FormField label="Lifecycle rule actions" description="Choose the actions you want this rule to perform.">
              <SpaceBetween size="xxs">
                <Checkbox checked={form.transitionCurrent} onChange={({ detail }) => set({ transitionCurrent: detail.checked })}>Move current versions of objects between storage classes</Checkbox>
                <Checkbox checked={form.transitionNoncurrent} onChange={({ detail }) => set({ transitionNoncurrent: detail.checked })}>Move noncurrent versions of objects between storage classes</Checkbox>
                <Checkbox checked={form.expireCurrent} onChange={({ detail }) => set({ expireCurrent: detail.checked })}>Expire current versions of objects</Checkbox>
                <Checkbox checked={form.deleteNoncurrent} onChange={({ detail }) => set({ deleteNoncurrent: detail.checked })}>Permanently delete noncurrent versions of objects</Checkbox>
                <Checkbox checked={form.deleteMarkers || form.abortMultipart} onChange={({ detail }) => set({ deleteMarkers: detail.checked && !form.expireCurrent, abortMultipart: detail.checked })}>Delete expired object delete markers or incomplete multipart uploads</Checkbox>
              </SpaceBetween>
            </FormField>
            {form.transitionCurrent && (
              <FormField label="Transition current versions of objects between storage classes">
                <TransitionRows label="Days after object creation" rows={form.transitions} onChange={rows => set({ transitions: rows })} />
              </FormField>
            )}
            {form.transitionNoncurrent && (
              <FormField label="Transition noncurrent versions of objects between storage classes">
                <TransitionRows label="Days after objects become noncurrent" rows={form.noncurrentTransitions} onChange={rows => set({ noncurrentTransitions: rows })} />
              </FormField>
            )}
            {form.expireCurrent && (
              <FormField label="Days after object creation" description="Expire current versions of objects. In a versioned bucket, S3 adds a delete marker and the object becomes noncurrent.">
                <Input type="number" value={form.expireDays} onChange={({ detail }) => set({ expireDays: detail.value })} ariaLabel="Days after object creation to expire" />
              </FormField>
            )}
            {form.deleteNoncurrent && (
              <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
                <FormField label="Days after objects become noncurrent">
                  <Input type="number" value={form.noncurrentDays} onChange={({ detail }) => set({ noncurrentDays: detail.value })} ariaLabel="Days after objects become noncurrent to delete" />
                </FormField>
                <FormField label={<>Number of newer versions to retain <i>- optional</i></>}>
                  <Input type="number" value={form.newerVersions} onChange={({ detail }) => set({ newerVersions: detail.value })} ariaLabel="Number of newer versions to retain" />
                </FormField>
              </Grid>
            )}
            {(form.deleteMarkers || form.abortMultipart) && (
              <SpaceBetween size="xs">
                <Checkbox checked={form.deleteMarkers} disabled={form.expireCurrent} onChange={({ detail }) => set({ deleteMarkers: detail.checked })} description={form.expireCurrent ? 'This option is unavailable when Expire current versions of objects is selected.' : undefined}>Delete expired object delete markers</Checkbox>
                <Checkbox checked={form.abortMultipart} onChange={({ detail }) => set({ abortMultipart: detail.checked })}>Delete incomplete multipart uploads</Checkbox>
                {form.abortMultipart && (
                  <FormField label="Number of days">
                    <Input type="number" value={form.abortDays} onChange={({ detail }) => set({ abortDays: detail.value })} ariaLabel="Days after initiation" />
                  </FormField>
                )}
              </SpaceBetween>
            )}
            {formError && <Box color="text-status-error" data-testid="lifecycle-form-error">{formError}</Box>}
          </SpaceBetween>
        )}
      </Modal>
    </SpaceBetween>
  );
}

// ───────────────────────────── Replication rules ─────────────────────────────

interface ReplicationForm {
  id: string;
  enabled: boolean;
  priority: string;
  scope: 'all' | 'prefix';
  prefix: string;
  destination: string;
  role: string;
  storageClass: string;
  deleteMarkers: boolean;
}

/** The checks the console and S3 make on a replication rule. Returns '' when valid. */
export function replicationFormError(form: ReplicationForm, sourceBucket: string, otherRules: any[]): string {
  if (!form.id.trim()) return 'Replication rule name is required.';
  if (otherRules.some(rule => rule.ID === form.id.trim())) return `A replication rule named "${form.id.trim()}" already exists.`;
  if (!isWholeNumber(form.priority)) return 'Priority must be a whole number of 0 or more.';
  if (otherRules.some(rule => rule.Priority === Number(form.priority))) return 'Another rule has this priority. Each rule needs a different priority.';
  if (form.scope === 'prefix' && !form.prefix.trim()) return 'Enter a prefix or apply the rule to all objects in the bucket.';
  if (!form.destination.trim()) return 'Choose a destination bucket.';
  if (form.destination.trim() === sourceBucket) return 'The destination bucket must be different from the source bucket.';
  if (!/^arn:aws:iam::\d{12}:role\/[\w+=,.@/-]+$/.test(form.role.trim())) return 'Enter an IAM role ARN, such as arn:aws:iam::123456789012:role/replication-role.';
  return '';
}

function ruleDestination(rule: any): string {
  return String(rule.Destination?.Bucket || '').replace(/^arn:aws:s3:::/, '');
}

/** Management → Replication rules. Every rule shares the configuration's IAM role. */
export function S3ReplicationRules({ bucket, buckets, versioningStatus, notify }: { bucket: string; buckets: string[]; versioningStatus: string; notify: Notify }) {
  const [configuration, setConfiguration] = useState<{ Role?: string; Rules?: any[] } | null | undefined>(undefined);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<any[]>([]);
  const [form, setForm] = useState<ReplicationForm | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoadError('');
    try { setConfiguration(await fetchS3Replication(bucket)); } catch (err) { setConfiguration(null); setLoadError(err instanceof Error ? err.message : 'Failed to read replication rules'); }
  };
  useEffect(() => { setConfiguration(undefined); setSelected([]); load(); }, [bucket]);

  const rules = configuration?.Rules || [];
  const versioned = versioningStatus === 'Enabled';
  const formError = form ? replicationFormError(form, bucket, rules) : '';

  const save = async (role: string, next: any[], message: string) => {
    setSaving(true);
    setError('');
    try {
      await putS3Replication(bucket, { Role: role, Rules: next });
      notify('success', message);
      setSelected([]);
      await load();
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save replication rules');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const openCreate = () => {
    setError('');
    const nextPriority = rules.length ? Math.max(...rules.map(rule => Number(rule.Priority) || 0)) + 1 : 0;
    setForm({ id: '', enabled: true, priority: String(nextPriority), scope: 'all', prefix: '', destination: '', role: configuration?.Role || '', storageClass: '', deleteMarkers: false });
  };

  const submit = async () => {
    if (!form || formError) return;
    const rule: any = {
      ID: form.id.trim(),
      Priority: Number(form.priority),
      Status: form.enabled ? 'Enabled' : 'Disabled',
      Filter: form.scope === 'prefix' ? { Prefix: form.prefix.trim() } : {},
      DeleteMarkerReplication: { Status: form.deleteMarkers ? 'Enabled' : 'Disabled' },
      Destination: { Bucket: `arn:aws:s3:::${form.destination.trim()}`, ...(form.storageClass ? { StorageClass: form.storageClass } : {}) },
    };
    if (await save(form.role.trim(), [...rules, rule], `Successfully created replication rule "${rule.ID}".`)) setForm(null);
  };

  const destinations = buckets.filter(name => name !== bucket).map(name => ({ label: name, value: name }));
  return (
    <SpaceBetween size="m">
      <FlociNote>{REPLICATION_NOTE}</FlociNote>
      {loadError && <Alert type="error">{loadError}</Alert>}
      {!versioned && <Alert type="warning" header="Bucket Versioning is required">Replication requires Bucket Versioning on the source bucket. Enable versioning in Properties first.</Alert>}
      {error && !form && <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>}
      <Table
        header={
          <Header
            variant="h3"
            counter={configuration !== undefined ? `(${rules.length})` : undefined}
            description={`Use replication rules to define options you want Amazon S3 to apply during replication, such as server-side encryption, replica ownership, transitioning replicas to another storage class, and more.${configuration?.Role ? ` IAM role: ${configuration.Role}` : ''}`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!selected.length} onClick={() => setDeleting(true)}>Delete</Button>
                <Button variant="primary" disabled={!versioned} onClick={openCreate}>Create replication rule</Button>
              </SpaceBetween>
            }
          >
            Replication rules
          </Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Replication rule name', cell: rule => rule.ID || '-' },
          { id: 'status', header: 'Status', cell: rule => <StatusIndicator type={rule.Status === 'Enabled' ? 'success' : 'stopped'}>{rule.Status}</StatusIndicator>, width: 120 },
          { id: 'destination', header: 'Destination bucket', cell: rule => ruleDestination(rule) },
          { id: 'priority', header: 'Priority', cell: rule => rule.Priority ?? '-', width: 100 },
          { id: 'scope', header: 'Scope', cell: rule => (rule.Filter?.Prefix ?? rule.Prefix) ? `Prefix ${rule.Filter?.Prefix ?? rule.Prefix}` : 'Entire bucket' },
          { id: 'storageClass', header: 'Storage class', cell: rule => rule.Destination?.StorageClass ? classLabel(rule.Destination.StorageClass) : 'Same as source' },
          { id: 'deleteMarkers', header: 'Delete marker replication', cell: rule => rule.DeleteMarkerReplication?.Status || '-' },
        ]}
        items={rules}
        loading={configuration === undefined}
        loadingText="Loading replication rules"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy={rule => rule.ID || JSON.stringify(rule)}
        empty={<Box textAlign="center" color="inherit"><b>No replication rules</b><p>There are no replication rules for this bucket.</p></Box>}
      />

      <ConfirmActionModal
        visible={deleting}
        header="Delete replication rule"
        confirmLabel="Delete"
        confirmationText="delete"
        loading={saving}
        onConfirm={async () => { await save(configuration?.Role || '', rules.filter(rule => rule !== selected[0]), `Successfully deleted replication rule "${selected[0]?.ID}".`); setDeleting(false); }}
        onDismiss={() => setDeleting(false)}
      >
        Delete replication rule <strong>{selected[0]?.ID}</strong>?{rules.length === 1 ? ' It is the only rule, so the replication configuration is deleted.' : ''}
      </ConfirmActionModal>

      <Modal
        visible={Boolean(form)}
        onDismiss={() => setForm(null)}
        header="Create replication rule"
        size="large"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setForm(null)}>Cancel</Button>
              <Button variant="primary" loading={saving} disabled={Boolean(formError)} onClick={submit}>Save</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {form && (
          <SpaceBetween size="m">
            {error && <Alert type="error">{error}</Alert>}
            <Grid gridDefinition={[{ colspan: 8 }, { colspan: 4 }]}>
              <FormField label="Replication rule name">
                <Input value={form.id} onChange={({ detail }) => setForm({ ...form, id: detail.value })} ariaLabel="Replication rule name" />
              </FormField>
              <FormField label="Priority" description="Higher numbers win when rules overlap.">
                <Input type="number" value={form.priority} onChange={({ detail }) => setForm({ ...form, priority: detail.value })} ariaLabel="Priority" />
              </FormField>
            </Grid>
            <Toggle checked={form.enabled} onChange={({ detail }) => setForm({ ...form, enabled: detail.checked })}>Enabled</Toggle>
            <FormField label="Choose a rule scope">
              <RadioGroup
                value={form.scope}
                onChange={({ detail }) => setForm({ ...form, scope: detail.value as ReplicationForm['scope'] })}
                items={[
                  { value: 'prefix', label: 'Limit the scope of this rule using a prefix' },
                  { value: 'all', label: 'Apply to all objects in the bucket' },
                ]}
              />
            </FormField>
            {form.scope === 'prefix' && (
              <FormField label="Prefix"><Input value={form.prefix} onChange={({ detail }) => setForm({ ...form, prefix: detail.value })} placeholder="reports/" ariaLabel="Replication prefix" /></FormField>
            )}
            <FormField label="Destination bucket" description="A bucket in this account. It must have Bucket Versioning enabled.">
              <Select
                ariaLabel="Destination bucket"
                selectedOption={destinations.find(option => option.value === form.destination) || null}
                onChange={({ detail }) => setForm({ ...form, destination: detail.selectedOption.value! })}
                options={destinations}
                placeholder="Choose a bucket"
                empty="No other buckets"
              />
            </FormField>
            <FormField label="IAM role" description={rules.length ? 'All rules in a bucket share one IAM role; changing it here changes it for every rule.' : 'S3 assumes this role to replicate objects.'}>
              <Input value={form.role} onChange={({ detail }) => setForm({ ...form, role: detail.value })} placeholder="arn:aws:iam::123456789012:role/replication-role" ariaLabel="IAM role ARN" />
            </FormField>
            <FormField label={<>Destination storage class <i>- optional</i></>}>
              <Select
                selectedOption={form.storageClass ? { label: classLabel(form.storageClass), value: form.storageClass } : { label: 'Same as source', value: '' }}
                onChange={({ detail }) => setForm({ ...form, storageClass: detail.selectedOption.value || '' })}
                options={[{ label: 'Same as source', value: '' }, { label: 'Standard', value: 'STANDARD' }, ...STORAGE_CLASSES]}
              />
            </FormField>
            <Checkbox checked={form.deleteMarkers} onChange={({ detail }) => setForm({ ...form, deleteMarkers: detail.checked })} description="Delete markers created by S3 delete operations are replicated.">
              Delete marker replication
            </Checkbox>
            {formError && <Box color="text-status-error" data-testid="replication-form-error">{formError}</Box>}
          </SpaceBetween>
        )}
      </Modal>
    </SpaceBetween>
  );
}

// ───────────────────────── Intelligent-Tiering archive configurations ─────────────────────────

interface TieringForm { id: string; enabled: boolean; prefix: string; archive: boolean; archiveDays: string; deep: boolean; deepDays: string }

export function tieringFormError(form: TieringForm, existingIds: string[], editing: boolean): string {
  if (!form.id.trim()) return 'Configuration name is required.';
  if (!/^[\w.-]{1,64}$/.test(form.id.trim())) return 'Configuration names can be up to 64 letters, numbers, periods, hyphens and underscores.';
  if (!editing && existingIds.includes(form.id.trim())) return `A configuration named "${form.id.trim()}" already exists.`;
  if (!form.archive && !form.deep) return 'Choose at least one archive access tier.';
  if (form.archive && (!isWholeNumber(form.archiveDays) || Number(form.archiveDays) < 90 || Number(form.archiveDays) > 730)) return 'Archive Access tier days must be from 90 to 730.';
  if (form.deep && (!isWholeNumber(form.deepDays) || Number(form.deepDays) < 180 || Number(form.deepDays) > 730)) return 'Deep Archive Access tier days must be from 180 to 730.';
  if (form.archive && form.deep && Number(form.deepDays) <= Number(form.archiveDays)) return 'Deep Archive Access tier days must be more than Archive Access tier days.';
  return '';
}

function tierDays(configuration: any, tier: string): number | undefined {
  return (configuration.Tierings || []).find((t: any) => t.AccessTier === tier)?.Days;
}

/** Properties → Intelligent-Tiering Archive configurations. */
export function S3IntelligentTieringConfigs({ bucket, notify }: { bucket: string; notify: Notify }) {
  const [configurations, setConfigurations] = useState<any[] | null>(null);
  const [loadError, setLoadError] = useState('');
  const [selected, setSelected] = useState<any[]>([]);
  const [form, setForm] = useState<TieringForm | null>(null);
  const [editingExisting, setEditingExisting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoadError('');
    try { setConfigurations(await fetchS3IntelligentTiering(bucket)); } catch (err) { setConfigurations([]); setLoadError(err instanceof Error ? err.message : 'Failed to read configurations'); }
  };
  useEffect(() => { setConfigurations(null); setSelected([]); load(); }, [bucket]);

  const formError = form ? tieringFormError(form, (configurations || []).map(c => c.Id), editingExisting) : '';

  const open = (configuration?: any) => {
    setError('');
    setEditingExisting(Boolean(configuration));
    if (!configuration) {
      setForm({ id: '', enabled: true, prefix: '', archive: true, archiveDays: '90', deep: false, deepDays: '180' });
      return;
    }
    const archive = tierDays(configuration, 'ARCHIVE_ACCESS');
    const deep = tierDays(configuration, 'DEEP_ARCHIVE_ACCESS');
    setForm({
      id: configuration.Id, enabled: configuration.Status === 'Enabled', prefix: configuration.Filter?.Prefix || configuration.Filter?.And?.Prefix || '',
      archive: archive !== undefined, archiveDays: String(archive ?? 90), deep: deep !== undefined, deepDays: String(deep ?? 180),
    });
  };

  const submit = async () => {
    if (!form || formError) return;
    const existing = editingExisting ? (configurations || []).find(c => c.Id === form.id) : null;
    const keepsTags = existing?.Filter?.Tag || existing?.Filter?.And?.Tags;
    const tags = existing?.Filter?.And?.Tags || (existing?.Filter?.Tag ? [existing.Filter.Tag] : []);
    const prefix = form.prefix.trim();
    const filter = keepsTags
      ? (prefix || tags.length > 1 ? { And: { ...(prefix ? { Prefix: prefix } : {}), Tags: tags } } : { Tag: tags[0] })
      : (prefix ? { Prefix: prefix } : undefined);
    const configuration = {
      Id: form.id.trim(),
      Status: form.enabled ? 'Enabled' : 'Disabled',
      ...(filter ? { Filter: filter } : {}),
      Tierings: [
        ...(form.archive ? [{ Days: Number(form.archiveDays), AccessTier: 'ARCHIVE_ACCESS' }] : []),
        ...(form.deep ? [{ Days: Number(form.deepDays), AccessTier: 'DEEP_ARCHIVE_ACCESS' }] : []),
      ],
    };
    setSaving(true);
    setError('');
    try {
      await putS3IntelligentTiering(bucket, configuration);
      notify('success', `Successfully ${editingExisting ? 'edited' : 'created'} Intelligent-Tiering Archive configuration "${configuration.Id}".`);
      setForm(null);
      setSelected([]);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save configuration');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    const id = selected[0]?.Id;
    if (!id) return;
    setSaving(true);
    try {
      await deleteS3IntelligentTiering(bucket, id);
      notify('success', `Successfully deleted Intelligent-Tiering Archive configuration "${id}".`);
      setSelected([]);
      await load();
    } catch (err) {
      notify('error', err instanceof Error ? err.message : 'Failed to delete configuration');
    } finally {
      setSaving(false);
      setDeleting(false);
    }
  };

  return (
    <SpaceBetween size="s">
      {loadError && <Alert type="error">{loadError}</Alert>}
      <Table
        header={
          <Header
            variant="h3"
            counter={configurations ? `(${configurations.length})` : undefined}
            description="Enable objects stored in the Intelligent-Tiering storage class to tier down to the Archive Access tier or the Deep Archive Access tier, which are optimized for objects that will be rarely accessed for long periods of time."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={!selected.length} onClick={() => open(selected[0])}>Edit</Button>
                <Button disabled={!selected.length} onClick={() => setDeleting(true)}>Delete</Button>
                <Button onClick={() => open()}>Create configuration</Button>
              </SpaceBetween>
            }
          >
            Intelligent-Tiering Archive configurations
          </Header>
        }
        columnDefinitions={[
          { id: 'name', header: 'Name', cell: c => c.Id },
          { id: 'status', header: 'Status', cell: c => <StatusIndicator type={c.Status === 'Enabled' ? 'success' : 'stopped'}>{c.Status}</StatusIndicator>, width: 120 },
          { id: 'scope', header: 'Scope', cell: c => c.Filter?.Prefix || c.Filter?.And?.Prefix ? `Prefix ${c.Filter?.Prefix || c.Filter?.And?.Prefix}` : c.Filter ? 'Filtered' : 'Entire bucket' },
          { id: 'archive', header: 'Archive Access tier', cell: c => tierDays(c, 'ARCHIVE_ACCESS') !== undefined ? `${tierDays(c, 'ARCHIVE_ACCESS')} days` : '-' },
          { id: 'deep', header: 'Deep Archive Access tier', cell: c => tierDays(c, 'DEEP_ARCHIVE_ACCESS') !== undefined ? `${tierDays(c, 'DEEP_ARCHIVE_ACCESS')} days` : '-' },
        ]}
        items={configurations || []}
        loading={configurations === null}
        loadingText="Loading configurations"
        selectionType="single"
        selectedItems={selected}
        onSelectionChange={({ detail }) => setSelected(detail.selectedItems)}
        trackBy="Id"
        empty={<Box textAlign="center" color="inherit"><b>No archive configurations</b><p>There are no Intelligent-Tiering Archive configurations for this bucket.</p></Box>}
      />

      <ConfirmActionModal
        visible={deleting}
        header="Delete Intelligent-Tiering Archive configuration"
        confirmLabel="Delete"
        confirmationText="delete"
        loading={saving}
        onConfirm={remove}
        onDismiss={() => setDeleting(false)}
      >
        Delete configuration <strong>{selected[0]?.Id}</strong>? Objects already archived stay archived until they are restored.
      </ConfirmActionModal>

      <Modal
        visible={Boolean(form)}
        onDismiss={() => setForm(null)}
        header={editingExisting ? 'Edit Intelligent-Tiering Archive configuration' : 'Create Intelligent-Tiering Archive configuration'}
        size="medium"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setForm(null)}>Cancel</Button>
              <Button variant="primary" loading={saving} disabled={Boolean(formError)} onClick={submit}>{editingExisting ? 'Save changes' : 'Create'}</Button>
            </SpaceBetween>
          </Box>
        }
      >
        {form && (
          <SpaceBetween size="m">
            <FlociNote>{INTELLIGENT_TIERING_NOTE}</FlociNote>
            {error && <Alert type="error">{error}</Alert>}
            <FormField label="Configuration name">
              <Input value={form.id} disabled={editingExisting} onChange={({ detail }) => setForm({ ...form, id: detail.value })} ariaLabel="Configuration name" />
            </FormField>
            <FormField label={<>Prefix <i>- optional</i></>} description="Leave empty to apply the configuration to every Intelligent-Tiering object in the bucket.">
              <Input value={form.prefix} onChange={({ detail }) => setForm({ ...form, prefix: detail.value })} placeholder="archive/" ariaLabel="Configuration prefix" />
            </FormField>
            <Toggle checked={form.enabled} onChange={({ detail }) => setForm({ ...form, enabled: detail.checked })}>Enabled</Toggle>
            <Checkbox checked={form.archive} onChange={({ detail }) => setForm({ ...form, archive: detail.checked })}>Archive Access tier</Checkbox>
            {form.archive && (
              <FormField label="Days until transition to the Archive Access tier" constraintText="90 to 730 days">
                <Input type="number" value={form.archiveDays} onChange={({ detail }) => setForm({ ...form, archiveDays: detail.value })} ariaLabel="Archive Access tier days" />
              </FormField>
            )}
            <Checkbox checked={form.deep} onChange={({ detail }) => setForm({ ...form, deep: detail.checked })}>Deep Archive Access tier</Checkbox>
            {form.deep && (
              <FormField label="Days until transition to the Deep Archive Access tier" constraintText="180 to 730 days">
                <Input type="number" value={form.deepDays} onChange={({ detail }) => setForm({ ...form, deepDays: detail.value })} ariaLabel="Deep Archive Access tier days" />
              </FormField>
            )}
            {formError && <Box color="text-status-error" data-testid="tiering-form-error">{formError}</Box>}
          </SpaceBetween>
        )}
      </Modal>
    </SpaceBetween>
  );
}
