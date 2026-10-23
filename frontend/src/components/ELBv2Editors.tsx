import { useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import Container from '@cloudscape-design/components/container';
import FormField from '@cloudscape-design/components/form-field';
import Header from '@cloudscape-design/components/header';
import Input from '@cloudscape-design/components/input';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Modal from '@cloudscape-design/components/modal';
import RadioGroup from '@cloudscape-design/components/radio-group';
import Select from '@cloudscape-design/components/select';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Table from './ResourceTable';
import { FlociNote } from './FlociNote';
import {
  createElbv2Rule,
  deleteElbv2Rule,
  modifyElbv2HealthCheck,
  modifyElbv2Listener,
  modifyElbv2Rule,
  modifyElbv2TargetGroupAttributes,
} from '../api/client';

type Notify = (type: 'success' | 'error', text: string) => void;

const toNumber = (value: string) => (value.trim() === '' ? undefined : Number(value));

function ModalFooter({ onCancel, onSubmit, busy, label }: { onCancel: () => void; onSubmit: () => void; busy: boolean; label: string }) {
  return <Box float="right"><SpaceBetween direction="horizontal" size="xs">
    <Button variant="link" onClick={onCancel}>Cancel</Button>
    <Button variant="primary" loading={busy} onClick={onSubmit}>{label}</Button>
  </SpaceBetween></Box>;
}

/** Text for the Matcher Floci returns ({ HttpCode: '200' }). */
export function matcherText(matcher: any): string {
  if (!matcher) return '';
  if (typeof matcher === 'string') return matcher;
  return matcher.HttpCode || matcher.GrpcCode || '';
}

export const STICKINESS_NOTE = 'Floci stores stickiness and the deregistration delay and reports them back, but Floci 2.3.0 doesn’t apply them: the load balancer sends requests to healthy targets in turn without setting a stickiness cookie, and a deregistered target stops receiving requests at once, with no draining period. Newer Floci builds apply duration-based (load balancer cookie) stickiness.';

export const HEALTH_CHECK_NOTE = 'Floci runs these health checks against each target and applies a new path, timeout, thresholds and success codes at once. A changed interval is reported, but Floci keeps probing at the interval the target group was created with. When every target is unhealthy, the load balancer sends requests to all of them, as AWS does.';

export function TargetGroupHealthChecks({ group, onSaved, notify }: { group: any; onSaved: () => Promise<void> | void; notify: Notify }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ path: '', interval: '', timeout: '', healthy: '', unhealthy: '', matcher: '' });
  const isLambda = group.TargetType === 'lambda';
  const start = () => {
    setForm({
      path: group.HealthCheckPath || '/',
      interval: String(group.HealthCheckIntervalSeconds ?? ''),
      timeout: String(group.HealthCheckTimeoutSeconds ?? ''),
      healthy: String(group.HealthyThresholdCount ?? ''),
      unhealthy: String(group.UnhealthyThresholdCount ?? ''),
      matcher: matcherText(group.Matcher),
    });
    setOpen(true);
  };
  const save = async () => {
    setBusy(true);
    try {
      await modifyElbv2HealthCheck(group.TargetGroupArn, {
        path: form.path.trim(),
        interval_seconds: toNumber(form.interval),
        timeout_seconds: toNumber(form.timeout),
        healthy_threshold: toNumber(form.healthy),
        unhealthy_threshold: toNumber(form.unhealthy),
        matcher: form.matcher.trim(),
      });
      notify('success', `Health check settings for "${group.TargetGroupName}" saved.`);
      setOpen(false);
      await onSaved();
    } catch (err: any) {
      notify('error', err.message || 'Failed to save health check settings');
    } finally {
      setBusy(false);
    }
  };
  const field = (key: keyof typeof form, label: string, description?: string) => (
    <FormField label={label} description={description}>
      <Input ariaLabel={label} value={form[key]} onChange={({ detail }) => setForm(prev => ({ ...prev, [key]: detail.value }))} />
    </FormField>
  );
  return <Container header={<Header variant="h2" description={`Health checks for "${group.TargetGroupName}"`} actions={!isLambda && <Button onClick={start}>Edit health check settings</Button>}>Health check settings</Header>}>
    <SpaceBetween size="m">
      {isLambda ? <Box>Lambda target groups have no health check settings to edit here.</Box> : <KeyValuePairs columns={3} items={[
        { label: 'Protocol', value: group.HealthCheckProtocol || group.health_check_protocol || 'Not reported' },
        { label: 'Path', value: group.HealthCheckPath || 'Not reported' },
        { label: 'Success codes', value: matcherText(group.Matcher) || 'Not reported' },
        { label: 'Interval', value: group.HealthCheckIntervalSeconds != null ? `${group.HealthCheckIntervalSeconds} seconds` : 'Not reported' },
        { label: 'Timeout', value: group.HealthCheckTimeoutSeconds != null ? `${group.HealthCheckTimeoutSeconds} seconds` : 'Not reported' },
        { label: 'Healthy threshold', value: group.HealthyThresholdCount ?? 'Not reported' },
        { label: 'Unhealthy threshold', value: group.UnhealthyThresholdCount ?? 'Not reported' },
      ]} />}
      {!isLambda && <FlociNote>{HEALTH_CHECK_NOTE}</FlociNote>}
    </SpaceBetween>
    <Modal visible={open} onDismiss={() => setOpen(false)} header="Edit health check settings" footer={<ModalFooter onCancel={() => setOpen(false)} onSubmit={save} busy={busy} label="Save changes" />}>
      <SpaceBetween size="m">
        {field('path', 'Health check path', 'Must start with /.')}
        {field('matcher', 'Success codes', 'For example 200, 200,202 or 200-299.')}
        {field('healthy', 'Healthy threshold', '2 to 10 consecutive successes.')}
        {field('unhealthy', 'Unhealthy threshold', '2 to 10 consecutive failures.')}
        {field('timeout', 'Timeout', '2 to 120 seconds, shorter than the interval.')}
        {field('interval', 'Interval', '5 to 300 seconds.')}
        <FlociNote>{HEALTH_CHECK_NOTE}</FlociNote>
      </SpaceBetween>
    </Modal>
  </Container>;
}

