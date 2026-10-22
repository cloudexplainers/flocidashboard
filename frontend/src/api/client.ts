import { CliResponse } from '../pages/cliResources';
import { ExamTracksResponse, LabDefinition, ServiceAction, ServiceDefinition } from '../types';

function getCsrfToken(): string {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta) {
    return meta.getAttribute('content') || '';
  }
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : '';
}

/**
 * Sends a JSON request and returns the parsed body.
 * Throws on HTTP errors, and on an `error` field in a successful body unless ignoreErrorField is set.
 */
async function requestJson(
  url: string,
  {
    method = 'GET',
    json,
    errorMessage,
    ignoreErrorField = false,
  }: { method?: string; json?: unknown; errorMessage?: string; ignoreErrorField?: boolean } = {},
): Promise<any> {
  const headers: Record<string, string> = {};
  if (json !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRFToken'] = getCsrfToken();
  const res = await fetch(url, {
    method,
    headers,
    body: json === undefined ? undefined : JSON.stringify(json),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || errorMessage || `HTTP ${res.status}`);
  }
  const data = await res.json();
  if (!ignoreErrorField && data.error) throw new Error(data.error);
  return data;
}

export async function fetchCloudWatchLogs(path: 'log-streams' | 'log-events', payload: Record<string, unknown>): Promise<any> {
  return requestJson(`/api/cloudwatch/${path}/`, { method: 'POST', json: payload, errorMessage: 'Unable to load CloudWatch logs' });
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

export async function fetchLabExams(): Promise<ExamTracksResponse> {
  const res = await fetch('/api/labs/exams/');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
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
  return requestJson(`/api/labs/${serviceKey}/${labKey}/reset/`, { method: 'POST' });
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
  return requestJson('/api/settings/test-connection/', {
    method: 'POST',
    json: endpointUrl ? { endpoint_url: endpointUrl } : {},
    errorMessage: 'Connection test failed',
  });
}

export async function resetFlociState(): Promise<any> {
  return requestJson('/api/settings/floci-reset/', { method: 'POST', errorMessage: 'Floci reset failed' });
}

export async function fetchIdentityDetail(): Promise<any> {
  const response = await fetch('/api/session-identity/');
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

export async function useAdminIdentity(): Promise<any> {
  const res = await fetch('/api/session-identity/use-admin/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const result = await res.json();
  window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: result }));
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
  window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: data }));
  return data;
}

export async function assumeRoleIdentity(roleName: string, sessionName?: string, accountId?: string, roleArn?: string): Promise<any> {
  const res = await fetch('/api/session-identity/assume-role/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ role_name: roleName, session_name: sessionName, account_id: accountId, ...(roleArn ? { role_arn: roleArn } : {}) }),
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || 'Failed to assume role');
  window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: data }));
  return data;
}

export async function clearSessionIdentity(): Promise<any> {
  const res = await fetch('/api/session-identity/clear/', {
    method: 'POST',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const result = await res.json();
  window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: result }));
  return result;
}

export async function runCliCommand(command: string, confirmed: boolean = false): Promise<CliResponse> {
  const res = await fetch('/api/console/run/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ command, confirmed }),
  });
  const data = await res.json();
  if (!(res.status === 409 && data.requires_confirmation === true) && (!res.ok || data.error)) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

/**
 * Runs a ServiceAction from /api/services/ against its own method and path.
 * Pages call typed helpers instead; posting an action name to /api/<service>/ is not
 * supported because those inventory endpoints ignore request bodies.
 */
export async function executeServiceAction(
  actionOrServiceKey: ServiceAction,
  actionKeyOrFormValues: Record<string, any> = {},
  payloadOrFileValues: Record<string, File> = {}
): Promise<any> {
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

// S3 console APIs. Each helper mirrors one S3 API call through /api/s3/buckets/.
const s3Bucket = (name: string) => `/api/s3/buckets/${encodeURIComponent(name)}`;

export interface S3ObjectListing {
  prefix: string;
  folders: Array<{ prefix: string; name: string }>;
  objects: Array<{ key: string; name: string; size: number | null; etag?: string; last_modified?: string; storage_class?: string }>;
  is_truncated: boolean;
  key_count: number;
}

/** ListObjectsV2 with Delimiter "/" so prefixes show as folders, like the S3 console. */
export async function fetchS3Objects(bucketName: string, prefix: string = ''): Promise<S3ObjectListing> {
  const params = new URLSearchParams({ prefix, delimiter: '/', max_keys: '1000' });
  return requestJson(`${s3Bucket(bucketName)}/objects/?${params}`, { errorMessage: 'Failed to list objects' });
}

export async function createS3Bucket(name: string, region?: string): Promise<any> {
  return requestJson('/api/s3/buckets/', { method: 'POST', json: { name, region }, errorMessage: 'Failed to create bucket' });
}

export async function deleteS3Bucket(name: string): Promise<any> {
  return requestJson(`${s3Bucket(name)}/`, { method: 'DELETE', errorMessage: 'Failed to delete bucket' });
}

export async function emptyS3Bucket(name: string): Promise<{ deleted_count: number }> {
  return requestJson(`${s3Bucket(name)}/empty/`, { method: 'POST', errorMessage: 'Failed to empty bucket' });
}

export async function uploadS3Object(bucketName: string, key: string, content: { file?: File; text?: string; contentType?: string }): Promise<any> {
  if (content.file) {
    const form = new FormData();
    form.append('key', key);
    form.append('file', content.file);
    const res = await fetch(`${s3Bucket(bucketName)}/objects/`, { method: 'POST', headers: { 'X-CSRFToken': getCsrfToken() }, body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) throw new Error(data.error || `Upload failed with HTTP ${res.status}`);
    return data;
  }
  return requestJson(`${s3Bucket(bucketName)}/objects/`, {
    method: 'POST',
    json: { key, body: content.text || '', content_type: content.contentType || undefined },
    errorMessage: 'Failed to upload object',
  });
}

export function s3DownloadUrl(bucketName: string, key: string): string {
  return `${s3Bucket(bucketName)}/objects/download/?${new URLSearchParams({ key })}`;
}

export async function presignS3Object(bucketName: string, key: string, expiresIn: number = 3600): Promise<{ url: string; expires_in: number }> {
  return requestJson(`${s3Bucket(bucketName)}/objects/presign/`, {
    method: 'POST',
    json: { key, expires_in: expiresIn },
    errorMessage: `Failed to generate presigned URL`,
  });
}

/** GetBucketWebsite plus the website endpoint Floci serves it on (null when hosting is disabled). */
export async function fetchS3Website(bucketName: string): Promise<{ configuration: any | null; endpoint: string | null }> {
  const data = await requestJson(`${s3Bucket(bucketName)}/website/`, { errorMessage: 'Failed to read website configuration' });
  return { configuration: data.configuration || null, endpoint: data.endpoint || null };
}

export async function putS3Website(bucketName: string, config: { IndexDocument: { Suffix: string }; ErrorDocument?: { Key: string } }): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/website/`, {
    method: 'PUT',
    json: { configuration: config },
    errorMessage: `Failed to update website configuration`,
  });
}

export async function deleteS3Website(bucketName: string): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/website/`, { method: 'DELETE', errorMessage: 'Failed to disable website hosting' });
}

export async function fetchS3Notifications(bucketName: string): Promise<any> {
  const data = await requestJson(`${s3Bucket(bucketName)}/notifications/`, { errorMessage: 'Failed to read event notifications' });
  const { ResponseMetadata: _ignored, ...config } = data || {};
  return config;
}

export async function putS3Notifications(bucketName: string, config: any): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/notifications/`, {
    method: 'PUT',
    json: config,
    errorMessage: `Failed to update notifications`,
  });
}

export async function deleteS3Object(bucketName: string, key: string): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/objects/`, { method: 'DELETE', json: { keys: [{ key }] }, errorMessage: 'Failed to delete object' });
}

