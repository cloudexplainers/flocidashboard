import { IdentityInfo, LabDefinition, ServiceAction, ServiceDefinition } from '../types';

function getCsrfToken(): string {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) {
    return meta.getAttribute('content') || '';
  }
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : '';
}

export async function fetchCloudWatchLogs(path: 'log-streams' | 'log-events', payload: Record<string, unknown>): Promise<any> {
  const response = await fetch(`/api/cloudwatch/${path}/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || 'Unable to load CloudWatch logs');
  return data;
}

export async function fetchServices(): Promise<ServiceDefinition[]> {
  try {
    const res = await fetch('/api/services/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.services || [];
  } catch (err) {
    console.error('Failed to fetch services:', err);
    return [];
  }
}

export async function fetchIdentity(): Promise<IdentityInfo> {
  try {
    const res = await fetch('/api/session-identity/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const caller = data.caller_identity || {};
    return {
      account_id: caller.Account || data.account_id || 'Unknown',
      user_id: caller.UserId || data.user_id || 'Unknown',
      arn: caller.Arn || data.arn || 'Unavailable',
      role: data.session_identity?.label || data.role || 'Current session',
      region: data.region || 'Unknown',
      endpoint: data.endpoint_url || data.endpoint || 'Unavailable',
    };
  } catch {
    return { account_id: 'Unknown', user_id: 'Unavailable', arn: 'Unavailable', role: 'Unavailable', region: 'Unknown', endpoint: 'Unavailable' };
  }
}

export async function fetchServiceInventory(serviceKey: string): Promise<any> {
  try {
    const res = await fetch(`/api/${serviceKey}/`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    window.dispatchEvent(new CustomEvent('floci_load_error', { detail: `Unable to load ${serviceKey} resources. Refresh to retry. ${err instanceof Error ? err.message : ''}` }));
    console.error(`Failed to fetch inventory for ${serviceKey}:`, err);
    return {};
  }
}

export const fetchInventory = fetchServiceInventory;

export async function fetchLabs(serviceKey?: string): Promise<LabDefinition[]> {
  try {
    const url = serviceKey ? `/api/labs/catalog/?service=${serviceKey}` : '/api/labs/catalog/';
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.labs || [];
  } catch (err) {
    console.error('Failed to fetch labs:', err);
    return [];
  }
}

export async function fetchLabsCatalog(): Promise<{ services: Array<{ service_key: string; service_title: string; lab_count: number; labs: LabDefinition[] }>; total_labs: number }> {
  try {
    const res = await fetch('/api/labs/catalog/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch labs catalog:', err);
    return { services: [], total_labs: 0 };
  }
}

export async function fetchLabsProgress(): Promise<{ completed_lab_count: number; labs: Array<{ service: string; key: string; complete: boolean }> }> {
  try {
    const res = await fetch('/api/labs/progress/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch labs progress:', err);
    return { completed_lab_count: 0, labs: [] };
  }
}

export async function fetchLabStatus(serviceKey: string, labKey: string): Promise<{ complete: boolean; error?: string; steps: Record<string, { verified: boolean; verification?: any }> }> {
  const res = await fetch(`/api/labs/${serviceKey}/${labKey}/status/`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export async function runLabStep(
  serviceKey: string,
  labKey: string,
  stepKey: string
): Promise<any> {
  const res = await fetch(`/api/labs/${serviceKey}/${labKey}/steps/${stepKey}/run/`, {
    method: 'POST',
    headers: {
      'X-CSRFToken': getCsrfToken(),
    },
  });
  const data = await res.json();
  if (!res.ok || data.error) {
    throw new Error(data.error || 'Step failed');
  }
  return data;
}

export async function resetLab(serviceKey: string, labKey: string): Promise<any> {
  const res = await fetch(`/api/labs/${serviceKey}/${labKey}/reset/`, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export async function resetAllLabs(): Promise<any> {
  const res = await fetch('/api/labs/reset-all/', {
    method: 'POST',
    headers: {
      'X-CSRFToken': getCsrfToken(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ all: true }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function fetchSesMessages(): Promise<{ mailbox_url: string; messages: any[]; raw: any }> {
  try {
    const res = await fetch('/api/inspector/ses/messages/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    window.dispatchEvent(new CustomEvent('floci_load_error', { detail: 'Unable to load SES messages. Refresh to retry.' }));
    console.error('Failed to fetch SES messages:', err);
    return { mailbox_url: '', messages: [], raw: {} };
  }
}

export async function clearSesMessages(): Promise<any> {
  const res = await fetch('/api/inspector/ses/messages/clear/', {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function fetchInspectorSqsQueues(): Promise<{ queues: Array<{ name: string; url: string; arn: string; available: number; in_flight: number }> }> {
  try {
    const res = await fetch('/api/inspector/sqs/queues/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    window.dispatchEvent(new CustomEvent('floci_load_error', { detail: 'Unable to load SQS queues. Refresh to retry.' }));
    console.error('Failed to fetch SQS queues:', err);
    return { queues: [] };
  }
}

export async function fetchInspectorSqsMessages(queueUrl: string, maxNumber: number = 10): Promise<{ queue_url: string; queue_name: string; messages: any[] }> {
  try {
    const res = await fetch(`/api/inspector/sqs/messages/?queue_url=${encodeURIComponent(queueUrl)}&max_number=${maxNumber}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    window.dispatchEvent(new CustomEvent('floci_load_error', { detail: 'Unable to load SQS messages. Refresh to retry.' }));
    console.error('Failed to fetch SQS messages:', err);
    return { queue_url: queueUrl, queue_name: '', messages: [] };
  }
}