export function TargetGroupAttributes({ group, onSaved, notify }: { group: any; onSaved: () => Promise<void> | void; notify: Notify }) {
  const attributes: Record<string, string> = group.Attributes || {};
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [delay, setDelay] = useState('');
  const [sticky, setSticky] = useState(false);
  const [stickyType, setStickyType] = useState<'lb_cookie' | 'app_cookie'>('lb_cookie');
  const [duration, setDuration] = useState('');
  const [cookieName, setCookieName] = useState('');
  const isLambda = group.TargetType === 'lambda';
  const reported = Object.keys(attributes).length > 0;
  const type = (attributes['stickiness.type'] || 'lb_cookie') as 'lb_cookie' | 'app_cookie';
  const start = () => {
    setDelay(attributes['deregistration_delay.timeout_seconds'] ?? '300');
    setSticky(attributes['stickiness.enabled'] === 'true');
    setStickyType(type);
    setDuration(attributes[`stickiness.${type}.duration_seconds`] ?? '86400');
    setCookieName(attributes['stickiness.app_cookie.cookie_name'] || '');
    setOpen(true);
  };
  const save = async () => {
    setBusy(true);
    try {
      await modifyElbv2TargetGroupAttributes(group.TargetGroupArn, {
        deregistration_delay_seconds: toNumber(delay),
        stickiness_enabled: sticky,
        ...(sticky ? { stickiness_type: stickyType, stickiness_duration_seconds: toNumber(duration), app_cookie_name: stickyType === 'app_cookie' ? cookieName.trim() : undefined } : {}),
      });
      notify('success', `Attributes for "${group.TargetGroupName}" saved. Floci stores stickiness and the deregistration delay but doesn't apply them.`);
      setOpen(false);
      await onSaved();
    } catch (err: any) {
      notify('error', err.message || 'Failed to save target group attributes');
    } finally {
      setBusy(false);
    }
  };
  const stickinessValue = attributes['stickiness.enabled'] === 'true'
    ? `On (${type === 'app_cookie' ? 'application-based cookie' : 'load balancer generated cookie'})`
    : attributes['stickiness.enabled'] === 'false' ? 'Off' : 'Not reported';
  return <Container header={<Header variant="h2" description={`Attributes of "${group.TargetGroupName}"`} actions={!isLambda && <Button onClick={start} disabled={!reported}>Edit attributes</Button>}>Attributes</Header>}>
    <SpaceBetween size="m">
      {isLambda ? <Box>Lambda target groups have no stickiness or deregistration delay.</Box> : !reported ? <Box>Floci didn't report this target group's attributes.</Box> : <KeyValuePairs columns={3} items={[
        { label: 'Deregistration delay', value: `${attributes['deregistration_delay.timeout_seconds'] ?? 'Not reported'} seconds` },
        { label: 'Stickiness', value: stickinessValue },
        { label: 'Stickiness duration', value: attributes['stickiness.enabled'] === 'true' ? `${attributes[`stickiness.${type}.duration_seconds`] ?? 'Not reported'} seconds` : '-' },
        ...(type === 'app_cookie' && attributes['stickiness.enabled'] === 'true' ? [{ label: 'App cookie name', value: attributes['stickiness.app_cookie.cookie_name'] || 'Not reported' }] : []),
        { label: 'Load balancing algorithm', value: attributes['load_balancing.algorithm.type'] || 'Not reported' },
        { label: 'Slow start duration', value: attributes['slow_start.duration_seconds'] != null ? `${attributes['slow_start.duration_seconds']} seconds` : 'Not reported' },
      ]} />}
      {!isLambda && <FlociNote>{STICKINESS_NOTE}</FlociNote>}
    </SpaceBetween>
    <Modal visible={open} onDismiss={() => setOpen(false)} header="Edit target group attributes" footer={<ModalFooter onCancel={() => setOpen(false)} onSubmit={save} busy={busy} label="Save changes" />}>
      <SpaceBetween size="m">
        <FormField label="Deregistration delay (draining interval)" description="0 to 3600 seconds. How long the load balancer lets in-flight requests finish before a deregistered target is removed.">
          <Input ariaLabel="Deregistration delay" type="number" value={delay} onChange={({ detail }) => setDelay(detail.value)} />
        </FormField>
        <Checkbox checked={sticky} onChange={({ detail }) => setSticky(detail.checked)}>Turn on stickiness</Checkbox>
        {sticky && <>
          <FormField label="Stickiness type">
            <RadioGroup value={stickyType} onChange={({ detail }) => { setStickyType(detail.value as 'lb_cookie' | 'app_cookie'); setDuration(attributes[`stickiness.${detail.value}.duration_seconds`] ?? '86400'); }} items={[
              { value: 'lb_cookie', label: 'Load balancer generated cookie', description: 'The load balancer sets an AWSALB cookie.' },
              { value: 'app_cookie', label: 'Application-based cookie', description: 'The load balancer follows a cookie your application sets.' },
            ]} />
          </FormField>
          <FormField label="Stickiness duration" description="1 second to 7 days (604800 seconds).">
            <Input ariaLabel="Stickiness duration" type="number" value={duration} onChange={({ detail }) => setDuration(detail.value)} />
          </FormField>
          {stickyType === 'app_cookie' && <FormField label="App cookie name" description="Names starting with AWSALB, AWSALBAPP or AWSALBTG are reserved.">
            <Input ariaLabel="App cookie name" value={cookieName} onChange={({ detail }) => setCookieName(detail.value)} />
          </FormField>}
        </>}
        <FlociNote>{STICKINESS_NOTE}</FlociNote>
      </SpaceBetween>
    </Modal>
  </Container>;
}