export async function createS3Folder(bucketName: string, folder: string): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/folders/`, {
    method: 'PUT',
    json: { folder },
    errorMessage: `Failed to create folder`,
  });
}

// IAM Deepening APIs
export async function createIamRole(roleName: string, trustTemplate: string = 'lambda', trustPolicy?: any): Promise<any> {
  return requestJson('/api/iam/roles/', {
    method: 'POST',
    json: { role_name: roleName, trust_template: trustTemplate, trust_policy: trustPolicy },
    errorMessage: `Failed to create IAM role`,
  });
}

/** DeleteRole. With force, detaches policies and removes the role from instance profiles first, as the console does. */
export async function deleteIamRole(roleName: string, force: boolean = true): Promise<any> {
  return requestJson(`/api/iam/roles/${encodeURIComponent(roleName)}/`, { method: 'DELETE', json: { force }, errorMessage: 'Failed to delete role' });
}

export async function createIamGroup(groupName: string): Promise<any> {
  return requestJson('/api/iam/groups/', { method: 'POST', json: { group_name: groupName }, errorMessage: `Failed to create IAM group` });
}

export async function simulateIamPolicy(principalArn: string, actionNames: string[], resourceArns: string[] = ['*']): Promise<any> {
  return requestJson('/api/iam/policy-simulation/', {
    method: 'POST',
    json: { principal_arn: principalArn, action_names: actionNames, resource_arns: resourceArns },
    errorMessage: `Policy simulation failed`,
  });
}

// DynamoDB Deepening APIs
export async function fetchDynamoDbTableScan(tableName: string, limit: number = 50, startKey?: any): Promise<{ items: any[]; count: number; scanned_count: number; last_evaluated_key?: any }> {
  return requestJson(`/api/dynamodb/tables/${encodeURIComponent(tableName)}/scan/`, {
    method: 'POST',
    json: { limit, exclusive_start_key: startKey },
    errorMessage: 'Unable to scan table',
  });
}

export async function executeDynamoDbPartiQL(statement: string, limit: number = 50): Promise<{ items: any[]; count: number }> {
  return requestJson('/api/dynamodb/partiql/', { method: 'POST', json: { statement, limit }, errorMessage: `PartiQL query failed` });
}

const dynamoTable = (name: string) => `/api/dynamodb/tables/${encodeURIComponent(name)}`;

/** PutItem with a plain JSON document; the backend converts it to DynamoDB attribute values. */
export async function putDynamoDbItem(tableName: string, item: Record<string, any>): Promise<any> {
  return requestJson(`${dynamoTable(tableName)}/items/`, { method: 'POST', json: { item }, errorMessage: 'Failed to save item' });
}

export async function deleteDynamoDbItem(tableName: string, key: Record<string, any>): Promise<any> {
  return requestJson(`${dynamoTable(tableName)}/items/delete/`, { method: 'POST', json: { key }, errorMessage: 'Failed to delete item' });
}

export async function createDynamoDbTable(request: {
  table_name: string;
  partition_key: { name: string; type: string };
  sort_key?: { name: string; type: string };
  billing_mode: 'PAY_PER_REQUEST' | 'PROVISIONED';
  read_capacity_units?: number;
  write_capacity_units?: number;
  deletion_protection?: boolean;
}): Promise<any> {
  return requestJson('/api/dynamodb/tables/', { method: 'POST', json: request, errorMessage: 'Failed to create table' });
}

export async function deleteDynamoDbTable(tableName: string): Promise<any> {
  return requestJson(`${dynamoTable(tableName)}/`, { method: 'DELETE', errorMessage: 'Failed to delete table' });
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

/** CreateFunctionUrlConfig. CORS is only sent when configured, as in the console (off by default). */
export async function createLambdaFunctionUrl(functionName: string, authType: 'NONE' | 'AWS_IAM' = 'NONE', cors?: any): Promise<any> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/url/`, {
    method: 'POST',
    json: { options: { AuthType: authType, ...(cors ? { Cors: cors } : {}) } },
    errorMessage: `Failed to create Function URL`,
  });
}

export async function deleteLambdaFunctionUrl(functionName: string): Promise<any> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/url/`, { method: 'DELETE', errorMessage: 'Failed to delete Function URL' });
}

export async function createLambdaFunction(request: { name: string; role: string; runtime: string; handler: string; fileName: string; source: string; architecture?: string }): Promise<any> {
  return requestJson('/api/lambda/functions/', {
    method: 'POST',
    json: {
      name: request.name,
      role: request.role,
      code: { InlineSource: { FileName: request.fileName, Content: request.source } },
      configuration: { Runtime: request.runtime, Handler: request.handler, Architectures: [request.architecture || 'x86_64'] },
    },
    errorMessage: 'Failed to create function',
  });
}

export async function deleteLambdaFunction(functionName: string): Promise<any> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/`, { method: 'DELETE', errorMessage: 'Failed to delete function' });
}

export interface LambdaInvokeResult {
  status_code?: number;
  executed_version?: string;
  function_error?: string;
  log_tail?: string;
  payload?: unknown;
}

/** Invoke with LogType Tail, so the response includes the last 4 KB of logs, like the console's Test tab. */
export async function invokeLambdaFunction(functionName: string, payload: unknown, qualifier?: string): Promise<LambdaInvokeResult> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/invoke/`, {
    method: 'POST',
    json: { payload, qualifier: qualifier || undefined },
    errorMessage: 'Failed to invoke function',
  });
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

/** CreateEventSourceMapping. StartingPosition applies only to stream sources (Kinesis, DynamoDB Streams); SQS rejects it. */
export async function createLambdaEventSourceMapping(functionName: string, eventSourceArn: string, batchSize: number = 10): Promise<any> {
  const isStream = /^arn:aws:(kinesis|dynamodb):/.test(eventSourceArn);
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/event-source-mappings/`, {
    method: 'POST',
    json: {
      options: {
        EventSourceArn: eventSourceArn,
        BatchSize: batchSize,
        ...(isStream ? { StartingPosition: 'LATEST' } : {}),
      },
    },
    errorMessage: `Failed to add event source mapping trigger`,
  });
}

export async function deleteLambdaEventSourceMapping(uuid: string): Promise<any> {
  return requestJson(`/api/lambda/event-source-mappings/${encodeURIComponent(uuid)}/`, { method: 'DELETE', errorMessage: 'Failed to delete trigger' });
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
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/versions/`, {
    method: 'POST',
    json: { description },
    errorMessage: `Failed to publish version`,
  });
}

// Cognito Deepening APIs
export async function createCognitoUserPool(name: string): Promise<any> {
  return requestJson('/api/cognito/user-pools/', { method: 'POST', json: { name }, errorMessage: 'Failed to create user pool' });
}

export async function createCognitoUser(userPoolId: string, username: string, password: string = 'TempPass123!', email?: string): Promise<any> {
  return requestJson(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/`, {
    method: 'POST',
    json: {
      username,
      temporary_password: password,
      attributes: email ? [{ Name: 'email', Value: email }] : [],
    },
    errorMessage: 'Failed to create Cognito user',
  });
}

export async function createCognitoAppClient(userPoolId: string, clientName: string, enablePasswordAuth = true): Promise<any> {
  return requestJson(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/clients/`, {
    method: 'POST',
    json: { name: clientName, generate_secret: false, ...(enablePasswordAuth ? { explicit_auth_flows: ['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'] } : {}) },
    errorMessage: 'Failed to create app client',
  });
}

export async function initiateCognitoAuth(clientId: string, username: string, password: string): Promise<any> {
  return requestJson('/api/cognito/auth/initiate/', {
    method: 'POST',
    json: {
      client_id: clientId,
      username,
      password,
      auth_flow: 'USER_PASSWORD_AUTH',
    },
    errorMessage: 'Authentication failed',
  });
}

export async function deleteCognitoUser(userPoolId: string, username: string): Promise<any> {
  return requestJson(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/users/${encodeURIComponent(username)}/`, { method: 'DELETE', errorMessage: 'Failed to delete user' });
}

// API Gateway Deepening APIs
export async function createApiGatewayApi(name: string, protocolType: string = 'HTTP', description: string = ''): Promise<any> {
  return requestJson('/api/apigateway/apis/create/', {
    method: 'POST',
    json: { name, api_type: protocolType.toLowerCase(), description },
    errorMessage: 'Failed to create API',
  });
}

export async function deleteApiGatewayApi(apiId: string, apiType: string = 'http'): Promise<any> {
  return requestJson('/api/apigateway/apis/delete/', {
    method: 'POST',
    json: { api_id: apiId, api_type: apiType.toLowerCase() },
    errorMessage: 'Failed to delete API',
  });
}

export async function testApiGatewayRequest(apiId: string, path: string, method: string = 'GET', headers: any = {}, body: any = null, apiType = 'http', stage = '', endpoint = ''): Promise<any> {
  return requestJson('/api/apigateway/requests/test/', {
    method: 'POST',
    json: {
      api_id: apiId,
      path: path.startsWith('/') ? path : `/${path}`,
      method,
      api_type: apiType.toLowerCase(),
      stage,
      endpoint,
      headers,
      body,
    },
    errorMessage: 'Request failed',
  });
}