export async function fetchInspectorLogGroups(): Promise<{ log_groups: Array<{ logGroupName: string; creationTime: number; storedBytes: number }> }> {
  try {
    const res = await fetch('/api/inspector/lambda/log-groups/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    window.dispatchEvent(new CustomEvent('floci_load_error', { detail: 'Unable to load Lambda log groups. Refresh to retry.' }));
    console.error('Failed to fetch Lambda log groups:', err);
    return { log_groups: [] };
  }
}

export async function fetchInspectorLogEvents(logGroupName: string, limit: number = 50): Promise<{ log_group_name: string; streams: any[]; events: Array<{ timestamp: number; message: string; ingestionTime: number; logStreamName: string }> }> {
  try {
    const res = await fetch(`/api/inspector/lambda/log-events/?log_group_name=${encodeURIComponent(logGroupName)}&limit=${limit}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    window.dispatchEvent(new CustomEvent('floci_load_error', { detail: 'Unable to load Lambda log events. Refresh to retry.' }));
    console.error('Failed to fetch Lambda log events:', err);
    return { log_group_name: logGroupName, streams: [], events: [] };
  }
}

export async function fetchSettings(): Promise<any> {
  try {
    const res = await fetch('/api/settings/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch settings:', err);
    return {};
  }
}

export async function saveEndpoint(endpointUrl: string): Promise<any> {
  const res = await fetch('/api/settings/endpoint/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ endpoint_url: endpointUrl }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to save endpoint');
  window.dispatchEvent(new Event('floci_session_changed'));
  return data;
}

export async function resetEndpoint(): Promise<any> {
  const res = await fetch('/api/settings/endpoint/reset/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const result = await res.json();
  window.dispatchEvent(new Event('floci_session_changed'));
  return result;
}

export async function testConnection(endpointUrl?: string): Promise<any> {
  const res = await fetch('/api/settings/test-connection/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(endpointUrl ? { endpoint_url: endpointUrl } : {}),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Connection test failed');
  return data;
}

export async function resetFlociState(): Promise<any> {
  const res = await fetch('/api/settings/floci-reset/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Floci reset failed');
  return data;
}

export async function fetchIdentityDetail(): Promise<any> {
  try {
    const res = await fetch('/api/session-identity/');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch identity detail:', err);
    return {};
  }
}

export async function useAdminIdentity(): Promise<any> {
  const res = await fetch('/api/session-identity/use-admin/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const result = await res.json();
  window.dispatchEvent(new Event('floci_session_changed'));
  return result;
}

export async function useUserIdentity(userName: string, rotateAccessKeys: boolean = false): Promise<any> {
  const res = await fetch('/api/session-identity/use-user/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ user_name: userName, rotate_access_keys: rotateAccessKeys }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to switch user identity');
  window.dispatchEvent(new Event('floci_session_changed'));
  return data;
}

export async function assumeRoleIdentity(roleName: string, sessionName?: string, accountId?: string): Promise<any> {
  const res = await fetch('/api/session-identity/assume-role/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ role_name: roleName, session_name: sessionName, account_id: accountId }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to assume role');
  window.dispatchEvent(new Event('floci_session_changed'));
  return data;
}

export async function clearSessionIdentity(): Promise<any> {
  const res = await fetch('/api/session-identity/clear/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const result = await res.json();
  window.dispatchEvent(new Event('floci_session_changed'));
  return result;
}

export async function runCliCommand(command: string, confirmed: boolean = false): Promise<any> {
  const res = await fetch('/api/console/run/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ command, confirmed }),
  });
  const data = await res.json();
  if (!res.ok && !data.confirmation_required) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

export function executeServiceAction(
  serviceKey: string,
  actionKey: string,
  payload: Record<string, any>
): Promise<any>;
export function executeServiceAction(
  action: ServiceAction,
  formValues: Record<string, any>,
  fileValues?: Record<string, File>
): Promise<any>;
export async function executeServiceAction(
  actionOrServiceKey: string | ServiceAction,
  actionKeyOrFormValues: string | Record<string, any>,
  payloadOrFileValues: Record<string, any> = {}
): Promise<any> {
  if (typeof actionOrServiceKey === 'string') {
    const serviceKey = actionOrServiceKey;
    const actionKey = actionKeyOrFormValues as string;
    const payload = payloadOrFileValues as Record<string, any>;
    const res = await fetch(`/api/${serviceKey}/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      body: JSON.stringify({
        action: actionKey,
        ...payload,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      throw new Error(data.error || `Action failed with status ${res.status}`);
    }
    return data;
  }

  const action = actionOrServiceKey;
  const formValues = (actionKeyOrFormValues || {}) as Record<string, any>;
  const fileValues = (payloadOrFileValues || {}) as Record<string, File>;

  let targetPath = action.path;
  const queryParams = new URLSearchParams();
  const payload: Record<string, any> = {};
  const hasFiles = Object.keys(fileValues).length > 0;

  // Substitute {param} in path or populate payload
  (action.fields || []).forEach((f) => {
    const value = formValues[f.name];
    if (targetPath.includes(`{${f.name}}`)) {
      targetPath = targetPath.replace(`{${f.name}}`, encodeURIComponent(value != null ? String(value) : ''));
    } else if (value !== undefined && value !== '') {
      if (action.method === 'GET') {
        queryParams.set(f.name, String(value));
      } else {
        if (f.field_type === 'number') {
          payload[f.name] = Number(value);
        } else if (f.field_type === 'boolean') {
          payload[f.name] = Boolean(value);
        } else if (f.field_type === 'object' || f.field_type === 'array') {
          try {
            payload[f.name] = typeof value === 'string' ? JSON.parse(value) : value;
          } catch {
            payload[f.name] = value;
          }
        } else {
          payload[f.name] = value;
        }
      }
    }
  });

  const queryString = queryParams.toString();
  const fullUrl = queryString ? `${targetPath}?${queryString}` : targetPath;

  const options: RequestInit = {
    method: action.method || 'POST',
    headers: {
      'X-CSRFToken': getCsrfToken(),
    },
  };

  if (action.method !== 'GET') {
    if (hasFiles) {
      const formData = new FormData();
      Object.entries(payload).forEach(([k, v]) => {
        formData.append(k, typeof v === 'object' ? JSON.stringify(v) : String(v));
      });
      Object.entries(fileValues).forEach(([k, file]) => {
        formData.append(k, file);
      });
      options.body = formData;
    } else {
      (options.headers as Record<string, string>)['Content-Type'] = 'application/json';
      options.body = JSON.stringify(payload);
    }
  }

  const res = await fetch(fullUrl, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) {
    throw new Error(data.error || `HTTP ${res.status}: ${res.statusText}`);
  }
  return data;
}

// S3 Deepening APIs
export async function fetchS3Objects(bucketName: string, prefix: string = ''): Promise<{ contents: any[]; folders: any[]; count: number }> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/objects/?prefix=${encodeURIComponent(prefix)}&delimiter=`);
    if (!res.ok) return { contents: [], folders: [], count: 0 };
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch S3 objects:', err);
    return { contents: [], folders: [], count: 0 };
  }
}

export async function presignS3Object(bucketName: string, key: string, expiresIn: number = 3600): Promise<{ url: string; expires_in: number }> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/objects/presign/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ key, expires_in: expiresIn }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to generate presigned URL`);
  return data;
}

export async function fetchS3Website(bucketName: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/website/`);
    if (!res.ok) return null;
    const data = await res.json();
    return data.configuration || null;
  } catch {
    return null;
  }
}

export async function putS3Website(bucketName: string, config: { IndexDocument: { Suffix: string }; ErrorDocument?: { Key: string } }): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/website/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ configuration: config }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to update website configuration`);
  return data;
}

export async function deleteS3Website(bucketName: string): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/website/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function fetchS3Notifications(bucketName: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/notifications/`);
    if (!res.ok) return {};
    return await res.json();
  } catch {
    return {};
  }
}

export async function putS3Notifications(bucketName: string, config: any): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/notifications/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(config),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to update notifications`);
  return data;
}

export async function deleteS3Object(bucketName: string, key: string): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/objects/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ keys: [{ Key: key }] }),
  });
  return await res.json();
}

export async function createS3Folder(bucketName: string, folder: string): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/folders/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ folder }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to create folder`);
  return data;
}

// IAM Deepening APIs
export async function createIamRole(roleName: string, trustTemplate: string = 'lambda', trustPolicy?: any): Promise<any> {
  const res = await fetch('/api/iam/roles/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ role_name: roleName, trust_template: trustTemplate, trust_policy: trustPolicy }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to create IAM role`);
  return data;
}

export async function deleteIamRole(roleName: string): Promise<any> {
  const res = await fetch(`/api/iam/roles/${encodeURIComponent(roleName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function createIamGroup(groupName: string): Promise<any> {
  const res = await fetch('/api/iam/groups/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ group_name: groupName }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to create IAM group`);
  return data;
}