const targetGroupOptions = (groups: any[]) => groups.map((item: any) => ({ label: item.TargetGroupName || item.TargetGroupArn, value: item.TargetGroupArn }));

/** The target group a forward action sends to (plain or weighted ForwardConfig). */
export function forwardTarget(action: any): string {
  return action?.TargetGroupArn || action?.ForwardConfig?.TargetGroups?.[0]?.TargetGroupArn || '';
}

export function describeAction(action: any, groups: any[]): string {
  if (!action) return 'Not reported';
  if (action.Type === 'forward') {
    const arn = forwardTarget(action);
    const group = groups.find((item: any) => item.TargetGroupArn === arn);
    return `Forward to ${group?.TargetGroupName || arn || 'Not reported'}`;
  }
  if (action.Type === 'fixed-response') return `Return fixed response ${action.FixedResponseConfig?.StatusCode || ''}`.trim();
  if (action.Type === 'redirect') return 'Redirect';
  return action.Type || 'Not reported';
}

export function ListenerEditModal({ listener, targetGroups, onClose, onSaved, notify }: { listener: any | null; targetGroups: any[]; onClose: () => void; onSaved: () => Promise<void> | void; notify: Notify }) {
  const current = listener?.DefaultActions?.[0];
  const [port, setPort] = useState('');
  const [actionType, setActionType] = useState<'forward' | 'fixed-response'>('forward');
  const [tgArn, setTgArn] = useState('');
  const [statusCode, setStatusCode] = useState('200');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadedFor, setLoadedFor] = useState('');
  if (listener && loadedFor !== listener.ListenerArn) {
    setLoadedFor(listener.ListenerArn);
    setPort(String(listener.Port ?? ''));
    setActionType(current?.Type === 'fixed-response' ? 'fixed-response' : 'forward');
    setTgArn(forwardTarget(current) || targetGroups[0]?.TargetGroupArn || '');
    setStatusCode(current?.FixedResponseConfig?.StatusCode || '200');
    setBody(current?.FixedResponseConfig?.MessageBody || '');
  }
  const close = () => { setLoadedFor(''); onClose(); };
  const save = async () => {
    if (!listener) return;
    setBusy(true);
    try {
      await modifyElbv2Listener(listener.ListenerArn, {
        port: toNumber(port),
        action_type: actionType,
        ...(actionType === 'forward' ? { target_group_arn: tgArn } : { status_code: toNumber(statusCode), message_body: body }),
      });
      notify('success', `Listener ${listener.Protocol}:${port} updated.`);
      close();
      await onSaved();
    } catch (err: any) {
      notify('error', err.message || 'Failed to update listener');
    } finally {
      setBusy(false);
    }
  };
  const selectedTg = targetGroupOptions(targetGroups).find(option => option.value === tgArn) || (tgArn ? { label: tgArn, value: tgArn } : null);
  return <Modal visible={!!listener} onDismiss={close} header={`Edit listener ${listener ? `${listener.Protocol}:${listener.Port}` : ''}`} footer={<ModalFooter onCancel={close} onSubmit={save} busy={busy} label="Save changes" />}>
    <SpaceBetween size="m">
      <FormField label="Protocol" description="Floci keeps the listener's protocol; change the port and the default action here.">
        <Input ariaLabel="Listener protocol" value={listener?.Protocol || ''} disabled />
      </FormField>
      <FormField label="Port" description="1 to 65535. Floci moves the listener to the new port at once.">
        <Input ariaLabel="Listener port" type="number" value={port} onChange={({ detail }) => setPort(detail.value)} />
      </FormField>
      <FormField label="Default action">
        <RadioGroup value={actionType} onChange={({ detail }) => setActionType(detail.value as 'forward' | 'fixed-response')} items={[
          { value: 'forward', label: 'Forward to target group' },
          { value: 'fixed-response', label: 'Return fixed response' },
        ]} />
      </FormField>
      {actionType === 'forward' ? <FormField label="Target group">
        <Select ariaLabel="Default target group" selectedOption={selectedTg} placeholder="Choose a target group" options={targetGroupOptions(targetGroups)} onChange={({ detail }) => setTgArn(detail.selectedOption.value || '')} />
      </FormField> : <>
        <FormField label="Response code" description="A 2XX, 4XX or 5XX code.">
          <Input ariaLabel="Response code" type="number" value={statusCode} onChange={({ detail }) => setStatusCode(detail.value)} />
        </FormField>
        <FormField label="Response body (optional)">
          <Input ariaLabel="Response body" value={body} onChange={({ detail }) => setBody(detail.value)} />
        </FormField>
      </>}
    </SpaceBetween>
  </Modal>;
}