// SSM Parameter Store Deepening APIs
// EC2 instance, networking and security group APIs
export async function launchEc2Instance(request: {
  image_id: string;
  instance_type: string;
  subnet_id?: string;
  security_group_ids?: string[];
  key_name?: string;
  tags?: Record<string, string>;
}): Promise<{ instance_id: string | null; reservation_id?: string }> {
  return requestJson('/api/ec2/instances/', { method: 'POST', json: request, errorMessage: 'Failed to launch instance' });
}

/** StartInstances, StopInstances, RebootInstances or TerminateInstances for one instance. */
export async function changeEc2InstanceState(instanceId: string, action: 'start' | 'stop' | 'reboot' | 'terminate'): Promise<any> {
  return requestJson(`/api/ec2/instances/${encodeURIComponent(instanceId)}/${action}/`, { method: 'POST', errorMessage: `Failed to ${action} instance` });
}

export async function createEc2Vpc(cidrBlock: string, name?: string): Promise<any> {
  return requestJson('/api/ec2/vpcs/', { method: 'POST', json: { cidr_block: cidrBlock, name }, errorMessage: 'Failed to create VPC' });
}

export async function deleteEc2Vpc(vpcId: string): Promise<any> {
  return requestJson(`/api/ec2/vpcs/${encodeURIComponent(vpcId)}/`, { method: 'DELETE', errorMessage: 'Failed to delete VPC' });
}

export async function createEc2Subnet(vpcId: string, cidrBlock: string, az?: string, name?: string): Promise<any> {
  return requestJson('/api/ec2/subnets/', {
    method: 'POST',
    json: { vpc_id: vpcId, cidr_block: cidrBlock, availability_zone: az, name },
    errorMessage: 'Failed to create subnet',
  });
}

export async function deleteEc2Subnet(subnetId: string): Promise<any> {
  return requestJson(`/api/ec2/subnets/${encodeURIComponent(subnetId)}/`, { method: 'DELETE', errorMessage: 'Failed to delete subnet' });
}

export async function createEc2SecurityGroup(name: string, description: string, vpcId: string): Promise<any> {
  return requestJson('/api/ec2/security-groups/', {
    method: 'POST',
    json: { name, description, vpc_id: vpcId },
    errorMessage: 'Failed to create security group',
  });
}

export async function deleteEc2SecurityGroup(groupId: string): Promise<any> {
  return requestJson(`/api/ec2/security-groups/${encodeURIComponent(groupId)}/`, { method: 'DELETE', errorMessage: 'Failed to delete security group' });
}

export interface Ec2RuleRequest {
  protocol: string;
  from_port?: number;
  to_port?: number;
  cidr: string;
  description?: string;
}

/** AuthorizeSecurityGroupIngress/Egress, or the matching Revoke call when revoke is true. */
export async function changeEc2SecurityGroupRule(groupId: string, direction: 'ingress' | 'egress', rule: Ec2RuleRequest, revoke: boolean = false): Promise<any> {
  return requestJson(`/api/ec2/security-groups/${encodeURIComponent(groupId)}/rules/`, {
    method: revoke ? 'DELETE' : 'POST',
    json: { direction, rule },
    errorMessage: `Failed to ${revoke ? 'remove' : 'add'} security group rule`,
  });
}

/** SSM SendCommand with the AWS-RunShellScript document. */
export async function runEc2InstanceCommand(instanceId: string, command: string): Promise<{ command_id: string; status: string }> {
  return requestJson(`/api/ec2/instances/${encodeURIComponent(instanceId)}/commands/`, {
    method: 'POST',
    json: { command },
    errorMessage: 'Failed to execute SSM command on instance',
  });
}

/** SSM GetCommandInvocation for one instance. */
export async function fetchEc2CommandInvocation(instanceId: string, commandId: string): Promise<{ status: string; status_details?: string; response_code?: number; stdout: string; stderr: string }> {
  return requestJson(`/api/ec2/instances/${encodeURIComponent(instanceId)}/commands/${encodeURIComponent(commandId)}/`, { errorMessage: 'Failed to read command output' });
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
/** CreateQueue. Omitted settings use the SQS defaults (30 s visibility, 4 days retention, no delay, short polling). */
export async function createSqsQueue(name: string, fifo: boolean = false, settings: {
  visibilityTimeout?: number;
  messageRetentionPeriod?: number;
  delaySeconds?: number;
  receiveWaitTime?: number;
  contentBasedDeduplication?: boolean;
} = {}): Promise<any> {
  return requestJson('/api/sqs/queues/', {
    method: 'POST',
    json: {
      name,
      fifo,
      visibility_timeout: settings.visibilityTimeout,
      message_retention_period: settings.messageRetentionPeriod,
      delay_seconds: settings.delaySeconds,
      receive_wait_time: settings.receiveWaitTime,
      content_based_deduplication: settings.contentBasedDeduplication,
    },
    errorMessage: 'Failed to create queue',
  });
}

export async function deleteSqsQueue(queueName: string): Promise<any> {
  return requestJson(`/api/sqs/queues/${encodeURIComponent(queueName)}/`, { method: 'DELETE', errorMessage: 'Failed to delete queue' });
}

export async function purgeSqsQueue(queueName: string): Promise<any> {
  return requestJson(`/api/sqs/queues/${encodeURIComponent(queueName)}/purge/`, { method: 'POST', errorMessage: 'Failed to purge queue' });
}

export async function sendSqsMessage(
  queueName: string,
  body: string,
  delaySeconds?: number,
  messageGroupId?: string,
  messageDeduplicationId?: string
): Promise<any> {
  return requestJson(`/api/sqs/queues/${encodeURIComponent(queueName)}/messages/send/`, {
    method: 'POST',
    json: {
      body,
      delay_seconds: delaySeconds,
      message_group_id: messageGroupId,
      message_deduplication_id: messageDeduplicationId,
    },
    errorMessage: 'Failed to send message',
  });
}

export async function receiveSqsMessages(
  queueName: string,
  maxNumber: number = 5,
  visibilityTimeout?: number,
  waitTimeSeconds: number = 0
): Promise<{ queue: string; messages: any[] }> {
  return requestJson(`/api/sqs/queues/${encodeURIComponent(queueName)}/messages/`, {
    method: 'POST',
    json: {
      max_number: maxNumber,
      visibility_timeout: visibilityTimeout,
      wait_time_seconds: waitTimeSeconds,
    },
    errorMessage: 'Failed to receive messages',
  });
}

export async function deleteSqsMessage(queueName: string, receiptHandle: string): Promise<any> {
  return requestJson(`/api/sqs/queues/${encodeURIComponent(queueName)}/messages/delete/`, { method: 'DELETE', json: { receipt_handle: receiptHandle }, errorMessage: 'Failed to delete message' });
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
  return requestJson('/api/sns/messages/publish/', {
    method: 'POST',
    json: {
      topic_arn: topicArn,
      message,
      subject,
      message_attributes: messageAttributes,
      message_structure: messageStructure,
      message_group_id: messageGroupId,
      message_deduplication_id: messageDeduplicationId,
    },
    errorMessage: 'Failed to publish message',
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
  return requestJson('/api/rds/instances/', {
    method: 'POST',
    json: {
      identifier,
      engine,
      username,
      password,
      db_instance_class: dbInstanceClass,
      allocated_storage: allocatedStorage,
      db_name: dbName,
      engine_version: engineVersion,
      enable_iam_auth: enableIamAuth,
    },
    errorMessage: 'Failed to create database instance',
  });
}

export async function modifyRdsInstance(
  identifier: string,
  dbInstanceClass?: string,
  allocatedStorage?: number,
  masterUserPassword?: string,
  applyImmediately: boolean = true
): Promise<any> {
  return requestJson(`/api/rds/instances/${encodeURIComponent(identifier)}/`, {
    method: 'PUT',
    json: {
      db_instance_class: dbInstanceClass,
      allocated_storage: allocatedStorage,
      master_user_password: masterUserPassword,
      apply_immediately: applyImmediately,
    },
    errorMessage: 'Failed to modify database instance',
  });
}

export async function rebootRdsInstance(identifier: string): Promise<any> {
  return requestJson(`/api/rds/instances/${encodeURIComponent(identifier)}/reboot/`, {
    method: 'POST',
    errorMessage: 'Failed to reboot database instance',
  });
}

export async function deleteRdsInstance(identifier: string, skipFinalSnapshot: boolean = true, finalSnapshotId: string = ''): Promise<any> {
  return requestJson(`/api/rds/instances/${encodeURIComponent(identifier)}/`, {
    method: 'DELETE',
    json: {
      skip_final_snapshot: skipFinalSnapshot,
      final_snapshot_identifier: finalSnapshotId,
    },
    errorMessage: 'Failed to delete database instance',
  });
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
  return requestJson('/api/rds/clusters/', {
    method: 'POST',
    json: {
      identifier,
      engine,
      username,
      password,
      database_name: databaseName,
      engine_version: engineVersion,
      enable_iam_auth: enableIamAuth,
    },
    errorMessage: 'Failed to create DB cluster',
  });
}

export async function deleteRdsCluster(identifier: string, skipFinalSnapshot: boolean = true, finalSnapshotId: string = ''): Promise<any> {
  return requestJson(`/api/rds/clusters/${encodeURIComponent(identifier)}/`, {
    method: 'DELETE',
    json: { skip_final_snapshot: skipFinalSnapshot, final_snapshot_identifier: finalSnapshotId },
    errorMessage: 'RDS operation failed',
  });
}

export async function createRdsParameterGroup(name: string, family: string, description: string): Promise<any> {
  return requestJson('/api/rds/parameter-groups/', {
    method: 'POST',
    json: { name, family, description },
    errorMessage: 'Failed to create parameter group',
  });
}

export async function deleteRdsParameterGroup(name: string): Promise<any> {
  return requestJson(`/api/rds/parameter-groups/${encodeURIComponent(name)}/`, { method: 'DELETE', errorMessage: 'RDS operation failed' });
}

// S3 bucket permissions and properties
/** GetBucketPolicy. Returns the policy as formatted JSON, or '' when the bucket has none. */
export async function fetchS3BucketPolicy(bucketName: string): Promise<string> {
  const data = await requestJson(`${s3Bucket(bucketName)}/policy/`, { errorMessage: 'Failed to read bucket policy' });
  return data.policy ? JSON.stringify(data.policy, null, 2) : '';
}

export async function putS3BucketPolicy(bucketName: string, policy: string): Promise<any> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(policy);
  } catch {
    throw new Error('Policy is not valid JSON.');
  }
  return requestJson(`${s3Bucket(bucketName)}/policy/`, {
    method: 'PUT',
    json: { policy: parsed },
    errorMessage: 'Failed to update bucket policy',
  });
}

export async function deleteS3BucketPolicy(bucketName: string): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/policy/`, { method: 'DELETE', errorMessage: 'Failed to delete bucket policy' });
}

/** GetBucketCors. Returns the CORSRules array, empty when no configuration exists. */
export async function fetchS3BucketCors(bucketName: string): Promise<any[]> {
  const data = await requestJson(`${s3Bucket(bucketName)}/cors/`, { errorMessage: 'Failed to read CORS configuration' });
  return Array.isArray(data.rules) ? data.rules : [];
}

export async function putS3BucketCors(bucketName: string, corsRules: unknown): Promise<any> {
  if (!Array.isArray(corsRules)) throw new Error('CORS configuration must be a JSON array of rules.');
  return requestJson(`${s3Bucket(bucketName)}/cors/`, {
    method: 'PUT',
    json: { rules: corsRules },
    errorMessage: 'Failed to update CORS',
  });
}

export async function deleteS3BucketCors(bucketName: string): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/cors/`, { method: 'DELETE', errorMessage: 'Failed to delete CORS configuration' });
}