export async function simulateIamPolicy(principalArn: string, actionNames: string[], resourceArns: string[] = ['*']): Promise<any> {
  const res = await fetch('/api/iam/policy-simulation/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ principal_arn: principalArn, action_names: actionNames, resource_arns: resourceArns }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Policy simulation failed`);
  return data;
}

// DynamoDB Deepening APIs
export async function fetchDynamoDbTableScan(tableName: string, limit: number = 50, startKey?: any): Promise<{ items: any[]; count: number; scanned_count: number; last_evaluated_key?: any }> {
  const response = await fetch(`/api/dynamodb/tables/${encodeURIComponent(tableName)}/scan/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ limit, exclusive_start_key: startKey }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || 'Unable to scan table');
  return data;
}

export async function executeDynamoDbPartiQL(statement: string, limit: number = 50): Promise<{ items: any[]; count: number }> {
  const res = await fetch('/api/dynamodb/partiql/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ statement, limit }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `PartiQL query failed`);
  return data;
}

export async function putDynamoDbItem(tableName: string, item: Record<string, any>): Promise<any> {
  return await executeServiceAction('dynamodb', 'put_item', {
    table_name: tableName,
    item,
  });
}

export async function deleteDynamoDbItem(tableName: string, key: Record<string, any>): Promise<any> {
  return await executeServiceAction('dynamodb', 'delete_item', {
    table_name: tableName,
    key,
  });
}

// Lambda Deepening APIs
export async function fetchLambdaFunctionUrl(functionName: string): Promise<any> {
  try {
    const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/url/`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function createLambdaFunctionUrl(functionName: string, authType: 'NONE' | 'AWS_IAM' = 'NONE', cors?: any): Promise<any> {
  const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/url/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      options: {
        AuthType: authType,
        Cors: cors || { AllowOrigins: ['*'], AllowMethods: ['*'] },
      },
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to create Function URL`);
  return data;
}

export async function deleteLambdaFunctionUrl(functionName: string): Promise<any> {
  const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/url/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function fetchLambdaEventSourceMappings(functionName: string): Promise<any[]> {
  try {
    const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/event-source-mappings/`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.EventSourceMappings || data.mappings || [];
  } catch {
    return [];
  }
}

export async function createLambdaEventSourceMapping(functionName: string, eventSourceArn: string, batchSize: number = 10): Promise<any> {
  const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/event-source-mappings/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      options: {
        EventSourceArn: eventSourceArn,
        BatchSize: batchSize,
        StartingPosition: 'LATEST',
      },
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to add event source mapping trigger`);
  return data;
}

export async function deleteLambdaEventSourceMapping(uuid: string): Promise<any> {
  const res = await fetch(`/api/lambda/event-source-mappings/${encodeURIComponent(uuid)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function fetchLambdaVersions(functionName: string): Promise<any[]> {
  try {
    const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/versions/`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.Versions || data.versions || [];
  } catch {
    return [];
  }
}

export async function publishLambdaVersion(functionName: string, description: string = ''): Promise<any> {
  const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/versions/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ description }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || `Failed to publish version`);
  return data;
}

// Cognito Deepening APIs
export async function createCognitoUserPool(name: string): Promise<any> {
  const res = await fetch('/api/cognito/user-pools/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create user pool');
  return data;
}

export async function createCognitoUser(userPoolId: string, username: string, password: string = 'TempPass123!', email?: string): Promise<any> {
  const res = await fetch(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      username,
      temporary_password: password,
      attributes: email ? [{ Name: 'email', Value: email }] : [],
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create Cognito user');
  return data;
}

export async function createCognitoAppClient(userPoolId: string, clientName: string, enablePasswordAuth = true): Promise<any> {
  const res = await fetch(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/clients/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name: clientName, generate_secret: false, ...(enablePasswordAuth ? { explicit_auth_flows: ['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'] } : {}) }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create app client');
  return data;
}

export async function initiateCognitoAuth(clientId: string, username: string, password: string): Promise<any> {
  const res = await fetch('/api/cognito/auth/initiate/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      client_id: clientId,
      username,
      password,
      auth_flow: 'USER_PASSWORD_AUTH',
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Authentication failed');
  return data;
}

export async function deleteCognitoUser(userPoolId: string, username: string): Promise<any> {
  const res = await fetch(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/${encodeURIComponent(username)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

// API Gateway Deepening APIs
export async function createApiGatewayApi(name: string, protocolType: string = 'HTTP', description: string = ''): Promise<any> {
  const res = await fetch('/api/apigateway/apis/create/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, api_type: protocolType.toLowerCase(), description }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create API');
  return data;
}

export async function deleteApiGatewayApi(apiId: string, apiType: string = 'http'): Promise<any> {
  const res = await fetch('/api/apigateway/apis/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ api_id: apiId, api_type: apiType.toLowerCase() }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to delete API');
  return data;
}

export async function testApiGatewayRequest(apiId: string, path: string, method: string = 'GET', headers: any = {}, body: any = null, apiType = 'http', stage = '', endpoint = ''): Promise<any> {
  const res = await fetch('/api/apigateway/requests/test/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      api_id: apiId,
      path: path.startsWith('/') ? path : `/${path}`,
      method,
      api_type: apiType.toLowerCase(),
      stage,
      endpoint,
      headers,
      body,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Request failed');
  return data;
}

// SSM Parameter Store Deepening APIs
export async function putSsmParameter(name: string, value: string, type: 'String' | 'StringList' | 'SecureString' = 'String', description: string = ''): Promise<any> {
  const res = await fetch('/api/ssm/parameters/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      name,
      value,
      type,
      description,
      overwrite: true,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to put parameter');
  return data;
}

export async function getSsmParameterValue(name: string, withDecryption: boolean = true): Promise<any> {
  const cleanName = name.startsWith('/') ? name.substring(1) : name;
  const res = await fetch(`/api/ssm/parameters/${encodeURIComponent(cleanName)}/value/?with_decryption=${withDecryption}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function deleteSsmParameter(name: string): Promise<any> {
  return await executeServiceAction('ssm', 'delete_parameter', { name });
}

// EC2 Networking & Security Groups APIs
export async function createEc2Vpc(cidrBlock: string, name?: string): Promise<any> {
  const res = await fetch('/api/ec2/vpcs/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ cidr_block: cidrBlock, name }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create VPC');
  return data;
}

export async function deleteEc2Vpc(vpcId: string): Promise<any> {
  const res = await fetch(`/api/ec2/vpcs/${encodeURIComponent(vpcId)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function createEc2Subnet(vpcId: string, cidrBlock: string, az?: string, name?: string): Promise<any> {
  const res = await fetch('/api/ec2/subnets/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ vpc_id: vpcId, cidr_block: cidrBlock, availability_zone: az, name }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create subnet');
  return data;
}

export async function deleteEc2Subnet(subnetId: string): Promise<any> {
  const res = await fetch(`/api/ec2/subnets/${encodeURIComponent(subnetId)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function createEc2SecurityGroup(name: string, description: string, vpcId: string): Promise<any> {
  const res = await fetch('/api/ec2/security-groups/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, description, vpc_id: vpcId }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create security group');
  return data;
}

export async function deleteEc2SecurityGroup(groupId: string): Promise<any> {
  const res = await fetch(`/api/ec2/security-groups/${encodeURIComponent(groupId)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function changeEc2SecurityGroupRule(groupId: string, direction: 'ingress' | 'egress', rule: any, revoke: boolean = false): Promise<any> {
  const res = await fetch(`/api/ec2/security-groups/${encodeURIComponent(groupId)}/rules/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ direction, rule, revoke }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update security group rule');
  return data;
}

export async function runEc2InstanceCommand(instanceId: string, command: string): Promise<any> {
  const res = await fetch(`/api/ec2/instances/${encodeURIComponent(instanceId)}/commands/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ command }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to execute SSM command on instance');
  return data;
}

// Resource Graph APIs
export async function fetchResourceGraph(scenario: string = 'eventbridge-application-spine'): Promise<any> {
  try {
    const res = await fetch(`/api/resource-graph/?scenario=${encodeURIComponent(scenario)}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error('Failed to fetch resource graph:', err);
    return null;
  }
}

// SQS Operational APIs
export async function createSqsQueue(name: string, fifo: boolean = false, visibilityTimeout?: number): Promise<any> {
  const res = await fetch('/api/sqs/queues/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, fifo, visibility_timeout: visibilityTimeout }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create queue');
  return data;
}

export async function deleteSqsQueue(queueName: string): Promise<any> {
  const res = await fetch(`/api/sqs/queues/${encodeURIComponent(queueName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function purgeSqsQueue(queueName: string): Promise<any> {
  const res = await fetch(`/api/sqs/queues/${encodeURIComponent(queueName)}/purge/`, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to purge queue');
  return data;
}

export async function sendSqsMessage(
  queueName: string,
  body: string,
  delaySeconds?: number,
  messageGroupId?: string,
  messageDeduplicationId?: string
): Promise<any> {
  const res = await fetch(`/api/sqs/queues/${encodeURIComponent(queueName)}/messages/send/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      body,
      delay_seconds: delaySeconds,
      message_group_id: messageGroupId,
      message_deduplication_id: messageDeduplicationId,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to send message');
  return data;
}

export async function receiveSqsMessages(
  queueName: string,
  maxNumber: number = 5,
  visibilityTimeout?: number,
  waitTimeSeconds: number = 0
): Promise<{ queue: string; messages: any[] }> {
  const res = await fetch(`/api/sqs/queues/${encodeURIComponent(queueName)}/messages/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      max_number: maxNumber,
      visibility_timeout: visibilityTimeout,
      wait_time_seconds: waitTimeSeconds,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to receive messages');
  return data;
}

export async function deleteSqsMessage(queueName: string, receiptHandle: string): Promise<any> {
  const res = await fetch(`/api/sqs/queues/${encodeURIComponent(queueName)}/messages/delete/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ receipt_handle: receiptHandle }),
  });
  return await res.json();
}

// SNS Operational APIs
export async function publishSnsMessage(
  topicArn: string,
  message: string,
  subject?: string,
  messageAttributes?: any,
  messageStructure?: string,
  messageGroupId?: string,
  messageDeduplicationId?: string
): Promise<any> {
  const res = await fetch('/api/sns/messages/publish/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      topic_arn: topicArn,
      message,
      subject,
      message_attributes: messageAttributes,
      message_structure: messageStructure,
      message_group_id: messageGroupId,
      message_deduplication_id: messageDeduplicationId,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to publish message');
  return data;
}

export async function createSnsSubscription(topicArn: string, protocol: string, endpoint: string): Promise<any> {
  return await executeServiceAction('sns', 'subscribe', {
    TopicArn: topicArn,
    Protocol: protocol,
    Endpoint: endpoint,
  });
}

// RDS Operational APIs
export async function createRdsInstance(
  identifier: string,
  engine: string,
  username: string,
  password: string,
  dbInstanceClass: string = 'db.t3.micro',
  allocatedStorage: number = 20,
  dbName: string = '',
  engineVersion: string = '',
  enableIamAuth: boolean = false
): Promise<any> {
  const res = await fetch('/api/rds/instances/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      identifier,
      engine,
      username,
      password,
      db_instance_class: dbInstanceClass,
      allocated_storage: allocatedStorage,
      db_name: dbName,
      engine_version: engineVersion,
      enable_iam_auth: enableIamAuth,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create database instance');
  return data;
}

export async function modifyRdsInstance(
  identifier: string,
  dbInstanceClass?: string,
  allocatedStorage?: number,
  masterUserPassword?: string,
  applyImmediately: boolean = true
): Promise<any> {
  const res = await fetch(`/api/rds/instances/${encodeURIComponent(identifier)}/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      db_instance_class: dbInstanceClass,
      allocated_storage: allocatedStorage,
      master_user_password: masterUserPassword,
      apply_immediately: applyImmediately,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to modify database instance');
  return data;
}

export async function rebootRdsInstance(identifier: string): Promise<any> {
  const res = await fetch(`/api/rds/instances/${encodeURIComponent(identifier)}/reboot/`, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to reboot database instance');
  return data;
}

export async function deleteRdsInstance(identifier: string, skipFinalSnapshot: boolean = true, finalSnapshotId: string = ''): Promise<any> {
  const res = await fetch(`/api/rds/instances/${encodeURIComponent(identifier)}/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      skip_final_snapshot: skipFinalSnapshot,
      final_snapshot_identifier: finalSnapshotId,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to delete database instance');
  return data;
}

export async function createRdsCluster(
  identifier: string,
  engine: string,
  username: string,
  password: string,
  databaseName: string = '',
  engineVersion: string = '',
  enableIamAuth: boolean = false
): Promise<any> {
  const res = await fetch('/api/rds/clusters/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      identifier,
      engine,
      username,
      password,
      database_name: databaseName,
      engine_version: engineVersion,
      enable_iam_auth: enableIamAuth,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create DB cluster');
  return data;
}

export async function deleteRdsCluster(identifier: string, skipFinalSnapshot: boolean = true): Promise<any> {
  const res = await fetch(`/api/rds/clusters/${encodeURIComponent(identifier)}/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ skip_final_snapshot: skipFinalSnapshot }),
  });
  return await res.json();
}

export async function createRdsParameterGroup(name: string, family: string, description: string): Promise<any> {
  const res = await fetch('/api/rds/parameter-groups/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, family, description }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create parameter group');
  return data;
}

export async function deleteRdsParameterGroup(name: string): Promise<any> {
  const res = await fetch(`/api/rds/parameter-groups/${encodeURIComponent(name)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

// S3 Deepening APIs (Permissions & Properties)
export async function fetchS3BucketPolicy(bucketName: string): Promise<string> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/policy/`);
    if (!res.ok) return '';
    const data = await res.json();
    return data.policy || (typeof data === 'string' ? data : JSON.stringify(data, null, 2));
  } catch {
    return '';
  }
}

export async function putS3BucketPolicy(bucketName: string, policy: string): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/policy/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ policy }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update bucket policy');
  return data;
}

export async function deleteS3BucketPolicy(bucketName: string): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/policy/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function fetchS3BucketCors(bucketName: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/cors/`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function putS3BucketCors(bucketName: string, corsRules: any): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/cors/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ cors_configuration: corsRules }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update CORS');
  return data;
}

export async function deleteS3BucketCors(bucketName: string): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/cors/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function fetchS3BucketVersioning(bucketName: string): Promise<{ status: string }> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/versioning/`);
    if (!res.ok) return { status: 'Suspended' };
    const data = await res.json();
    return { status: data.Status || data.status || 'Suspended' };
  } catch {
    return { status: 'Suspended' };
  }
}

export async function putS3BucketVersioning(bucketName: string, status: 'Enabled' | 'Suspended'): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/versioning/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ status }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update versioning');
  return data;
}

export async function fetchS3BucketEncryption(bucketName: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/encryption/`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function putS3BucketEncryption(bucketName: string, config: any): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/encryption/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(config),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update encryption');
  return data;
}

export async function fetchS3BucketLifecycle(bucketName: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/lifecycle/`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function putS3BucketLifecycle(bucketName: string, rules: any): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/lifecycle/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ rules }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update lifecycle');
  return data;
}

export async function fetchS3BucketPublicAccessBlock(bucketName: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/public-access-block/`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function putS3BucketPublicAccessBlock(bucketName: string, config: any): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/public-access-block/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(config),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update public access block');
  return data;
}

export async function fetchS3ObjectTags(bucketName: string, key: string): Promise<any[]> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/objects/tags/?key=${encodeURIComponent(key)}`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.tags || data.TagSet || [];
  } catch {
    return [];
  }
}

export async function putS3ObjectTags(bucketName: string, key: string, tags: Array<{ Key: string; Value: string }>): Promise<any> {
  const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/objects/tags/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ key, tags }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update object tags');
  return data;
}

export async function fetchS3ObjectHead(bucketName: string, key: string): Promise<any> {
  try {
    const res = await fetch(`/api/s3/buckets/${encodeURIComponent(bucketName)}/objects/head/?key=${encodeURIComponent(key)}`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// CloudFormation Operational APIs
export async function validateCloudFormationTemplate(templateBody: string): Promise<any> {
  const res = await fetch('/api/cloudformation/templates/validate/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ template_body: templateBody }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Template validation failed');
  return data;
}

export async function createCloudFormationStack(
  stackName: string,
  templateBody: string,
  parameters?: Array<{ ParameterKey: string; ParameterValue: string }>,
  capabilities?: string[],
  disableRollback: boolean = false
): Promise<any> {
  const res = await fetch('/api/cloudformation/stacks/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      stack_name: stackName,
      template_body: templateBody,
      parameters,
      capabilities: capabilities || ['CAPABILITY_IAM', 'CAPABILITY_NAMED_IAM'],
      disable_rollback: disableRollback,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create stack');
  return data;
}

export async function updateCloudFormationStack(
  stackName: string,
  templateBody?: string,
  parameters?: Array<{ ParameterKey: string; ParameterValue: string }>
): Promise<any> {
  const res = await fetch(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      template_body: templateBody,
      parameters,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to update stack');
  return data;
}

export async function deleteCloudFormationStack(stackName: string): Promise<any> {
  const res = await fetch(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function createCloudFormationChangeSet(
  stackName: string,
  changeSetName: string,
  templateBody: string,
  changeSetType: string = 'UPDATE',
  parameters?: any
): Promise<any> {
  const res = await fetch('/api/cloudformation/change-sets/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      stack_name: stackName,
      change_set_name: changeSetName,
      template_body: templateBody,
      change_set_type: changeSetType,
      parameters,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create change set');
  return data;
}

export async function describeCloudFormationChangeSet(stackName: string, changeSetName: string): Promise<any> {
  const res = await fetch(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/change-sets/${encodeURIComponent(changeSetName)}/`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

export async function executeCloudFormationChangeSet(stackName: string, changeSetName: string): Promise<any> {
  const res = await fetch(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/change-sets/${encodeURIComponent(changeSetName)}/`, {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to execute change set');
  return data;
}

export async function deleteCloudFormationChangeSet(stackName: string, changeSetName: string): Promise<any> {
  const res = await fetch(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/change-sets/${encodeURIComponent(changeSetName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

// EventBridge Operational APIs
export async function createEventBus(name: string, description: string = ''): Promise<any> {
  const res = await fetch('/api/eventbridge/buses/create/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, description }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create event bus');
  return data;
}

export async function deleteEventBus(name: string): Promise<any> {
  const res = await fetch('/api/eventbridge/buses/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name }),
  });
  return await res.json();
}

export async function putEventRule(
  name: string,
  busName: string = 'default',
  pattern?: any,
  schedule?: string,
  description: string = '',
  state: string = 'ENABLED'
): Promise<any> {
  const res = await fetch('/api/eventbridge/rules/put/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      name,
      event_bus_name: busName,
      event_pattern: pattern ? (typeof pattern === 'string' ? pattern : JSON.stringify(pattern)) : undefined,
      schedule_expression: schedule,
      description,
      state,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to put event rule');
  return data;
}

export async function setEventRuleState(name: string, busName: string = 'default', enabled: boolean = true): Promise<any> {
  const res = await fetch('/api/eventbridge/rules/state/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, event_bus_name: busName, enabled }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to change rule state');
  return data;
}

export async function deleteEventRule(name: string, busName: string = 'default'): Promise<any> {
  const res = await fetch('/api/eventbridge/rules/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, event_bus_name: busName }),
  });
  return await res.json();
}

export async function putEventTarget(
  ruleName: string,
  busName: string = 'default',
  targetId: string,
  arn: string,
  input?: any,
  roleArn: string = ''
): Promise<any> {
  const res = await fetch('/api/eventbridge/targets/put/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      rule_name: ruleName,
      event_bus_name: busName,
      target_id: targetId,
      arn,
      input: input ? (typeof input === 'string' ? input : JSON.stringify(input)) : undefined,
      role_arn: roleArn,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to add target');
  return data;
}

export async function removeEventTarget(ruleName: string, busName: string = 'default', targetId: string): Promise<any> {
  const res = await fetch('/api/eventbridge/targets/remove/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      rule_name: ruleName,
      event_bus_name: busName,
      target_id: targetId,
    }),
  });
  return await res.json();
}

export async function putEvents(entries: any[]): Promise<any> {
  const res = await fetch('/api/eventbridge/events/put/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ entries }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to send events');
  return data;
}

// ----------------------------------------------------
// Phase 2: Core Service Consoles Expansion API Helpers
// ----------------------------------------------------

// IAM Additional APIs
export async function createIamPolicy(policyName: string, policyDocument: any, description: string = ''): Promise<any> {
  return await executeServiceAction('iam', 'create_policy', {
    policy_name: policyName,
    policy_document: typeof policyDocument === 'string' ? policyDocument : JSON.stringify(policyDocument),
    description,
  });
}

export async function deleteIamPolicy(policyArn: string): Promise<any> {
  return await executeServiceAction('iam', 'delete_policy', { policy_arn: policyArn });
}

export async function fetchIamUserAccessKeys(userName: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('iam', 'list_access_keys', { user_name: userName });
    return res.access_keys || res.AccessKeyMetadata || [];
  } catch {
    return [];
  }
}

export async function createIamUserAccessKey(userName: string): Promise<any> {
  return await executeServiceAction('iam', 'create_access_key', { user_name: userName });
}

export async function deleteIamUserAccessKey(userName: string, accessKeyId: string): Promise<any> {
  return await executeServiceAction('iam', 'delete_access_key', { user_name: userName, access_key_id: accessKeyId });
}

export async function updateIamAccessKeyState(userName: string, accessKeyId: string, status: 'Active' | 'Inactive'): Promise<any> {
  return await executeServiceAction('iam', 'update_access_key', { user_name: userName, access_key_id: accessKeyId, status });
}

export async function attachIamUserPolicy(userName: string, policyArn: string): Promise<any> {
  return await executeServiceAction('iam', 'attach_user_policy', { user_name: userName, policy_arn: policyArn });
}

export async function detachIamUserPolicy(userName: string, policyArn: string): Promise<any> {
  return await executeServiceAction('iam', 'detach_user_policy', { user_name: userName, policy_arn: policyArn });
}

export async function attachIamRolePolicy(roleName: string, policyArn: string): Promise<any> {
  return await executeServiceAction('iam', 'attach_role_policy', { role_name: roleName, policy_arn: policyArn });
}

export async function detachIamRolePolicy(roleName: string, policyArn: string): Promise<any> {
  return await executeServiceAction('iam', 'detach_role_policy', { role_name: roleName, policy_arn: policyArn });
}

export async function addUserToIamGroup(groupName: string, userName: string): Promise<any> {
  return await executeServiceAction('iam', 'add_user_to_group', { group_name: groupName, user_name: userName });
}

export async function removeUserFromIamGroup(groupName: string, userName: string): Promise<any> {
  return await executeServiceAction('iam', 'remove_user_from_group', { group_name: groupName, user_name: userName });
}

// DynamoDB Additional APIs
export async function updateDynamoDbTableCapacity(tableName: string, billingMode: 'PAY_PER_REQUEST' | 'PROVISIONED', readUnits: number = 5, writeUnits: number = 5): Promise<any> {
  return await executeServiceAction('dynamodb', 'update_table', {
    table_name: tableName,
    billing_mode: billingMode,
    read_capacity_units: readUnits,
    write_capacity_units: writeUnits,
  });
}

export async function updateDynamoDbPitr(tableName: string, enabled: boolean): Promise<any> {
  return await executeServiceAction('dynamodb', 'update_continuous_backups', {
    table_name: tableName,
    point_in_time_recovery_enabled: enabled,
  });
}

export async function updateDynamoDbTtl(tableName: string, attributeName: string, enabled: boolean): Promise<any> {
  return await executeServiceAction('dynamodb', 'update_time_to_live', {
    table_name: tableName,
    attribute_name: attributeName,
    enabled,
  });
}

// Lambda Additional APIs
export async function updateLambdaFunctionConfiguration(
  functionName: string,
  config: { timeout?: number; memorySize?: number; description?: string; handler?: string; runtime?: string }
): Promise<any> {
  return await executeServiceAction('lambda', 'update_function_configuration', {
    function_name: functionName,
    timeout: config.timeout,
    memory_size: config.memorySize,
    description: config.description,
    handler: config.handler,
    runtime: config.runtime,
  });
}

export async function updateLambdaEnvironmentVariables(functionName: string, variables: Record<string, string>): Promise<any> {
  return await executeServiceAction('lambda', 'update_function_configuration', {
    function_name: functionName,
    environment: { Variables: variables },
  });
}

export async function fetchLambdaAliases(functionName: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('lambda', 'list_aliases', { function_name: functionName });
    return res.aliases || res.Aliases || [];
  } catch {
    return [];
  }
}

export async function createLambdaAlias(functionName: string, name: string, functionVersion: string = '$LATEST', description: string = '', routingWeight?: number): Promise<any> {
  return await executeServiceAction('lambda', 'create_alias', {
    function_name: functionName,
    name,
    function_version: functionVersion,
    description,
    routing_config: routingWeight ? { AdditionalVersionWeights: { [functionVersion]: routingWeight } } : undefined,
  });
}

export async function deleteLambdaAlias(functionName: string, name: string): Promise<any> {
  return await executeServiceAction('lambda', 'delete_alias', { function_name: functionName, name });
}

// KMS Additional APIs
export async function fetchKmsKeyPolicy(keyId: string, policyName: string = 'default'): Promise<string> {
  try {
    const res = await executeServiceAction('kms', 'get_key_policy', { key_id: keyId, policy_name: policyName });
    return res.policy || res.Policy || '';
  } catch {
    return '';
  }
}

export async function putKmsKeyPolicy(keyId: string, policy: string, policyName: string = 'default'): Promise<any> {
  return await executeServiceAction('kms', 'put_key_policy', { key_id: keyId, policy_name: policyName, policy });
}

export async function fetchKmsKeyRotation(keyId: string): Promise<boolean> {
  try {
    const res = await executeServiceAction('kms', 'get_key_rotation_status', { key_id: keyId });
    return res.key_rotation_enabled ?? res.KeyRotationEnabled ?? false;
  } catch {
    return false;
  }
}

export async function updateKmsKeyRotation(keyId: string, enabled: boolean): Promise<any> {
  const action = enabled ? 'enable_key_rotation' : 'disable_key_rotation';
  return await executeServiceAction('kms', action, { key_id: keyId });
}

export async function updateKmsKeyState(keyId: string, state: 'enable' | 'disable' | 'schedule_deletion' | 'cancel_deletion', pendingDays: number = 7): Promise<any> {
  if (state === 'enable') return await executeServiceAction('kms', 'enable_key', { key_id: keyId });
  if (state === 'disable') return await executeServiceAction('kms', 'disable_key', { key_id: keyId });
  if (state === 'schedule_deletion') return await executeServiceAction('kms', 'schedule_key_deletion', { key_id: keyId, pending_window_in_days: pendingDays });
  return await executeServiceAction('kms', 'cancel_key_deletion', { key_id: keyId });
}

export async function fetchKmsGrants(keyId: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('kms', 'list_grants', { key_id: keyId });
    return res.grants || res.Grants || [];
  } catch {
    return [];
  }
}

export async function createKmsGrant(keyId: string, granteePrincipal: string, operations: string[]): Promise<any> {
  return await executeServiceAction('kms', 'create_grant', {
    key_id: keyId,
    grantee_principal: granteePrincipal,
    operations,
  });
}

export async function revokeKmsGrant(keyId: string, grantId: string): Promise<any> {
  return await executeServiceAction('kms', 'revoke_grant', { key_id: keyId, grant_id: grantId });
}

export async function generateKmsDataKey(keyId: string, keySpec: 'AES_256' | 'AES_128' = 'AES_256'): Promise<any> {
  return await executeServiceAction('kms', 'generate_data_key', { key_id: keyId, key_spec: keySpec });
}

// CloudWatch Additional APIs
export async function putCloudWatchMetricData(namespace: string, metricData: any[]): Promise<any> {
  return await executeServiceAction('cloudwatch', 'put_metric_data', { namespace, metric_data: metricData });
}

export async function setCloudWatchAlarmState(alarmName: string, stateValue: 'OK' | 'ALARM' | 'INSUFFICIENT_DATA', stateReason: string = 'Manual trigger from workbench'): Promise<any> {
  return await executeServiceAction('cloudwatch', 'set_alarm_state', {
    alarm_name: alarmName,
    state_value: stateValue,
    state_reason: stateReason,
  });
}

export async function deleteCloudWatchAlarms(alarmNames: string[]): Promise<any> {
  return await executeServiceAction('cloudwatch', 'delete_alarms', { alarm_names: alarmNames });
}

export async function createCloudWatchLogStream(logGroupName: string, logStreamName: string): Promise<any> {
  return await executeServiceAction('logs', 'create_log_stream', { log_group_name: logGroupName, log_stream_name: logStreamName });
}

export async function deleteCloudWatchLogStream(logGroupName: string, logStreamName: string): Promise<any> {
  return await executeServiceAction('logs', 'delete_log_stream', { log_group_name: logGroupName, log_stream_name: logStreamName });
}

export async function putCloudWatchLogRetention(logGroupName: string, retentionInDays: number): Promise<any> {
  if (retentionInDays === 0) {
    return await executeServiceAction('logs', 'delete_retention_policy', { log_group_name: logGroupName });
  }
  return await executeServiceAction('logs', 'put_retention_policy', { log_group_name: logGroupName, retention_in_days: retentionInDays });
}

// Step Functions Additional APIs
export async function fetchStepFunctionExecutionHistory(executionArn: string): Promise<any[]> {
  const response = await fetch(`/api/stepfunctions/executions/history/${encodeURIComponent(executionArn)}/`);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || 'Execution history unavailable');
  return data.events || [];
}

export async function stopStepFunctionExecution(executionArn: string, error: string = 'UserCancelled', cause: string = 'Execution stopped manually from workbench'): Promise<any> {
  return await executeServiceAction('stepfunctions', 'stop_execution', { execution_arn: executionArn, error, cause });
}

export async function updateStepFunctionDefinition(stateMachineArn: string, definition: string, roleArn?: string): Promise<any> {
  return await executeServiceAction('stepfunctions', 'update_state_machine', {
    state_machine_arn: stateMachineArn,
    definition,
    role_arn: roleArn,
  });
}

export async function fetchStepFunctionActivities(): Promise<any[]> {
  try {
    const res = await executeServiceAction('stepfunctions', 'list_activities', {});
    return res.activities || res.Activities || [];
  } catch {
    return [];
  }
}

export async function createStepFunctionActivity(name: string): Promise<any> {
  return await executeServiceAction('stepfunctions', 'create_activity', { name });
}

export async function deleteStepFunctionActivity(activityArn: string): Promise<any> {
  return await executeServiceAction('stepfunctions', 'delete_activity', { activity_arn: activityArn });
}

export async function sendStepFunctionTaskSuccess(taskToken: string, output: string): Promise<any> {
  return await executeServiceAction('stepfunctions', 'send_task_success', { task_token: taskToken, output });
}

export async function sendStepFunctionTaskFailure(taskToken: string, error: string = 'ManualFailure', cause: string = 'Task failed from workbench'): Promise<any> {
  return await executeServiceAction('stepfunctions', 'send_task_failure', { task_token: taskToken, error, cause });
}

// Cognito Additional APIs
export async function fetchCognitoUserGroups(userPoolId: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('cognito-idp', 'list_groups', { user_pool_id: userPoolId });
    return res.groups || res.Groups || [];
  } catch {
    return [];
  }
}

export async function createCognitoUserGroup(userPoolId: string, groupName: string, description: string = ''): Promise<any> {
  return postConsoleAction(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/groups/`, { group_name: groupName, description });
}

export async function addUserToCognitoGroup(userPoolId: string, groupName: string, username: string): Promise<any> {
  return await executeServiceAction('cognito-idp', 'admin_add_user_to_group', {
    user_pool_id: userPoolId,
    group_name: groupName,
    username,
  });
}

export async function removeUserFromCognitoGroup(userPoolId: string, groupName: string, username: string): Promise<any> {
  return await executeServiceAction('cognito-idp', 'admin_remove_user_from_group', {
    user_pool_id: userPoolId,
    group_name: groupName,
    username,
  });
}

export async function adminSetCognitoUserPassword(userPoolId: string, username: string, password: string, permanent: boolean = true): Promise<any> {
  return postConsoleAction(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/${encodeURIComponent(username)}/password/`, { password, permanent });
}

export async function adminConfirmCognitoSignUp(userPoolId: string, username: string): Promise<any> {
  return postConsoleAction(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/${encodeURIComponent(username)}/state/`, { action: 'confirm' });
}

export async function adminToggleCognitoUserState(userPoolId: string, username: string, enabled: boolean): Promise<any> {
  return postConsoleAction(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/${encodeURIComponent(username)}/state/`, { action: enabled ? 'enable' : 'disable' });
}

export async function fetchCognitoIdentityPools(): Promise<any[]> {
  const res = await executeServiceAction('cognito-identity', 'list_identity_pools', { max_results: 50 });
  return res.identity_pools || res.IdentityPools || [];
}

export async function createCognitoIdentityPool(identityPoolName: string, allowUnauthenticated: boolean = false): Promise<any> {
  return await executeServiceAction('cognito-identity', 'create_identity_pool', {
    identity_pool_name: identityPoolName,
    allow_unauthenticated_identities: allowUnauthenticated,
  });
}

// API Gateway Additional APIs
export async function fetchApiGatewayResources(restApiId: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('apigateway', 'get_resources', { rest_api_id: restApiId });
    return res.items || res.resources || [];
  } catch {
    return [];
  }
}

export async function createApiGatewayResource(restApiId: string, parentId: string, pathPart: string): Promise<any> {
  return await executeServiceAction('apigateway', 'create_resource', {
    rest_api_id: restApiId,
    parent_id: parentId,
    path_part: pathPart,
  });
}

export async function createApiGatewayMethod(restApiId: string, resourceId: string, httpMethod: string, authorizationType: string = 'NONE'): Promise<any> {
  return await executeServiceAction('apigateway', 'put_method', {
    rest_api_id: restApiId,
    resource_id: resourceId,
    http_method: httpMethod,
    authorization_type: authorizationType,
  });
}

export async function deleteApiGatewayResource(restApiId: string, resourceId: string): Promise<any> {
  return await executeServiceAction('apigateway', 'delete_resource', {
    rest_api_id: restApiId,
    resource_id: resourceId,
  });
}

export async function fetchApiGatewayStages(restApiId: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('apigateway', 'get_stages', { rest_api_id: restApiId });
    return res.item || res.stages || [];
  } catch {
    return [];
  }
}

export async function createApiGatewayStage(restApiId: string, stageName: string, deploymentId: string, description: string = ''): Promise<any> {
  return await executeServiceAction('apigateway', 'create_stage', {
    rest_api_id: restApiId,
    stage_name: stageName,
    deployment_id: deploymentId,
    description,
  });
}

export async function createApiGatewayDeployment(restApiId: string, stageName?: string, description: string = ''): Promise<any> {
  return await executeServiceAction('apigateway', 'create_deployment', {
    rest_api_id: restApiId,
    stage_name: stageName,
    description,
  });
}

export async function fetchApiGatewayAuthorizers(restApiId: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('apigateway', 'get_authorizers', { rest_api_id: restApiId });
    return res.items || res.authorizers || [];
  } catch {
    return [];
  }
}

export async function createApiGatewayAuthorizer(restApiId: string, name: string, type: 'TOKEN' | 'COGNITO_USER_POOLS' = 'TOKEN', providerArns?: string[], identitySource: string = 'method.request.header.Authorization'): Promise<any> {
  return await executeServiceAction('apigateway', 'create_authorizer', {
    rest_api_id: restApiId,
    name,
    type,
    provider_arns: providerArns,
    identity_source: identitySource,
  });
}

// SSM Additional APIs
export async function fetchSsmParameterHistory(name: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('ssm', 'get_parameter_history', { name });
    return res.parameters || res.Parameters || [];
  } catch {
    return [];
  }
}

export async function fetchSsmParameterTags(resourceId: string): Promise<any[]> {
  try {
    const res = await executeServiceAction('ssm', 'list_tags_for_resource', { resource_type: 'Parameter', resource_id: resourceId });
    return res.tag_list || res.TagList || [];
  } catch {
    return [];
  }
}

export async function addSsmParameterTags(resourceId: string, tags: Array<{ Key: string; Value: string }>): Promise<any> {
  return await executeServiceAction('ssm', 'add_tags_to_resource', { resource_type: 'Parameter', resource_id: resourceId, tags });
}

export async function removeSsmParameterTags(resourceId: string, tagKeys: string[]): Promise<any> {
  return await executeServiceAction('ssm', 'remove_tags_from_resource', { resource_type: 'Parameter', resource_id: resourceId, tag_keys: tagKeys });
}

export async function fetchSsmDocuments(): Promise<any[]> {
  try {
    const res = await executeServiceAction('ssm', 'list_documents', {});
    return res.document_identifiers || res.DocumentIdentifiers || [];
  } catch {
    return [];
  }
}

export async function createSsmDocument(name: string, content: string, documentType: 'Command' | 'Automation' = 'Command'): Promise<any> {
  return await executeServiceAction('ssm', 'create_document', { name, content, document_type: documentType });
}

export async function deleteSsmDocument(name: string): Promise<any> {
  return await executeServiceAction('ssm', 'delete_document', { name });
}

// ==========================================
// Phase 3: ECS APIs
// ==========================================
export async function createEcsCluster(name: string, capacityProviders?: string[], tags?: Array<{ Key: string; Value: string }>): Promise<any> {
  const res = await fetch('/api/ecs/clusters/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, capacity_providers: capacityProviders, tags }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteEcsCluster(cluster: string): Promise<any> {
  const res = await fetch('/api/ecs/clusters/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ cluster }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function registerEcsTaskDefinition(params: {
  family: string;
  container_definitions: any[];
  requires_compatibilities?: string[];
  network_mode?: string;
  cpu?: string;
  memory?: string;
  task_role_arn?: string;
  execution_role_arn?: string;
}): Promise<any> {
  const res = await fetch('/api/ecs/task-definitions/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deregisterEcsTaskDefinition(taskDefinition: string): Promise<any> {
  const res = await fetch('/api/ecs/task-definitions/detail/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ task_definition: taskDefinition }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function runEcsTask(params: {
  cluster: string;
  task_definition: string;
  launch_type?: string;
  count?: number;
  network_configuration?: any;
}): Promise<any> {
  const res = await fetch('/api/ecs/tasks/run/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function stopEcsTask(cluster: string, task: string, reason?: string): Promise<any> {
  const res = await fetch('/api/ecs/tasks/stop/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ cluster, task, reason: reason || 'Stopped from Floci Workbench' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createEcsService(params: {
  network_configuration?: any;
  cluster: string;
  service_name: string;
  task_definition: string;
  desired_count?: number;
  launch_type?: string;
}): Promise<any> {
  const res = await fetch('/api/ecs/services/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function updateEcsService(params: {
  cluster: string;
  service: string;
  desired_count?: number;
  task_definition?: string;
}): Promise<any> {
  const res = await fetch('/api/ecs/services/update/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteEcsService(cluster: string, service: string, force: boolean = true): Promise<any> {
  const res = await fetch('/api/ecs/services/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ cluster, service, force }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function updateEcsContainerInstanceState(cluster: string, containerInstances: string[], status: 'ACTIVE' | 'DRAINING'): Promise<any> {
  const res = await fetch('/api/ecs/container-instances/state/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ cluster, container_instances: containerInstances, status }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ==========================================
// Phase 3: ECR APIs
// ==========================================
export async function createEcrRepository(name: string, imageTagMutability: 'MUTABLE' | 'IMMUTABLE' = 'MUTABLE'): Promise<any> {
  const res = await fetch('/api/ecr/repositories/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, image_tag_mutability: imageTagMutability }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteEcrRepository(repositoryName: string, force: boolean = true): Promise<any> {
  const res = await fetch('/api/ecr/repositories/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ repository_name: repositoryName, force }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function fetchEcrAuthToken(): Promise<any> {
  const res = await fetch('/api/ecr/auth-token/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function batchDeleteEcrImages(repositoryName: string, imageIds: Array<{ imageDigest?: string; imageTag?: string }>): Promise<any> {
  const res = await fetch('/api/ecr/images/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ repository_name: repositoryName, image_ids: imageIds }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function putEcrTagMutability(repositoryName: string, imageTagMutability: 'MUTABLE' | 'IMMUTABLE'): Promise<any> {
  const res = await fetch('/api/ecr/tag-mutability/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ repository_name: repositoryName, image_tag_mutability: imageTagMutability }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function fetchEcrLifecyclePolicy(repositoryName: string): Promise<any> {
  const res = await fetch(`/api/ecr/lifecycle-policy/?repository_name=${encodeURIComponent(repositoryName)}`);
  if (!res.ok) return null;
  return await res.json();
}

export async function putEcrLifecyclePolicy(repositoryName: string, lifecyclePolicyText: string): Promise<any> {
  const res = await fetch('/api/ecr/lifecycle-policy/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ repository_name: repositoryName, lifecycle_policy_text: lifecyclePolicyText }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function runEcrGarbageCollection(): Promise<any> {
  const res = await fetch('/api/ecr/garbage-collection/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ==========================================
// Phase 3: CloudFront APIs
// ==========================================
export async function createCloudFrontDistribution(params: {
  caller_reference?: string;
  origin_domain_name: string;
  origin_id?: string;
  comment?: string;
  enabled?: boolean;
  viewer_protocol_policy?: string;
  aliases?: string[];
  cache_policy_id?: string;
}): Promise<any> {
  const res = await fetch('/api/cloudfront/distributions/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      caller_reference: params.caller_reference || crypto.randomUUID(),
      origin_domain_name: params.origin_domain_name,
      origin_id: params.origin_id || `origin-${Date.now()}`,
      comment: params.comment || '',
      enabled: params.enabled ?? true,
      viewer_protocol_policy: params.viewer_protocol_policy || 'redirect-to-https',
      aliases: params.aliases,
      cache_policy_id: params.cache_policy_id,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function updateCloudFrontDistribution(distributionId: string, params: { enabled?: boolean; comment?: string; config?: any; if_match?: string }): Promise<any> {
  const res = await fetch(`/api/cloudfront/distributions/${distributionId}/`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteCloudFrontDistribution(distributionId: string, ifMatch?: string): Promise<any> {
  const res = await fetch(`/api/cloudfront/distributions/${distributionId}/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ if_match: ifMatch || '' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createCloudFrontInvalidation(distributionId: string, paths: string[]): Promise<any> {
  const res = await fetch(`/api/cloudfront/distributions/${distributionId}/invalidations/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ paths, caller_reference: `inv-${Date.now()}` }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createCloudFrontCachePolicy(params: { name: string; default_ttl?: number; max_ttl?: number; min_ttl?: number; comment?: string }): Promise<any> {
  const res = await fetch('/api/cloudfront/cache-policies/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createCloudFrontOriginAccessIdentity(comment?: string): Promise<any> {
  const res = await fetch('/api/cloudfront/origin-access-identities/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ caller_reference: `oai-${Date.now()}`, comment: comment || 'OAI created from Floci Workbench' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createCloudFrontFunction(name: string, functionCode: string, comment?: string): Promise<any> {
  const res = await fetch('/api/cloudfront/functions/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      name,
      function_config: { comment: comment || '', runtime: 'cloudfront-js-2.0' },
      function_code: functionCode,
    }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteCloudFrontFunction(name: string, ifMatch?: string): Promise<any> {
  const res = await fetch(`/api/cloudfront/functions/${name}/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ if_match: ifMatch || '*' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ==========================================
// Phase 3: Elastic Load Balancing (ELBv2) APIs
// ==========================================
export async function createElbv2LoadBalancer(params: {
  name: string;
  type?: 'application' | 'network';
  scheme?: 'internet-facing' | 'internal';
  subnets?: string[];
  security_groups?: string[];
}): Promise<any> {
  const res = await fetch('/api/elasticloadbalancing/load-balancers/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteElbv2LoadBalancer(loadBalancerArn: string): Promise<any> {
  const res = await fetch(`/api/elasticloadbalancing/load-balancers/${encodeURIComponent(loadBalancerArn)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createElbv2TargetGroup(params: {
  name: string;
  protocol?: string;
  port?: number;
  target_type?: 'instance' | 'ip' | 'lambda';
  vpc_id?: string;
  health_check_path?: string;
}): Promise<any> {
  const res = await fetch('/api/elasticloadbalancing/target-groups/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteElbv2TargetGroup(targetGroupArn: string): Promise<any> {
  const res = await fetch(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(targetGroupArn)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function registerElbv2Targets(targetGroupArn: string, targets: Array<{ Id: string; Port?: number }>): Promise<any> {
  const res = await fetch(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(targetGroupArn)}/targets/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ targets }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deregisterElbv2Targets(targetGroupArn: string, targets: Array<{ Id: string; Port?: number }>): Promise<any> {
  const res = await fetch(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(targetGroupArn)}/targets/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ targets }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createElbv2Listener(params: {
  load_balancer_arn: string;
  protocol: string;
  port: number;
  target_group_arn: string;
}): Promise<any> {
  const res = await fetch('/api/elasticloadbalancing/listeners/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteElbv2Listener(listenerArn: string): Promise<any> {
  const res = await fetch(`/api/elasticloadbalancing/listeners/${encodeURIComponent(listenerArn)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ==========================================
// Phase 3: Athena APIs
// ==========================================
export async function startAthenaQuery(params: {
  query_string: string;
  database?: string;
  catalog?: string;
  workgroup?: string;
  output_location?: string;
}): Promise<{ query_execution_id: string }> {
  const res = await fetch('/api/athena/queries/start/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function stopAthenaQuery(queryExecutionId: string): Promise<any> {
  const res = await fetch('/api/athena/queries/stop/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ query_execution_id: queryExecutionId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function fetchAthenaQueryResults(queryExecutionId: string, maxResults: number = 50): Promise<any> {
  const res = await fetch('/api/athena/queries/results/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ query_execution_id: queryExecutionId, max_results: maxResults }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function fetchAthenaQueryDetail(queryExecutionId: string): Promise<any> {
  const res = await fetch('/api/athena/queries/detail/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ query_execution_id: queryExecutionId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createAthenaWorkgroup(name: string, description?: string, outputLocation?: string): Promise<any> {
  const res = await fetch('/api/athena/workgroups/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, description: description || '', output_location: outputLocation || '' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ==========================================
// Phase 3: AppSync APIs
// ==========================================
export async function createAppSyncGraphQLApi(name: string, authenticationType: 'API_KEY' | 'AWS_IAM' | 'OPENID_CONNECT' | 'AMAZON_COGNITO_USER_POOLS' = 'API_KEY'): Promise<any> {
  const res = await fetch('/api/appsync/apis/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, authentication_type: authenticationType }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteAppSyncGraphQLApi(apiId: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createAppSyncSchema(apiId: string, definition: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/schema/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ definition }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createAppSyncApiKey(apiId: string, description?: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/api-keys/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ description: description || 'Workbench generated API Key' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteAppSyncApiKey(apiId: string, keyId: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/api-keys/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ key_id: keyId }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createAppSyncDataSource(apiId: string, name: string, sourceType: string = 'NONE', description?: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/data-sources/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, source_type: sourceType, description: description || '' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteAppSyncDataSource(apiId: string, name: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/data-sources/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createAppSyncResolver(apiId: string, typeName: string, fieldName: string, dataSourceName?: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/resolvers/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ type_name: typeName, field_name: fieldName, data_source_name: dataSourceName || '' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteAppSyncResolver(apiId: string, typeName: string, fieldName: string): Promise<any> {
  const res = await fetch(`/api/appsync/apis/${apiId}/resolvers/`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ type_name: typeName, field_name: fieldName }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// ==========================================
// Phase 3: SES APIs
// ==========================================
export async function verifySesEmailIdentity(emailAddress: string): Promise<any> {
  const res = await fetch('/api/ses/identities/email/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ email_address: emailAddress }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function verifySesDomainIdentity(domain: string): Promise<any> {
  const res = await fetch('/api/ses/identities/domain/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ domain }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteSesIdentity(identity: string): Promise<any> {
  const res = await fetch(`/api/ses/identities/${encodeURIComponent(identity)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function sendSesEmail(params: {
  source: string;
  to_addresses: string[];
  subject: string;
  text?: string;
  html?: string;
  cc_addresses?: string[];
  bcc_addresses?: string[];
  configuration_set_name?: string;
}): Promise<any> {
  const res = await fetch('/api/ses/email/send/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function sendSesRawEmail(source: string, destinations: string[], rawMessage: string): Promise<any> {
  const res = await fetch('/api/ses/email/send-raw/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ source, destinations, raw_message: rawMessage }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function sendSesTemplatedEmail(source: string, toAddresses: string[], templateName: string, templateData: string): Promise<any> {
  const res = await fetch('/api/ses/email/send-templated/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ source, to_addresses: toAddresses, template_name: templateName, template_data: templateData }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createSesTemplate(name: string, subject: string, htmlPart?: string, textPart?: string): Promise<any> {
  const res = await fetch('/api/ses/templates/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, subject, html_part: htmlPart || '', text_part: textPart || '' }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function renderSesTemplate(templateName: string, templateData: string): Promise<any> {
  const res = await fetch(`/api/ses/templates/${encodeURIComponent(templateName)}/render/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ template_data: templateData }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function deleteSesTemplate(templateName: string): Promise<any> {
  const res = await fetch(`/api/ses/templates/${encodeURIComponent(templateName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function createSesConfigurationSet(name: string): Promise<any> {
  const res = await fetch('/api/ses/configuration-sets/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function updateSesSendingEnabled(enabled: boolean): Promise<any> {
  const res = await fetch('/api/ses/account/sending/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

export async function clearSesMailbox(): Promise<any> {
  const res = await fetch('/api/ses/mailbox/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return await res.json();
}

// EKS APIs
export async function createEksCluster(
  name: string,
  roleArn: string,
  version?: string,
  subnetIds?: string[],
  securityGroupIds?: string[],
  tags?: Record<string, string>
): Promise<any> {
  const res = await fetch('/api/eks/clusters/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      name,
      role_arn: roleArn,
      version,
      subnet_ids: subnetIds,
      security_group_ids: securityGroupIds,
      tags,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create EKS cluster');
  return data;
}

export async function deleteEksCluster(name: string): Promise<any> {
  const res = await fetch('/api/eks/clusters/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to delete EKS cluster');
  return data;
}

export async function createEksNodegroup(
  clusterName: string,
  nodegroupName: string,
  nodeRole: string,
  subnets: string[],
  scalingConfig?: { minSize?: number; maxSize?: number; desiredSize?: number },
  instanceTypes?: string[],
  amiType?: string,
  capacityType?: string,
  diskSize?: number,
  labels?: Record<string, string>,
  tags?: Record<string, string>
): Promise<any> {
  const res = await fetch(`/api/eks/clusters/${encodeURIComponent(clusterName)}/nodegroups/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      nodegroup_name: nodegroupName,
      node_role: nodeRole,
      subnets,
      scaling_config: scalingConfig,
      instance_types: instanceTypes,
      ami_type: amiType,
      capacity_type: capacityType,
      disk_size: diskSize,
      labels,
      tags,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create EKS node group');
  return data;
}

export async function deleteEksNodegroup(clusterName: string, nodegroupName: string): Promise<any> {
  const res = await fetch(`/api/eks/clusters/${encodeURIComponent(clusterName)}/nodegroups/${encodeURIComponent(nodegroupName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to delete EKS node group');
  return data;
}

export async function createEksFargateProfile(
  clusterName: string,
  profileName: string,
  podExecutionRoleArn: string,
  subnets?: string[],
  selectors?: Array<{ namespace: string; labels?: Record<string, string> }>,
  tags?: Record<string, string>
): Promise<any> {
  const res = await fetch(`/api/eks/clusters/${encodeURIComponent(clusterName)}/fargate-profiles/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({
      profile_name: profileName,
      pod_execution_role_arn: podExecutionRoleArn,
      subnets,
      selectors,
      tags,
    }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to create EKS Fargate profile');
  return data;
}

export async function deleteEksFargateProfile(clusterName: string, profileName: string): Promise<any> {
  const res = await fetch(`/api/eks/clusters/${encodeURIComponent(clusterName)}/fargate-profiles/${encodeURIComponent(profileName)}/`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to delete EKS Fargate profile');
  return data;
}

export async function fetchEksKubeconfig(clusterName: string): Promise<any> {
  const res = await fetch(`/api/eks/clusters/${encodeURIComponent(clusterName)}/kubeconfig/`, {
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to fetch EKS kubeconfig');
  return data;
}

export async function fetchEksTags(resourceArn: string): Promise<any> {
  const res = await fetch(`/api/eks/tags/list/?resource_arn=${encodeURIComponent(resourceArn)}`, {
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  return await res.json();
}

export async function tagEksResource(resourceArn: string, tags: Record<string, string>): Promise<any> {
  const res = await fetch('/api/eks/tags/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ resource_arn: resourceArn, tags }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to tag EKS resource');
  return data;
}

export async function untagEksResource(resourceArn: string, tagKeys: string[]): Promise<any> {
  const res = await fetch('/api/eks/tags/', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ resource_arn: resourceArn, tag_keys: tagKeys }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to untag EKS resource');
  return data;
}



export async function runAppSyncGraphQL(apiId: string, query: string, variables: Record<string, unknown>, apiKey?: string): Promise<any> {
  const response = await fetch('/api/appsync/graphql/run/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ api_id: apiId, query, variables, api_key: apiKey }),
  });
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.error || 'GraphQL execution failed');
  return result;
}

/** Strict inventory reads for resource consoles that display retrieval state. */
export async function fetchConsoleInventory(service: 'route53' | 'cognito'): Promise<any> {
  const response = await fetch(`/api/${service}/`);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Unable to load ${service} inventory (HTTP ${response.status})`);
  return data;
}

export async function postConsoleAction(path: string, payload: Record<string, unknown>): Promise<any> {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Action failed (HTTP ${response.status})`);
  return data;
}