const rulePath = (rule: any) => (rule.Conditions || []).find((condition: any) => condition.Field === 'path-pattern');
const rulePathText = (rule: any) => {
  const condition = rulePath(rule);
  return (condition?.PathPatternConfig?.Values || condition?.Values || []).join(', ');
};
const ruleConditionsText = (rule: any) => {
  if (rule.IsDefault || rule.Priority === 'default') return 'If no other rule applies';
  const parts = (rule.Conditions || []).map((condition: any) => {
    const values = condition.PathPatternConfig?.Values || condition.HostHeaderConfig?.Values || condition.Values || [];
    return `${condition.Field} is ${values.join(' or ')}`;
  });
  return parts.length ? parts.join('; ') : 'Not reported';
};

export function ListenerRules({ listener, rules, targetGroups, onSaved, notify }: { listener: any; rules: any[]; targetGroups: any[]; onSaved: () => Promise<void> | void; notify: Notify }) {
  const [editing, setEditing] = useState<any | null>(null);
  const [creating, setCreating] = useState(false);
  const [priority, setPriority] = useState('');
  const [path, setPath] = useState('');
  const [tgArn, setTgArn] = useState('');
  const [busy, setBusy] = useState(false);
  const listenerRules = rules
    .filter((rule: any) => rule.ListenerArn === listener.ListenerArn)
    .sort((a: any, b: any) => (a.Priority === 'default' ? 1 : b.Priority === 'default' ? -1 : Number(a.Priority) - Number(b.Priority)));
  const openCreate = () => { setPriority(''); setPath('/api/*'); setTgArn(targetGroups[0]?.TargetGroupArn || ''); setCreating(true); };
  const openEdit = (rule: any) => { setPriority(String(rule.Priority)); setPath(rulePathText(rule)); setTgArn(forwardTarget((rule.Actions || [])[0])); setEditing(rule); };
  const close = () => { setCreating(false); setEditing(null); };
  const save = async () => {
    setBusy(true);
    try {
      if (editing) {
        await modifyElbv2Rule(editing.RuleArn, {
          path_pattern: path.trim() !== rulePathText(editing) ? path.trim() : undefined,
          target_group_arn: tgArn !== forwardTarget((editing.Actions || [])[0]) ? tgArn : undefined,
          priority: priority !== String(editing.Priority) ? toNumber(priority) : undefined,
        });
        notify('success', `Rule ${priority} updated.`);
      } else {
        await createElbv2Rule({ listener_arn: listener.ListenerArn, priority: Number(priority), path_pattern: path.trim(), target_group_arn: tgArn });
        notify('success', `Rule ${priority} added.`);
      }
      close();
      await onSaved();
    } catch (err: any) {
      notify('error', err.message || 'Failed to save rule');
    } finally {
      setBusy(false);
    }
  };
  const remove = async (rule: any) => {
    if (!confirm(`Delete rule ${rule.Priority}? Requests it matched go to the next rule or the default action.`)) return;
    try {
      await deleteElbv2Rule(rule.RuleArn);
      notify('success', `Rule ${rule.Priority} deleted.`);
      await onSaved();
    } catch (err: any) {
      notify('error', err.message || 'Failed to delete rule');
    }
  };
  const selectedTg = targetGroupOptions(targetGroups).find(option => option.value === tgArn) || (tgArn ? { label: tgArn, value: tgArn } : null);
  const editingPathRule = !editing || rulePath(editing);
  return <>
    <Table
      header={<Header variant="h3" counter={`(${listenerRules.length})`} description="Rules are evaluated in priority order, lowest first; the default rule applies when no other rule matches." actions={<Button onClick={openCreate}>Add rule</Button>}>Rules for {listener.Protocol}:{listener.Port}</Header>}
      items={listenerRules}
      columnDefinitions={[
        { id: 'priority', header: 'Priority', cell: (rule: any) => rule.Priority ?? 'Not reported', width: 110 },
        { id: 'conditions', header: 'Conditions (If)', cell: (rule: any) => ruleConditionsText(rule) },
        { id: 'actions', header: 'Actions (Then)', cell: (rule: any) => describeAction((rule.Actions || [])[0], targetGroups) },
        { id: 'manage', header: 'Manage', cell: (rule: any) => rule.IsDefault || rule.Priority === 'default'
          ? <Box color="text-status-inactive">Edit the listener to change the default action</Box>
          : <SpaceBetween direction="horizontal" size="xs">
            <Button ariaLabel={`Edit rule ${rule.Priority}`} onClick={() => openEdit(rule)}>Edit</Button>
            <Button ariaLabel={`Delete rule ${rule.Priority}`} iconName="remove" onClick={() => remove(rule)}>Delete</Button>
          </SpaceBetween> },
      ]}
      empty={<Box textAlign="center">No rules reported for this listener.</Box>}
    />
    <Modal visible={creating || !!editing} onDismiss={close} header={editing ? `Edit rule ${editing.Priority}` : `Add rule to ${listener.Protocol}:${listener.Port}`} footer={<ModalFooter onCancel={close} onSubmit={save} busy={busy} label={editing ? 'Save changes' : 'Add rule'} />}>
      <SpaceBetween size="m">
        <FormField label="Priority" description="1 to 50000. Lower numbers are evaluated first; priorities must be unique on a listener.">
          <Input ariaLabel="Rule priority" type="number" value={priority} onChange={({ detail }) => setPriority(detail.value)} />
        </FormField>
        {editingPathRule ? <FormField label="Path pattern" description="For example /api/* or /images/*.png. Case sensitive.">
          <Input ariaLabel="Path pattern" value={path} onChange={({ detail }) => setPath(detail.value)} />
        </FormField> : <Box>This rule has no path condition; its conditions are kept.</Box>}
        <FormField label="Forward to target group">
          <Select ariaLabel="Rule target group" selectedOption={selectedTg} placeholder="Choose a target group" options={targetGroupOptions(targetGroups)} onChange={({ detail }) => setTgArn(detail.selectedOption.value || '')} />
        </FormField>
      </SpaceBetween>
    </Modal>
  </>;
}