/** GetBucketVersioning. A bucket that has never had versioning enabled has no Status, which the console shows as Disabled. */
export async function fetchS3BucketVersioning(bucketName: string): Promise<{ status: 'Enabled' | 'Suspended' | 'Disabled'; mfaDelete?: string }> {
  const data = await requestJson(`${s3Bucket(bucketName)}/versioning/`, { errorMessage: 'Failed to read versioning' });
  return { status: data.Status || 'Disabled', mfaDelete: data.MFADelete };
}

export async function putS3BucketVersioning(bucketName: string, status: 'Enabled' | 'Suspended'): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/versioning/`, {
    method: 'PUT',
    json: { status },
    errorMessage: 'Failed to update versioning',
  });
}

export interface S3PublicAccessBlock {
  BlockPublicAcls?: boolean;
  IgnorePublicAcls?: boolean;
  BlockPublicPolicy?: boolean;
  RestrictPublicBuckets?: boolean;
}

/** GetPublicAccessBlock. Returns null when the bucket has no configuration (every setting off). */
export async function fetchS3BucketPublicAccessBlock(bucketName: string): Promise<S3PublicAccessBlock | null> {
  const data = await requestJson(`${s3Bucket(bucketName)}/public-access-block/`, { errorMessage: 'Failed to read Block Public Access' });
  return data.configuration || null;
}

export async function putS3BucketPublicAccessBlock(bucketName: string, config: S3PublicAccessBlock): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/public-access-block/`, {
    method: 'PUT',
    json: { configuration: config },
    errorMessage: 'Failed to update public access block',
  });
}

/** GetBucketEncryption. Returns null when no default encryption configuration exists. */
export async function fetchS3BucketEncryption(bucketName: string): Promise<any | null> {
  const data = await requestJson(`${s3Bucket(bucketName)}/encryption/`, { errorMessage: 'Failed to read default encryption' });
  return data.configuration || null;
}

export async function fetchS3BucketTags(bucketName: string): Promise<Array<{ Key: string; Value: string }>> {
  const data = await requestJson(`${s3Bucket(bucketName)}/tags/`, { errorMessage: 'Failed to read bucket tags' });
  return data.tags || [];
}

export async function fetchS3ObjectTags(bucketName: string, key: string): Promise<Array<{ Key: string; Value: string }>> {
  const data = await requestJson(`${s3Bucket(bucketName)}/objects/tags/?${new URLSearchParams({ key })}`, { errorMessage: 'Failed to read object tags' });
  return data.tags || [];
}

export async function putS3ObjectTags(bucketName: string, key: string, tags: Array<{ Key: string; Value: string }>): Promise<any> {
  return requestJson(`${s3Bucket(bucketName)}/objects/tags/`, {
    method: 'PUT',
    json: { key, tags },
    errorMessage: 'Failed to update object tags',
  });
}

/** HeadObject, returned with the backend's snake_case field names. */
export async function fetchS3ObjectHead(bucketName: string, key: string): Promise<{ content_type?: string; content_length?: number; etag?: string; last_modified?: string; storage_class?: string; metadata?: Record<string, string>; versioning?: string } | null> {
  return requestJson(`${s3Bucket(bucketName)}/objects/head/?${new URLSearchParams({ key })}`, { errorMessage: 'Failed to read object metadata' });
}

// CloudFormation Operational APIs
export async function validateCloudFormationTemplate(templateBody: string): Promise<any> {
  return requestJson('/api/cloudformation/templates/validate/', {
    method: 'POST',
    json: { template_body: templateBody },
    errorMessage: 'Template validation failed',
  });
}

export async function createCloudFormationStack(
  stackName: string,
  templateBody: string,
  parameters?: any,
  capabilities?: string[],
  disableRollback: boolean = false
): Promise<any> {
  return requestJson('/api/cloudformation/stacks/', {
    method: 'POST',
    json: {
      stack_name: stackName,
      template_body: templateBody,
      parameters,
      capabilities: capabilities || [],
      disable_rollback: disableRollback,
    },
    errorMessage: 'Failed to create stack',
  });
}

export async function updateCloudFormationStack(
  stackName: string,
  templateBody?: string,
  parameters?: any,
  capabilities?: string[]
): Promise<any> {
  return requestJson(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/`, {
    method: 'PUT',
    json: {
      template_body: templateBody,
      parameters,
      capabilities,
    },
    errorMessage: 'Failed to update stack',
  });
}

export async function deleteCloudFormationStack(stackName: string): Promise<any> {
  return requestJson(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/`, {
    method: 'DELETE',
    errorMessage: 'CloudFormation operation failed',
  });
}

export async function createCloudFormationChangeSet(
  stackName: string,
  changeSetName: string,
  templateBody: string,
  changeSetType: string = 'UPDATE',
  parameters?: any,
  capabilities?: string[]
): Promise<any> {
  return requestJson('/api/cloudformation/change-sets/', {
    method: 'POST',
    json: {
      stack_name: stackName,
      change_set_name: changeSetName,
      template_body: templateBody,
      change_set_type: changeSetType,
      parameters,
      capabilities,
    },
    errorMessage: 'Failed to create change set',
  });
}

export async function describeCloudFormationChangeSet(stackName: string, changeSetName: string): Promise<any> {
  return requestJson(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/change-sets/${encodeURIComponent(changeSetName)}/`, {
    errorMessage: 'CloudFormation operation failed',
  });
}

export async function executeCloudFormationChangeSet(stackName: string, changeSetName: string): Promise<any> {
  return requestJson(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/change-sets/${encodeURIComponent(changeSetName)}/`, {
    method: 'POST',
    errorMessage: 'Failed to execute change set',
  });
}

export async function deleteCloudFormationChangeSet(stackName: string, changeSetName: string): Promise<any> {
  return requestJson(`/api/cloudformation/stacks/${encodeURIComponent(stackName)}/change-sets/${encodeURIComponent(changeSetName)}/`, {
    method: 'DELETE',
    errorMessage: 'CloudFormation operation failed',
  });
}

// EventBridge Operational APIs
export async function createEventBus(name: string, description: string = ''): Promise<any> {
  return requestJson('/api/eventbridge/buses/create/', {
    method: 'POST',
    json: { name, description },
    errorMessage: 'Failed to create event bus',
  });
}

export async function deleteEventBus(name: string): Promise<any> {
  const res = await fetch('/api/eventbridge/buses/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!res.ok || data.error || data.removed === false || data.failed_entries?.length) throw new Error(data.error || data.failed_entries?.[0]?.ErrorMessage || 'EventBridge operation failed');
  return data;
}

export async function putEventRule(
  name: string,
  busName: string = 'default',
  pattern?: any,
  schedule?: string,
  description: string = '',
  state: string = 'ENABLED'
): Promise<any> {
  return requestJson('/api/eventbridge/rules/put/', {
    method: 'POST',
    json: {
      name,
      event_bus_name: busName,
      event_pattern: pattern ? (typeof pattern === 'string' ? pattern : JSON.stringify(pattern)) : undefined,
      schedule_expression: schedule,
      description,
      state,
    },
    errorMessage: 'Failed to put event rule',
  });
}

export async function setEventRuleState(name: string, busName: string = 'default', enabled: boolean = true): Promise<any> {
  return requestJson('/api/eventbridge/rules/state/', {
    method: 'POST',
    json: { name, event_bus_name: busName, enabled },
    errorMessage: 'Failed to change rule state',
  });
}

export async function deleteEventRule(name: string, busName: string = 'default'): Promise<any> {
  const res = await fetch('/api/eventbridge/rules/delete/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ name, event_bus_name: busName }),
  });
  const data = await res.json();
  if (!res.ok || data.error || data.removed === false || data.failed_entries?.length) throw new Error(data.error || data.failed_entries?.[0]?.ErrorMessage || 'EventBridge operation failed');
  return data;
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
  if (!res.ok || data.error || data.failed_entry_count > 0 || data.failed_entries?.length) throw new Error(data.error || data.failed_entries?.[0]?.ErrorMessage || 'Failed to add target');
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
  const data = await res.json();
  if (!res.ok || data.error || data.removed === false || data.failed_entries?.length) throw new Error(data.error || data.failed_entries?.[0]?.ErrorMessage || 'EventBridge operation failed');
  return data;
}

export async function putEvents(entries: any[]): Promise<any> {
  if (entries.length !== 1) throw new Error('Send one event at a time.');
  const entry = entries[0];
  return requestJson('/api/eventbridge/events/put/', {
    method: 'POST',
    json: { event_bus_name: entry.EventBusName, source: entry.Source, detail_type: entry.DetailType, detail: entry.Detail, resources: entry.Resources },
    errorMessage: 'Failed to send events',
  });
}

// ----------------------------------------------------
// Phase 2: Core Service Consoles Expansion API Helpers
// ----------------------------------------------------

// IAM users, policies, access keys and group membership
const iamPrincipal = (type: 'user' | 'role' | 'group', name: string) => `/api/iam/principals/${type}/${encodeURIComponent(name)}`;

/** CreateUser. The dashboard also adds a GetCallerIdentity inline policy so the user can sign in to this console. */
export async function createIamUser(userName: string, addBaselinePolicy: boolean = true): Promise<{ user_name: string; arn: string; baseline_policy: string | null }> {
  return requestJson('/api/iam/users/', { method: 'POST', json: { user_name: userName, add_baseline_policy: addBaselinePolicy }, errorMessage: 'Failed to create user' });
}

/** DeleteUser. With force, first removes access keys, login profile, policies and group memberships, as the console does. */
export async function deleteIamUser(userName: string, force: boolean = true): Promise<any> {
  return requestJson(`/api/iam/users/${encodeURIComponent(userName)}/`, { method: 'DELETE', json: { force }, errorMessage: 'Failed to delete user' });
}

export async function deleteIamGroup(groupName: string, force: boolean = true): Promise<any> {
  return requestJson(`/api/iam/groups/${encodeURIComponent(groupName)}/`, { method: 'DELETE', json: { force }, errorMessage: 'Failed to delete group' });
}

export async function createIamPolicy(policyName: string, policyDocument: any, description: string = ''): Promise<any> {
  let document = policyDocument;
  if (typeof document === 'string') {
    try {
      document = JSON.parse(document);
    } catch {
      throw new Error('Policy document is not valid JSON.');
    }
  }
  return requestJson('/api/iam/policies/', { method: 'POST', json: { name: policyName, document, description }, errorMessage: 'Failed to create policy' });
}

export async function deleteIamPolicy(policyArn: string): Promise<any> {
  return requestJson('/api/iam/policies/', { method: 'DELETE', json: { policy_arn: policyArn }, errorMessage: 'Failed to delete policy' });
}

/** GetPolicy plus GetPolicyVersion for the default version. */
export async function fetchIamPolicyDocument(policyArn: string): Promise<{ default_version: string; document: any }> {
  return requestJson('/api/iam/policies/document/', { method: 'POST', json: { policy_arn: policyArn }, errorMessage: 'Failed to read policy document' });
}

/** GetUserPolicy, GetRolePolicy or GetGroupPolicy. */
export async function fetchIamInlinePolicyDocument(type: 'user' | 'role' | 'group', principal: string, policyName: string): Promise<{ document: any }> {
  return requestJson(`${iamPrincipal(type, principal)}/inline-policies/${encodeURIComponent(policyName)}/document/`, { errorMessage: 'Failed to read inline policy' });
}

export async function createIamUserAccessKey(userName: string): Promise<any> {
  return requestJson(`/api/iam/users/${encodeURIComponent(userName)}/access-keys/`, { method: 'POST', errorMessage: 'Failed to create access key' });
}

export async function deleteIamUserAccessKey(userName: string, accessKeyId: string): Promise<any> {
  return requestJson(`/api/iam/users/${encodeURIComponent(userName)}/access-keys/${encodeURIComponent(accessKeyId)}/`, { method: 'DELETE', errorMessage: 'Failed to delete access key' });
}

export async function updateIamAccessKeyState(userName: string, accessKeyId: string, status: 'Active' | 'Inactive'): Promise<any> {
  return requestJson(`/api/iam/users/${encodeURIComponent(userName)}/access-keys/${encodeURIComponent(accessKeyId)}/`, { method: 'PUT', json: { status }, errorMessage: 'Failed to update access key' });
}

/** AttachUserPolicy / AttachRolePolicy / AttachGroupPolicy. */
export async function attachIamPolicy(type: 'user' | 'role' | 'group', principal: string, policyArn: string): Promise<any> {
  return requestJson(`${iamPrincipal(type, principal)}/attached-policies/`, { method: 'POST', json: { policy_arn: policyArn }, errorMessage: 'Failed to attach policy' });
}

/** DetachUserPolicy / DetachRolePolicy / DetachGroupPolicy. */
export async function detachIamPolicy(type: 'user' | 'role' | 'group', principal: string, policyArn: string): Promise<any> {
  return requestJson(`${iamPrincipal(type, principal)}/attached-policies/`, { method: 'DELETE', json: { policy_arn: policyArn }, errorMessage: 'Failed to detach policy' });
}

export async function addUserToIamGroup(groupName: string, userName: string): Promise<any> {
  return requestJson(`/api/iam/groups/${encodeURIComponent(groupName)}/members/`, { method: 'POST', json: { user_name: userName }, errorMessage: 'Failed to add user to group' });
}

export async function removeUserFromIamGroup(groupName: string, userName: string): Promise<any> {
  return requestJson(`/api/iam/groups/${encodeURIComponent(groupName)}/members/`, { method: 'DELETE', json: { user_name: userName }, errorMessage: 'Failed to remove user from group' });
}

// DynamoDB capacity, backups and TTL
/** UpdateTable BillingMode (and ProvisionedThroughput when provisioned). */
export async function updateDynamoDbTableCapacity(tableName: string, billingMode: 'PAY_PER_REQUEST' | 'PROVISIONED', readUnits?: number, writeUnits?: number): Promise<any> {
  return requestJson(`${dynamoTable(tableName)}/`, {
    method: 'PATCH',
    json: { billing_mode: billingMode, read_capacity_units: readUnits, write_capacity_units: writeUnits },
    errorMessage: 'Failed to update capacity',
  });
}

/** UpdateContinuousBackups (point-in-time recovery). */
export async function updateDynamoDbPitr(tableName: string, enabled: boolean): Promise<any> {
  return requestJson(`${dynamoTable(tableName)}/backups/`, { method: 'PUT', json: { enabled }, errorMessage: 'Failed to update point-in-time recovery' });
}

export async function updateDynamoDbTtl(tableName: string, attributeName: string, enabled: boolean): Promise<any> {
  return requestJson(`${dynamoTable(tableName)}/ttl/`, { method: 'PUT', json: { attribute_name: attributeName, enabled }, errorMessage: 'Failed to update TTL' });
}

// Lambda configuration and aliases
/** UpdateFunctionConfiguration with only the fields given. */
export async function updateLambdaFunctionConfiguration(
  functionName: string,
  config: { timeout?: number; memorySize?: number; description?: string; handler?: string; runtime?: string; ephemeralStorage?: number }
): Promise<any> {
  const configuration: Record<string, unknown> = {};
  if (config.timeout !== undefined) configuration.Timeout = config.timeout;
  if (config.memorySize !== undefined) configuration.MemorySize = config.memorySize;
  if (config.description !== undefined) configuration.Description = config.description;
  if (config.handler !== undefined) configuration.Handler = config.handler;
  if (config.runtime !== undefined) configuration.Runtime = config.runtime;
  if (config.ephemeralStorage !== undefined) configuration.EphemeralStorage = { Size: config.ephemeralStorage };
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/`, { method: 'PATCH', json: { configuration }, errorMessage: 'Failed to update configuration' });
}

/** UpdateFunctionConfiguration replaces the whole Environment, so pass every variable to keep. */
export async function updateLambdaEnvironmentVariables(functionName: string, variables: Record<string, string>): Promise<any> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/`, { method: 'PATCH', json: { configuration: { Environment: { Variables: variables } } }, errorMessage: 'Failed to update environment variables' });
}

export async function fetchLambdaAliases(functionName: string): Promise<any[]> {
  try {
    const res = await fetch(`/api/lambda/functions/${encodeURIComponent(functionName)}/aliases/`);
    if (!res.ok) return [];
    const data = await res.json();
    return data.Aliases || data.aliases || [];
  } catch {
    return [];
  }
}

export async function createLambdaAlias(functionName: string, name: string, functionVersion: string = '$LATEST', description: string = ''): Promise<any> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/aliases/`, { method: 'POST', json: { name, function_version: functionVersion, description }, errorMessage: 'Failed to create alias' });
}

export async function deleteLambdaAlias(functionName: string, name: string): Promise<any> {
  return requestJson(`/api/lambda/functions/${encodeURIComponent(functionName)}/aliases/${encodeURIComponent(name)}/`, { method: 'DELETE', errorMessage: 'Failed to delete alias' });
}

// KMS Additional APIs
// CloudWatch Additional APIs
// Step Functions Additional APIs
export async function stepFunctionsRequest(path = '', method = 'GET', payload?: Record<string, unknown>): Promise<any> {
  const response = await fetch(`/api/stepfunctions/${path}`, { method,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  });
  const data = await response.json();
  // DescribeExecution uses error/cause as workflow outcome fields, even on HTTP 200.
  const executionOutcome = path.startsWith('executions/detail/') && typeof data.status === 'string';
  if (!response.ok || (data.error && !executionOutcome)) throw new Error(data.error || `Step Functions request failed (${response.status})`);
  return data;
}
export async function stopStepFunctionExecution(executionArn: string, error = 'UserCancelled', cause = 'Execution stopped manually from workbench') {
  return stepFunctionsRequest('executions/stop/', 'POST', { execution_arn: executionArn, error, cause });
}
export async function updateStepFunctionDefinition(stateMachineArn: string, definition: string, roleArn?: string) {
  return stepFunctionsRequest(`state-machines/${encodeURIComponent(stateMachineArn)}/`, 'PATCH', { definition, role_arn: roleArn });
}
export async function fetchStepFunctionActivities(): Promise<any[]> { return (await stepFunctionsRequest('activities/')).activities || []; }
export async function createStepFunctionActivity(name: string) { return stepFunctionsRequest('activities/', 'POST', { name }); }
export async function deleteStepFunctionActivity(activityArn: string) { return stepFunctionsRequest('activities/', 'DELETE', { activity_arn: activityArn }); }
export async function sendStepFunctionTaskSuccess(taskToken: string, output: string) { return stepFunctionsRequest('tasks/callback/', 'POST', { task_token: taskToken, output, success: true }); }
export async function sendStepFunctionTaskFailure(taskToken: string, error = 'ManualFailure', cause = 'Task failed from workbench') { return stepFunctionsRequest('tasks/callback/', 'POST', { task_token: taskToken, error, cause, success: false }); }

// Cognito Additional APIs
export async function createCognitoUserGroup(userPoolId: string, groupName: string, description: string = ''): Promise<any> {
  return postConsoleAction(`/api/cognito/user-pools/${encodeURIComponent(userPoolId)}/groups/`, { group_name: groupName, description });
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

export async function createCognitoIdentityPool(identityPoolName: string, allowUnauthenticated: boolean = false): Promise<any> {
  return requestJson('/api/cognito/identity-pools/', {
    method: 'POST',
    json: { identity_pool_name: identityPoolName, allow_unauthenticated_identities: allowUnauthenticated },
    errorMessage: 'Failed to create identity pool',
  });
}

// API Gateway Additional APIs
// SSM Additional APIs
// ==========================================
// Phase 3: ECS APIs
// ==========================================
export async function createEcsCluster(name: string, capacityProviders?: string[], tags?: Array<{ Key: string; Value: string }>): Promise<any> {
  return requestJson('/api/ecs/clusters/', {
    method: 'POST',
    json: { name, capacity_providers: capacityProviders, tags },
    ignoreErrorField: true,
  });
}

export async function deleteEcsCluster(cluster: string): Promise<any> {
  return requestJson('/api/ecs/clusters/delete/', { method: 'POST', json: { cluster }, ignoreErrorField: true });
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
  return requestJson('/api/ecs/task-definitions/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function deregisterEcsTaskDefinition(taskDefinition: string): Promise<any> {
  return requestJson('/api/ecs/task-definitions/detail/', {
    method: 'POST',
    json: { task_definition: taskDefinition },
    ignoreErrorField: true,
  });
}

export async function runEcsTask(params: {
  cluster: string;
  task_definition: string;
  launch_type?: string;
  count?: number;
  network_configuration?: any;
}): Promise<any> {
  return requestJson('/api/ecs/tasks/run/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function stopEcsTask(cluster: string, task: string, reason?: string): Promise<any> {
  return requestJson('/api/ecs/tasks/stop/', {
    method: 'POST',
    json: { cluster, task, reason: reason || 'Stopped from Floci Workbench' },
    ignoreErrorField: true,
  });
}

export async function createEcsService(params: {
  network_configuration?: any;
  cluster: string;
  service_name: string;
  task_definition: string;
  desired_count?: number;
  launch_type?: string;
}): Promise<any> {
  return requestJson('/api/ecs/services/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function updateEcsService(params: {
  cluster: string;
  service: string;
  desired_count?: number;
  task_definition?: string;
}): Promise<any> {
  return requestJson('/api/ecs/services/update/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function deleteEcsService(cluster: string, service: string, force: boolean = true): Promise<any> {
  return requestJson('/api/ecs/services/delete/', { method: 'POST', json: { cluster, service, force }, ignoreErrorField: true });
}

export async function updateEcsContainerInstanceState(cluster: string, containerInstances: string[], status: 'ACTIVE' | 'DRAINING'): Promise<any> {
  return requestJson('/api/ecs/container-instances/state/', {
    method: 'POST',
    json: { cluster, container_instances: containerInstances, status },
    ignoreErrorField: true,
  });
}

// ==========================================
// Phase 3: ECR APIs
// ==========================================
export async function createEcrRepository(name: string, imageTagMutability: 'MUTABLE' | 'IMMUTABLE' = 'MUTABLE'): Promise<any> {
  return requestJson('/api/ecr/repositories/', {
    method: 'POST',
    json: { name, image_tag_mutability: imageTagMutability },
    ignoreErrorField: true,
  });
}

export async function deleteEcrRepository(repositoryName: string, force: boolean = true): Promise<any> {
  return requestJson('/api/ecr/repositories/delete/', {
    method: 'POST',
    json: { repository_name: repositoryName, force },
    ignoreErrorField: true,
  });
}

export async function batchDeleteEcrImages(repositoryName: string, imageIds: Array<{ imageDigest?: string; imageTag?: string }>): Promise<any> {
  return requestJson('/api/ecr/images/delete/', {
    method: 'POST',
    json: { repository_name: repositoryName, image_ids: imageIds },
    ignoreErrorField: true,
  });
}

export async function putEcrTagMutability(repositoryName: string, imageTagMutability: 'MUTABLE' | 'IMMUTABLE'): Promise<any> {
  return requestJson('/api/ecr/tag-mutability/', {
    method: 'POST',
    json: { repository_name: repositoryName, image_tag_mutability: imageTagMutability },
    ignoreErrorField: true,
  });
}

export async function putEcrLifecyclePolicy(repositoryName: string, lifecyclePolicyText: string): Promise<any> {
  return requestJson('/api/ecr/lifecycle-policy/', {
    method: 'POST',
    json: { repository_name: repositoryName, lifecycle_policy_text: lifecyclePolicyText },
    ignoreErrorField: true,
  });
}

export async function runEcrGarbageCollection(): Promise<any> {
  return requestJson('/api/ecr/garbage-collection/', { method: 'POST', json: {}, ignoreErrorField: true });
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
  origin_type?: string;
  origin_protocol_policy?: string;
  http_port?: number;
  https_port?: number;
}): Promise<any> {
  return requestJson('/api/cloudfront/distributions/', {
    method: 'POST',
    json: {
      caller_reference: params.caller_reference || crypto.randomUUID(),
      origin_domain_name: params.origin_domain_name,
      origin_id: params.origin_id || `origin-${Date.now()}`,
      comment: params.comment || '',
      enabled: params.enabled ?? true,
      viewer_protocol_policy: params.viewer_protocol_policy || 'redirect-to-https',
      aliases: params.aliases,
      cache_policy_id: params.cache_policy_id,
      origin_type: params.origin_type,
      origin_protocol_policy: params.origin_protocol_policy,
      http_port: params.http_port,
      https_port: params.https_port,
    },
    ignoreErrorField: true,
  });
}

export async function updateCloudFrontDistribution(distributionId: string, params: { enabled?: boolean; comment?: string; config?: any; if_match?: string }): Promise<any> {
  return requestJson(`/api/cloudfront/distributions/${distributionId}/`, { method: 'PUT', json: params, ignoreErrorField: true });
}

export async function deleteCloudFrontDistribution(distributionId: string, ifMatch?: string): Promise<any> {
  return requestJson(`/api/cloudfront/distributions/${distributionId}/`, {
    method: 'DELETE',
    json: { if_match: ifMatch || '' },
    ignoreErrorField: true,
  });
}

export async function createCloudFrontInvalidation(distributionId: string, paths: string[]): Promise<any> {
  return requestJson(`/api/cloudfront/distributions/${distributionId}/invalidations/`, {
    method: 'POST',
    json: { paths, caller_reference: `inv-${crypto.randomUUID()}` },
    ignoreErrorField: true,
  });
}

export async function createCloudFrontCachePolicy(params: { name: string; default_ttl?: number; max_ttl?: number; min_ttl?: number; comment?: string }): Promise<any> {
  return requestJson('/api/cloudfront/cache-policies/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function createCloudFrontFunction(name: string, functionCode: string, comment?: string): Promise<any> {
  return requestJson('/api/cloudfront/functions/', {
    method: 'POST',
    json: {
      name,
      comment: comment || '', runtime: 'cloudfront-js-2.0',
      code: functionCode,
    },
    ignoreErrorField: true,
  });
}

export async function deleteCloudFrontFunction(name: string, ifMatch?: string): Promise<any> {
  return requestJson(`/api/cloudfront/functions/${name}/`, {
    method: 'DELETE',
    json: { if_match: ifMatch || '' },
    ignoreErrorField: true,
  });
}

export async function publishCloudFrontFunction(name: string): Promise<any> {
  const response = await fetch(`/api/cloudfront/functions/${encodeURIComponent(name)}/`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() }, body: JSON.stringify({}) });
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.error || 'Unable to publish function');
  return result;
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
  return requestJson('/api/elasticloadbalancing/load-balancers/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function deleteElbv2LoadBalancer(loadBalancerArn: string): Promise<any> {
  return requestJson(`/api/elasticloadbalancing/load-balancers/${encodeURIComponent(loadBalancerArn)}/`, {
    method: 'DELETE',
    ignoreErrorField: true,
  });
}

export async function createElbv2TargetGroup(params: {
  name: string;
  protocol?: string;
  port?: number;
  target_type?: 'instance' | 'ip' | 'lambda';
  vpc_id?: string;
  health_check_path?: string;
}): Promise<any> {
  return requestJson('/api/elasticloadbalancing/target-groups/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function deleteElbv2TargetGroup(targetGroupArn: string): Promise<any> {
  return requestJson(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(targetGroupArn)}/`, {
    method: 'DELETE',
    ignoreErrorField: true,
  });
}

export async function registerElbv2Targets(targetGroupArn: string, targets: Array<{ Id: string; Port?: number }>): Promise<any> {
  return requestJson(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(targetGroupArn)}/targets/`, {
    method: 'POST',
    json: { targets },
    ignoreErrorField: true,
  });
}

export async function deregisterElbv2Targets(targetGroupArn: string, targets: Array<{ Id: string; Port?: number }>): Promise<any> {
  return requestJson(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(targetGroupArn)}/targets/`, {
    method: 'DELETE',
    json: { targets },
    ignoreErrorField: true,
  });
}

export async function createElbv2Listener(params: {
  load_balancer_arn: string;
  protocol: string;
  port: number;
  target_group_arn: string;
}): Promise<any> {
  return requestJson('/api/elasticloadbalancing/listeners/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function deleteElbv2Listener(listenerArn: string): Promise<any> {
  return requestJson(`/api/elasticloadbalancing/listeners/${encodeURIComponent(listenerArn)}/`, {
    method: 'DELETE',
    ignoreErrorField: true,
  });
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
  return requestJson('/api/athena/queries/start/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function stopAthenaQuery(queryExecutionId: string): Promise<any> {
  return requestJson('/api/athena/queries/stop/', {
    method: 'POST',
    json: { query_execution_id: queryExecutionId },
    ignoreErrorField: true,
  });
}

export async function fetchAthenaQueryResults(queryExecutionId: string, maxResults: number = 50, nextToken: string = ''): Promise<any> {
  return requestJson('/api/athena/queries/results/', {
    method: 'POST',
    json: { query_execution_id: queryExecutionId, max_results: maxResults, next_token: nextToken },
    ignoreErrorField: true,
  });
}

export async function fetchAthenaQueryDetail(queryExecutionId: string): Promise<any> {
  return requestJson('/api/athena/queries/detail/', {
    method: 'POST',
    json: { query_execution_id: queryExecutionId },
    ignoreErrorField: true,
  });
}

export async function createAthenaWorkgroup(name: string, description?: string, outputLocation?: string): Promise<any> {
  return requestJson('/api/athena/workgroups/', {
    method: 'POST',
    json: { name, description: description || '', output_location: outputLocation || '' },
    ignoreErrorField: true,
  });
}

// ==========================================
// Phase 3: AppSync APIs
// ==========================================
export async function createAppSyncGraphQLApi(name: string, authenticationType: 'API_KEY' | 'AWS_IAM' | 'OPENID_CONNECT' | 'AMAZON_COGNITO_USER_POOLS' = 'API_KEY'): Promise<any> {
  return requestJson('/api/appsync/apis/', {
    method: 'POST',
    json: { name, authentication_type: authenticationType },
    ignoreErrorField: true,
  });
}

export async function deleteAppSyncGraphQLApi(apiId: string): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/`, { method: 'DELETE', ignoreErrorField: true });
}

export async function createAppSyncSchema(apiId: string, definition: string): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/schema/`, { method: 'POST', json: { definition }, ignoreErrorField: true });
}

export async function createAppSyncApiKey(apiId: string, description?: string, expires?: number): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/api-keys/`, {
    method: 'POST',
    json: { description: description || '', expires },
    ignoreErrorField: true,
  });
}

export async function deleteAppSyncApiKey(apiId: string, keyId: string): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/api-keys/`, { method: 'DELETE', json: { key_id: keyId }, ignoreErrorField: true });
}

export async function createAppSyncDataSource(apiId: string, name: string, sourceType: string = 'NONE', description?: string, options?: { service_role_arn?: string; config?: Record<string, unknown> }): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/data-sources/`, {
    method: 'POST',
    json: { name, source_type: sourceType, description: description || '', ...options },
    ignoreErrorField: true,
  });
}

export async function deleteAppSyncDataSource(apiId: string, name: string): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/data-sources/`, { method: 'DELETE', json: { name }, ignoreErrorField: true });
}

export async function createAppSyncResolver(apiId: string, typeName: string, fieldName: string, dataSourceName?: string, templates?: { request_mapping_template: string; response_mapping_template: string }): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/resolvers/`, {
    method: 'POST',
    json: { type_name: typeName, field_name: fieldName, data_source_name: dataSourceName || '', ...templates },
    ignoreErrorField: true,
  });
}

export async function deleteAppSyncResolver(apiId: string, typeName: string, fieldName: string): Promise<any> {
  return requestJson(`/api/appsync/apis/${apiId}/resolvers/`, {
    method: 'DELETE',
    json: { type_name: typeName, field_name: fieldName },
    ignoreErrorField: true,
  });
}

// ==========================================
// Phase 3: SES APIs
// ==========================================
export async function verifySesEmailIdentity(emailAddress: string): Promise<any> {
  return requestJson('/api/ses/identities/email/', { method: 'POST', json: { email_address: emailAddress }, ignoreErrorField: true });
}

export async function verifySesDomainIdentity(domain: string): Promise<any> {
  return requestJson('/api/ses/identities/domain/', { method: 'POST', json: { domain }, ignoreErrorField: true });
}

export async function deleteSesIdentity(identity: string): Promise<any> {
  return requestJson(`/api/ses/identities/${encodeURIComponent(identity)}/`, { method: 'DELETE', ignoreErrorField: true });
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
  return requestJson('/api/ses/email/send/', { method: 'POST', json: params, ignoreErrorField: true });
}

export async function createSesTemplate(name: string, subject: string, htmlPart?: string, textPart?: string): Promise<any> {
  return requestJson('/api/ses/templates/', {
    method: 'POST',
    json: { template_name: name, subject, html: htmlPart || '', text: textPart || '' },
    ignoreErrorField: true,
  });
}

export async function renderSesTemplate(templateName: string, templateData: string): Promise<any> {
  return requestJson(`/api/ses/templates/${encodeURIComponent(templateName)}/render/`, {
    method: 'POST',
    json: { template_data: templateData },
    ignoreErrorField: true,
  });
}

export async function deleteSesTemplate(templateName: string): Promise<any> {
  return requestJson(`/api/ses/templates/${encodeURIComponent(templateName)}/`, { method: 'DELETE', ignoreErrorField: true });
}

export async function createSesConfigurationSet(name: string): Promise<any> {
  return requestJson('/api/ses/configuration-sets/', { method: 'POST', json: { name }, ignoreErrorField: true });
}

export async function updateSesSendingEnabled(enabled: boolean): Promise<any> {
  return requestJson('/api/ses/account/sending/', { method: 'PUT', json: { enabled }, ignoreErrorField: true });
}

export async function clearSesMailbox(): Promise<any> {
  return requestJson('/api/ses/mailbox/', { method: 'DELETE', json: {}, ignoreErrorField: true });
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
  return requestJson('/api/eks/clusters/', {
    method: 'POST',
    json: {
      name,
      role_arn: roleArn,
      version,
      subnet_ids: subnetIds,
      security_group_ids: securityGroupIds,
      tags,
    },
    errorMessage: 'Failed to create EKS cluster',
  });
}

export async function deleteEksCluster(name: string): Promise<any> {
  return requestJson('/api/eks/clusters/delete/', { method: 'POST', json: { name }, errorMessage: 'Failed to delete EKS cluster' });
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
  return requestJson(`/api/eks/clusters/${encodeURIComponent(clusterName)}/nodegroups/`, {
    method: 'POST',
    json: {
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
    },
    errorMessage: 'Failed to create EKS node group',
  });
}

export async function deleteEksNodegroup(clusterName: string, nodegroupName: string): Promise<any> {
  return requestJson(`/api/eks/clusters/${encodeURIComponent(clusterName)}/nodegroups/${encodeURIComponent(nodegroupName)}/`, {
    method: 'DELETE',
    errorMessage: 'Failed to delete EKS node group',
  });
}

export async function createEksFargateProfile(
  clusterName: string,
  profileName: string,
  podExecutionRoleArn: string,
  subnets?: string[],
  selectors?: Array<{ namespace: string; labels?: Record<string, string> }>,
  tags?: Record<string, string>
): Promise<any> {
  return requestJson(`/api/eks/clusters/${encodeURIComponent(clusterName)}/fargate-profiles/`, {
    method: 'POST',
    json: {
      profile_name: profileName,
      pod_execution_role_arn: podExecutionRoleArn,
      subnets,
      selectors,
      tags,
    },
    errorMessage: 'Failed to create EKS Fargate profile',
  });
}

export async function deleteEksFargateProfile(clusterName: string, profileName: string): Promise<any> {
  return requestJson(`/api/eks/clusters/${encodeURIComponent(clusterName)}/fargate-profiles/${encodeURIComponent(profileName)}/`, {
    method: 'DELETE',
    errorMessage: 'Failed to delete EKS Fargate profile',
  });
}

export async function fetchEksKubeconfig(clusterName: string): Promise<any> {
  return requestJson(`/api/eks/clusters/${encodeURIComponent(clusterName)}/kubeconfig/`, {
    errorMessage: 'Failed to fetch EKS kubeconfig',
  });
}

export async function runAppSyncGraphQL(apiId: string, query: string, variables: Record<string, unknown>, apiKey?: string, operationName?: string): Promise<any> {
  const response = await fetch('/api/appsync/graphql/run/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify({ api_id: apiId, query, variables, api_key: apiKey, operation_name: operationName }),
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

/** Dedicated KMS endpoints; /api/kms/ is a read-only inventory, not an action dispatcher. */
export async function executeKmsAction(action: string, payload: Record<string, any>): Promise<any> {
  const paths: Record<string, [string, string]> = {
    create_key: ['POST', 'keys'], create_alias: ['POST', 'aliases'],
    encrypt: ['POST', 'crypto/encrypt'], decrypt: ['POST', 'crypto/decrypt'],
    generate_data_key: ['POST', 'data-keys'], set_key_rotation: ['POST', 'rotation'],
    set_key_enabled: ['POST', 'key-state'], put_key_policy: ['PUT', 'key-policy'],
    create_grant: ['POST', 'grants'], revoke_grant: ['DELETE', 'grants'],
  };
  const target = paths[action];
  if (!target) throw new Error(`Unsupported KMS action: ${action}`);
  const response = await fetch(`/api/kms/${target[1]}/`, {
    method: target[0], headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || result.error) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}

/** Secrets Manager uses dedicated endpoints; inventory is read-only. */
export async function secretsRequest(path = '', method = 'GET', payload?: Record<string, unknown>): Promise<any> {
  const response = await fetch(`/api/secretsmanager/${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Secrets Manager request failed (${response.status})`);
  return data;
}

export async function cloudWatchRequest(path = '', method = 'GET', payload?: Record<string, unknown>): Promise<any> {
  const response = await fetch(`/api/cloudwatch/${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `CloudWatch request failed (${response.status})`);
  return data;
}

export async function snsRequest(path = '', method = 'GET', payload?: Record<string, unknown>): Promise<any> {
  const response = await fetch(`/api/sns/${path}`, { method,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `SNS request failed (${response.status})`);
  return data;
}
