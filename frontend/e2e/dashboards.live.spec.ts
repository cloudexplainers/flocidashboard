/// <reference types="node" />
import { existsSync } from 'fs';
import { execFileSync } from 'child_process';
import { test, expect } from '@playwright/test';

const python = process.env.E2E_PYTHON || (existsSync('../.venv/bin/python') ? '../.venv/bin/python' : 'python3');
const flociEndpoint = process.env.E2E_FLOCI_ENDPOINT || process.env.FLOCI_AWS_ENDPOINT_URL || 'http://localhost:4566';

// Run explicitly against a local Floci instance:
// E2E_FLOCI_ENDPOINT=http://localhost:4566 npx playwright test dashboards.live.spec.ts --project=live
for (const protocol of ['HTTP', 'REST']) {
  test(`API Gateway ${protocol} creation, inventory, and deletion against Floci`, async ({ page }) => {
    test.setTimeout(60_000);
    const name = `ui-review-${protocol.toLowerCase()}-${Date.now()}`;
    let createdId = '';
    let csrf = '';
    await page.goto('/apigateway/');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    try {
      await page.getByRole('button', { name: 'Create API', exact: true }).click();
      const modal = page.getByRole('dialog');
      await modal.getByLabel('API name', { exact: true }).fill(name);
      await modal.getByRole('button', { name: /^API type / }).click();
      await page.getByRole('option', { name: `${protocol} API`, exact: true }).click();
      const created = page.waitForResponse(response => response.url().endsWith('/api/apigateway/apis/create/') && response.request().method() === 'POST');
      await modal.getByRole('button', { name: 'Create API', exact: true }).click();
      const response = await created;
      const payload = await response.json();
      expect(response.ok(), JSON.stringify(payload)).toBe(true);
      createdId = payload.api_id;
      expect(createdId).toBeTruthy();
      await expect(modal).not.toBeVisible();
      await page.getByPlaceholder('Find APIs by name, ID, or protocol', { exact: true }).fill(name);
      await page.getByRole('button', { name, exact: true }).click();
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
      await page.reload();
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Request flow', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Test selected route', exact: true })).toBeDisabled();
      await expect(page.getByRole('tab', { name: protocol === 'HTTP' ? 'Routes' : 'Resources and methods', exact: true })).toBeVisible();
      await page.getByRole('tab', { name: 'Stages (0)', exact: true }).click();
      await expect(page.getByText('No stages deployed.', { exact: true })).toBeVisible();
      await page.screenshot({ path: `test-results/screenshots/apigateway-${protocol.toLowerCase()}-live.png`, fullPage: true });
      await page.getByRole('button', { name: 'Delete API', exact: true }).click();
      const deleted = page.waitForResponse(response => response.url().endsWith('/api/apigateway/apis/delete/') && response.request().method() === 'POST');
      await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
      const deletedResponse = await deleted;
      expect(deletedResponse.ok(), await deletedResponse.text()).toBe(true);
      await expect(page.getByRole('dialog')).not.toBeVisible();
      await expect(page.getByRole('button', { name, exact: true })).toHaveCount(0);
      createdId = '';
    } finally {
      if (createdId) {
        const cleanup = await page.request.post('/api/apigateway/apis/delete/', {
          headers: { 'X-CSRFToken': csrf }, data: { api_id: createdId, api_type: protocol.toLowerCase() },
        });
        expect(cleanup.ok(), await cleanup.text()).toBe(true);
      }
    }
  });
}

test('ECS live resource pages: create, inspect, update, task navigation, stop and delete', async ({ page }) => {
  test.setTimeout(180_000);
  const name = `ui-ecs-${Date.now()}`;
  let definition = '';
  let vpc = '';
  let subnet = '';
  let serviceCreated = false;
  let clusterCreated = false;
  let csrf = '';
  const taskArns: string[] = [];
  const post = async (path: string, data: any) => {
    const response = await page.request.post(path, { headers: { 'X-CSRFToken': csrf }, data });
    const result = await response.json();
    expect(response.ok(), JSON.stringify(result)).toBe(true);
    return result;
  };
  const clusterUrl = `/ecs/clusters/${name}`;
  const serviceUrl = `${clusterUrl}/services/web`;
  try {
    await page.goto('/ecs/clusters');
    page.on('dialog', dialog => dialog.accept());
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    await page.getByRole('button', { name: 'Create Cluster', exact: true }).first().click();
    await page.getByRole('dialog').getByLabel('Cluster Name', { exact: true }).fill(name);
    const creation = page.waitForResponse(response => response.url().endsWith('/api/ecs/clusters/') && response.request().method() === 'POST');
    await page.getByRole('dialog').getByRole('button', { name: 'Create Cluster', exact: true }).click();
    const created = await creation;
    expect(created.ok(), await created.text()).toBe(true);
    clusterCreated = true;
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${clusterUrl}$`));
    // Seed a real definition; service creation and updates run through the UI.
    const registered = await post('/api/ecs/task-definitions/', {
      family: name, cpu: '256', memory: '512', network_mode: 'awsvpc', requires_compatibilities: ['FARGATE'],
      container_definitions: [{ name: 'web', image: 'nginx:latest', essential: true }],
    });
    definition = registered.task_definition_arn;
    expect(definition).toBeTruthy();
    vpc = (await post('/api/ec2/vpcs/', { cidr_block: '10.77.0.0/16', name })).vpc_id;
    subnet = (await post('/api/ec2/subnets/', { vpc_id: vpc, cidr_block: '10.77.1.0/24', name })).subnet_id;
    await page.getByRole('button', { name: 'Create service', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Service Name', { exact: true }).fill('web');
    await dialog.getByLabel('Subnet IDs', { exact: true }).fill(subnet);
    await dialog.getByLabel('Task Definition', { exact: true }).fill(definition);
    await dialog.getByLabel('Desired Tasks', { exact: true }).fill('0');
    const serviceResponse = page.waitForResponse(response => response.url().endsWith('/api/ecs/services/') && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Service', exact: true }).click();
    const response = await serviceResponse;
    const servicePayload = await response.json();
    expect(response.ok(), JSON.stringify(servicePayload)).toBe(true);
    expect(servicePayload.desired_count).toBe(0);
    serviceCreated = true;
    await expect(dialog).not.toBeVisible();
    await page.getByRole('button', { name: 'web', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${serviceUrl}$`));
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Service: web', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Update service', exact: true }).click();
    await page.getByRole('dialog').getByLabel('Desired Tasks Count', { exact: true }).fill('1');
    const update = page.waitForResponse(response => response.url().endsWith('/api/ecs/services/update/'));
    await page.getByRole('button', { name: 'Update Desired Count', exact: true }).click();
    const updated = await update;
    expect(updated.ok(), await updated.text()).toBe(true);
    await expect(page.getByRole('dialog')).not.toBeVisible();
    // In CI service containers without Docker daemon access, the emulator's service reconciler
    // does not spawn container tasks automatically. Seed the task directly so the UI workflow can proceed.
    execFileSync(python, ['-c', `import boto3\necs=boto3.client('ecs',endpoint_url='${flociEndpoint}',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')\necs.run_task(cluster='${name}',taskDefinition='${definition}',launchType='FARGATE',group='service:web',networkConfiguration={'awsvpcConfiguration':{'subnets':['${subnet}']}})`]);
    await expect.poll(async () => {
      const inventory = await (await page.request.get('/api/ecs/')).json();
      const cluster = inventory.clusters.find((item: any) => item.name === name);
      const tasks = (cluster?.tasks || []).filter((task: any) => task.group === 'service:web');
      for (const task of tasks) if (!taskArns.includes(task.taskArn)) taskArns.push(task.taskArn);
      return tasks.length;
    }, { timeout: 60_000, intervals: [1000, 2000, 5000] }).toBeGreaterThan(0);
    await page.reload();
    await page.getByRole('tab', { name: /^Tasks \(/ }).click();
    const taskId = taskArns[0].split('/').pop()!;
    await page.getByRole('button', { name: taskId, exact: true }).click();
    await expect(page.getByRole('heading', { name: `Task: ${taskId}`, exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Task configuration', exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/ecs-task-live.png', fullPage: true });
    await page.getByRole('link', { name: 'web', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Service: web', exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole('heading', { name: `Task: ${taskId}`, exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Stop task', exact: true }).click();
    await expect(page.getByText('Task stop requested.', { exact: true })).toBeVisible();
    // Scale down service so it does not replace stopped tasks.
    await post('/api/ecs/services/update/', { cluster: name, service: 'web', desired_count: 0 });
    await page.reload();
    await expect(page.getByRole('heading', { name: `Task: ${taskId}`, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Stop task', exact: true })).toBeDisabled();
    await page.goto(serviceUrl);
    await page.getByRole('button', { name: 'Delete service', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${clusterUrl}$`));
    serviceCreated = false;
    await expect.poll(async () => {
      const inventory = await (await page.request.get('/api/ecs/')).json();
      const cluster = inventory.clusters.find((item: any) => item.name === name);
      const tasks = cluster?.tasks || [];
      const runningTasks = tasks.filter((t: any) => t.lastStatus === 'RUNNING');
      const services = cluster?.services || [];
      const activeServices = services.filter((s: any) => s.status === 'ACTIVE');
      return runningTasks.length === 0 && activeServices.length === 0;
    }, { timeout: 30_000, intervals: [500, 1000] }).toBe(true);
    await page.getByRole('button', { name: 'Delete cluster', exact: true }).click();
    await expect(page).toHaveURL(/\/ecs\/clusters$/);
    clusterCreated = false;
  } finally {
    if (serviceCreated) {
      await post('/api/ecs/services/update/', { cluster: name, service: 'web', desired_count: 0 });
      await post('/api/ecs/services/delete/', { cluster: name, service: 'web', force: true });
    }
    if (clusterCreated) for (const task of taskArns) await post('/api/ecs/tasks/stop/', { cluster: name, task });
    if (clusterCreated) await post('/api/ecs/clusters/delete/', { cluster: name });
    if (subnet) { const response = await page.request.delete(`/api/ec2/subnets/${subnet}/`, { headers: { 'X-CSRFToken': csrf } }); expect(response.ok(), await response.text()).toBe(true); }
    if (vpc) { const response = await page.request.delete(`/api/ec2/vpcs/${vpc}/`, { headers: { 'X-CSRFToken': csrf } }); expect(response.ok(), await response.text()).toBe(true); }
    if (definition) {
      await post('/api/ecs/task-definitions/detail/', { task_definition: definition });
      const cleanup = await page.request.delete('/api/ecs/task-definitions/detail/', { headers: { 'X-CSRFToken': csrf }, data: { task_definitions: [definition] } });
      expect(cleanup.ok(), await cleanup.text()).toBe(true);
    }
  }
});

test('EKS live cluster and node group pages preserve details, navigation and cleanup', async ({ page }) => {
  test.setTimeout(240_000);
  const name = `ui-eks-${Date.now()}`;
  let csrf = '';
  let vpc = '';
  let subnet = '';
  let clusterCreated = false;
  let nodegroupCreated = false;
  const roles: string[] = [];
  const post = async (path: string, data: any) => {
    const response = await page.request.post(path, { headers: { 'X-CSRFToken': csrf }, data });
    const payload = await response.json();
    expect(response.ok(), JSON.stringify(payload)).toBe(true);
    return payload;
  };
  const remove = async (path: string) => {
    const response = await page.request.delete(path, { headers: { 'X-CSRFToken': csrf }, data: {} });
    expect(response.ok(), await response.text()).toBe(true);
  };
  try {
    await page.goto('/eks/clusters');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    vpc = (await post('/api/ec2/vpcs/', { cidr_block: '10.88.0.0/16', name })).vpc_id;
    subnet = (await post('/api/ec2/subnets/', { vpc_id: vpc, cidr_block: '10.88.1.0/24', name })).subnet_id;
    const roleArn = async (suffix: string, principal: string) => {
      const role = `${name}-${suffix}`;
      const result = await post('/api/iam/roles/', { role_name: role, trust_policy: { Version: '2012-10-17', Statement: [{ Effect: 'Allow', Principal: { Service: principal }, Action: 'sts:AssumeRole' }] } });
      roles.push(role);
      return result.arn;
    };
    const clusterRole = await roleArn('cluster', 'eks.amazonaws.com');
    const nodeRole = await roleArn('nodes', 'ec2.amazonaws.com');
    await page.getByRole('button', { name: 'Create cluster', exact: true }).first().click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel('Cluster Name', { exact: true }).fill(name);
    await dialog.getByLabel('Cluster Service IAM Role ARN', { exact: true }).fill(clusterRole);
    await dialog.getByLabel('VPC Subnet IDs (Comma separated)', { exact: true }).fill(subnet);
    await dialog.getByLabel('Security Group IDs (Comma separated)', { exact: true }).fill('');
    const creation = page.waitForResponse(response => response.url().endsWith('/api/eks/clusters/') && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Cluster', exact: true }).click();
    const response = await creation;
    const payload = await response.json();
    expect(response.ok(), JSON.stringify(payload)).toBe(true);
    clusterCreated = true;
    expect(payload.status).not.toBe('FAILED');
    await expect(dialog).not.toBeVisible();
    await expect.poll(async () => {
      const result = await (await page.request.get('/api/eks/')).json();
      return result.clusters.find((cluster: any) => cluster.name === name)?.status;
    }, { timeout: 120_000, intervals: [2000, 5000] }).toBe('ACTIVE');
    await page.getByRole('button', { name, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/eks/clusters/${name}$`));
    await page.reload();
    await expect(page.getByRole('heading', { name: `Cluster: ${name}`, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: vpc, exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Add node group', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('Node Group Name', { exact: true }).fill('workers');
    await dialog.getByLabel('Node Instance IAM Role ARN', { exact: true }).fill(nodeRole);
    await dialog.getByLabel('Subnet IDs (Comma separated)', { exact: true }).fill(subnet);
    await dialog.getByLabel('Min Size', { exact: true }).fill('0');
    await dialog.getByLabel('Desired Size', { exact: true }).fill('0');
    await dialog.getByLabel('Max Size', { exact: true }).fill('1');
    const nodeCreation = page.waitForResponse(response => response.url().endsWith(`/api/eks/clusters/${name}/nodegroups/`) && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Node Group', exact: true }).click();
    const nodeResponse = await nodeCreation;
    expect(nodeResponse.ok(), await nodeResponse.text()).toBe(true);
    nodegroupCreated = true;
    await expect(dialog).not.toBeVisible();
    await page.getByRole('tab', { name: 'Node Groups (1)', exact: true }).click();
    await page.getByRole('button', { name: 'workers', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Node group: workers', exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText(nodeRole, { exact: true }).first()).toBeVisible();
    // Assert the actual zero values returned by the emulator, not just successful creation.
    const state = await (await page.request.get('/api/eks/')).json();
    expect(state.nodegroups.find((node: any) => node.cluster_name === name && node.name === 'workers').scaling_config).toMatchObject({ minSize: 0, desiredSize: 0, maxSize: 1 });
    await page.screenshot({ path: 'test-results/screenshots/eks-nodegroup-live.png', fullPage: true });
    await page.getByRole('link', { name, exact: true }).click();
    await expect(page.getByRole('heading', { name: `Cluster: ${name}`, exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole('heading', { name: 'Node group: workers', exact: true })).toBeVisible();
    page.once('dialog', dialog => dialog.accept());
    await page.getByRole('button', { name: 'Delete node group', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/eks/clusters/${name}\\?tab=nodegroups$`));
    nodegroupCreated = false;
    await page.getByRole('button', { name: 'Delete cluster', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Confirm Delete', exact: true }).click();
    await expect(page).toHaveURL(/\/eks\/clusters$/);
    clusterCreated = false;
  } finally {
    const remaining = await (await page.request.get('/api/eks/')).json();
    if (nodegroupCreated || remaining.nodegroups?.some((node: any) => node.cluster_name === name && node.name === 'workers')) await remove(`/api/eks/clusters/${name}/nodegroups/workers/`);
    if (clusterCreated || remaining.clusters?.some((cluster: any) => cluster.name === name)) await post('/api/eks/clusters/delete/', { name });
    for (const role of roles) await remove(`/api/iam/roles/${role}/`);
    if (subnet) await remove(`/api/ec2/subnets/${subnet}/`);
    if (vpc) await remove(`/api/ec2/vpcs/${vpc}/`);
  }
});

test('EventBridge live pattern tester agrees with real event delivery and does not publish samples', async ({ page }) => {
  test.setTimeout(60_000);
  const name = `ui-routing-${Date.now()}`;
  let csrf = '';
  let bus = false, rule = false, queue = false, target = false;
  const post = async (path: string, data: any) => {
    const response = await page.request.post(path, { headers: { 'X-CSRFToken': csrf }, data });
    const payload = await response.json(); expect(response.ok(), JSON.stringify(payload)).toBe(true); return payload;
  };
  const messages = async () => (await (await page.request.get(`/api/sqs/queues/${name}/messages/`)).json()).messages;
  const pattern = { source: ['ecommerce.orders'], 'detail-type': ['OrderPlaced'], detail: { total: [75] } };
  try {
    await page.goto('/eventbridge/');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    await post('/api/eventbridge/buses/create/', { name }); bus = true;
    await post('/api/eventbridge/rules/put/', { name: 'orders', event_bus_name: name, event_pattern: pattern }); rule = true;
    await post('/api/sqs/queues/', { name }); queue = true;
    const attributes = await (await page.request.get(`/api/sqs/queues/${name}/attributes/`)).json();
    const arn = attributes.attributes.QueueArn;
    expect(arn).toBeTruthy();
    await post('/api/eventbridge/targets/put/', { rule_name: 'orders', event_bus_name: name, target_id: 'queue', arn }); target = true;
    await page.goto(`/eventbridge/tester?bus=${name}&rule=orders`);
    await expect(page.getByLabel('Pattern JSON', { exact: true })).toHaveValue(/75/);
    const sample = { version: '0', id: 'sample', account: '000000000000', time: '2026-10-20T12:00:00Z', region: 'us-east-1', source: 'ecommerce.orders', 'detail-type': 'OrderPlaced', resources: [], detail: { total: 75 } };
    await page.getByLabel('Sample event JSON', { exact: true }).fill(JSON.stringify(sample));
    await page.getByRole('button', { name: 'Test pattern', exact: true }).click();
    await expect(page.getByText('Pattern matches sample event', { exact: true })).toBeVisible();
    expect(await messages()).toHaveLength(0);
    await page.getByLabel('Sample event JSON', { exact: true }).fill(JSON.stringify({ ...sample, detail: { total: 10 } }));
    await page.getByRole('button', { name: 'Test pattern', exact: true }).click();
    await expect(page.getByText('Pattern does not match sample event', { exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/eventbridge-tester-live.png', fullPage: true });
    await post('/api/eventbridge/events/put/', { event_bus_name: name, source: sample.source, detail_type: sample['detail-type'], detail: { total: 75 } });
    await expect.poll(async () => (await messages()).length, { timeout: 10_000 }).toBe(1);
  } finally {
    if (target) await post('/api/eventbridge/targets/remove/', { rule_name: 'orders', event_bus_name: name, target_id: 'queue' });
    if (rule) await post('/api/eventbridge/rules/delete/', { name: 'orders', event_bus_name: name });
    if (bus) await post('/api/eventbridge/buses/delete/', { name });
    if (queue) { const response = await page.request.delete(`/api/sqs/queues/${name}/`, { headers: { 'X-CSRFToken': csrf } }); expect(response.ok(), await response.text()).toBe(true); }
  }
});

test('CloudFront live distribution behavior navigation preserves real configuration', async ({ page }) => {
  test.setTimeout(60_000);
  let id = '', csrf = '';
  const headers = () => ({ 'X-CSRFToken': csrf });
  try {
    await page.goto('/cloudfront/');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    await page.getByRole('button', { name: 'Create Distribution', exact: true }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Origin Domain Name', { exact: true }).fill('assets.localhost');
    await dialog.getByLabel('Origin ID', { exact: true }).fill('assets');
    const created = page.waitForResponse(response => response.url().endsWith('/api/cloudfront/distributions/') && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Distribution', exact: true }).click();
    const response = await created;
    const payload = await response.json();
    expect(response.ok(), JSON.stringify(payload)).toBe(true);
    id = payload.distribution.Id;
    await expect(dialog).not.toBeVisible();
    await page.goto(`/cloudfront/origins?distribution=${id}`);
    await expect(page.getByRole('cell', { name: 'Default (*)', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'assets', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Origin: assets', exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: `Distribution: ${id}`, exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/cloudfront-live.png', fullPage: true });
  } finally {
    if (id) {
      const disabled = await page.request.put(`/api/cloudfront/distributions/${id}/`, { headers: headers(), data: { enabled: false } });
      expect(disabled.ok(), await disabled.text()).toBe(true);
      const deleted = await page.request.delete(`/api/cloudfront/distributions/${id}/`, { headers: headers() });
      expect(deleted.ok(), await deleted.text()).toBe(true);
    }
  }
});

test('load balancing live listeners and scoped target health survive refresh', async ({ page }) => {
  test.setTimeout(90_000);
  const name = `ui-lb-${Date.now()}`;
  let csrf = '', vpc = '', subnet = '', subnet2 = '', lb = '', tg = '', listener = '';
  const post = async (path: string, data: any) => {
    const response = await page.request.post(path, { headers: { 'X-CSRFToken': csrf }, data });
    const payload = await response.json(); expect(response.ok(), JSON.stringify(payload)).toBe(true); return payload;
  };
  const remove = async (path: string) => { const response = await page.request.delete(path, { headers: { 'X-CSRFToken': csrf }, data: {} }); expect(response.ok(), await response.text()).toBe(true); };
  try {
    await page.goto('/elasticloadbalancing/');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    vpc = (await post('/api/ec2/vpcs/', { name, cidr_block: '10.99.0.0/16' })).vpc_id;
    subnet = (await post('/api/ec2/subnets/', { name, vpc_id: vpc, cidr_block: '10.99.1.0/24', availability_zone: 'us-east-1a' })).subnet_id;
    subnet2 = (await post('/api/ec2/subnets/', { name, vpc_id: vpc, cidr_block: '10.99.2.0/24', availability_zone: 'us-east-1b' })).subnet_id;
    await page.getByRole('button', { name: 'Create Load Balancer', exact: true }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Load Balancer Name', { exact: true }).fill(name);
    await dialog.getByLabel('Subnet IDs', { exact: true }).fill(`${subnet},${subnet2}`);
    const creating = page.waitForResponse(response => response.url().endsWith('/api/elasticloadbalancing/load-balancers/') && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Load Balancer', exact: true }).click();
    const response = await creating;
    const payload = await response.json(); expect(response.ok(), JSON.stringify(payload)).toBe(true);
    lb = payload.load_balancers[0].LoadBalancerArn;
    tg = (await post('/api/elasticloadbalancing/target-groups/', { name, protocol: 'HTTP', port: 8080, target_type: 'ip', vpc_id: vpc, health_check_path: '/ready' })).target_groups[0].TargetGroupArn;
    listener = (await post('/api/elasticloadbalancing/listeners/', { load_balancer_arn: lb, protocol: 'HTTP', port: 80, target_group_arn: tg })).listeners[0].ListenerArn;
    await post(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(tg)}/targets/`, { targets: [{ Id: '10.99.1.20', Port: 8080 }] });
    await page.goto(`/elasticloadbalancing/targets?${new URLSearchParams({ lb, tg })}`);
    await expect(page.getByRole('heading', { name: `Target group: ${name}`, exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: '10.99.1.20', exact: true }).first()).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: `Traffic routing: ${name}`, exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'View listeners', exact: true }).click();
    await expect(page.getByRole('cell', { name: listener, exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/elb-live.png', fullPage: true });
  } finally {
    if (listener) await remove(`/api/elasticloadbalancing/listeners/${encodeURIComponent(listener)}/`);
    if (tg) await remove(`/api/elasticloadbalancing/target-groups/${encodeURIComponent(tg)}/`);
    if (lb) await remove(`/api/elasticloadbalancing/load-balancers/${encodeURIComponent(lb)}/`);
    if (subnet2) await remove(`/api/ec2/subnets/${subnet2}/`);
    if (subnet) await remove(`/api/ec2/subnets/${subnet}/`);
    if (vpc) await remove(`/api/ec2/vpcs/${vpc}/`);
  }
});

test('S3 and Lambda live resource links preserve emulator resource selection', async ({ page }) => {
  test.setTimeout(90_000);
  const suffix = Date.now();
  const bucket = `ui-review-${suffix}`;
  const fn = `ui-worker-${suffix}`;
  const run = (body: string) => execFileSync(python, ['-c', `import boto3,io,zipfile\nendpoint='${flociEndpoint}'\ns3=boto3.client('s3',endpoint_url=endpoint,region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')\nlam=boto3.client('lambda',endpoint_url=endpoint,region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')\n${body}`], { timeout: 30_000 });
  try {
    run(`s3.create_bucket(Bucket='${bucket}')\nbuf=io.BytesIO()\nwith zipfile.ZipFile(buf,'w') as z: z.writestr('worker.py','def handler(event, context): return event')\nlam.create_function(FunctionName='${fn}',Runtime='python3.12',Handler='worker.handler',Role='arn:aws:iam::000000000000:role/lambda-basic-execution',Code={'ZipFile':buf.getvalue()},MemorySize=256,Timeout=12)`);
    await page.goto(`/s3/objects?bucket=${bucket}`);
    await expect(page.getByRole('heading', { name: `Bucket: ${bucket}`, exact: true })).toBeVisible();
    await page.getByRole('tab', { name: 'Properties & Hosting', exact: true }).click();
    await page.reload();
    await expect(page.getByRole('heading', { name: `Bucket: ${bucket}`, exact: true })).toBeVisible();
    await page.goto(`/lambda/code?function=${fn}`);
    await expect(page.getByRole('heading', { name: `Function: ${fn}`, exact: true })).toBeVisible();
    await expect(page.getByRole('table').first().getByText('256 MB / 12s', { exact: true })).toBeVisible();
    await page.getByRole('tab', { name: 'Monitor', exact: true }).click();
    await page.reload();
    await expect(page.getByRole('heading', { name: `Function: ${fn}`, exact: true })).toBeVisible();
  } finally {
    run(`try: lam.delete_function(FunctionName='${fn}')\nexcept lam.exceptions.ResourceNotFoundException: pass\ns3.delete_bucket(Bucket='${bucket}')`);
  }
});

test('SNS to SQS live delivery links preserve resource context and received messages', async ({ page }) => {
  test.setTimeout(60_000);
  const name = `ui-messaging-${Date.now()}`;
  let resources: { topic: string; queue: string } | undefined;
  const run = (body: string) => execFileSync(python, ['-c', `import boto3,json\noptions=dict(endpoint_url='${flociEndpoint}',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')\nsqs=boto3.client('sqs',**options)\nsns=boto3.client('sns',**options)\n${body}`], { timeout: 30_000 }).toString();
  try {
    resources = JSON.parse(run(`url=sqs.create_queue(QueueName='${name}')['QueueUrl']\narn=sqs.get_queue_attributes(QueueUrl=url,AttributeNames=['All'])['Attributes']['QueueArn']\ntopic=sns.create_topic(Name='${name}')['TopicArn']\nsqs.set_queue_attributes(QueueUrl=url,Attributes={'Policy':json.dumps({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'sns.amazonaws.com'},'Action':'sqs:SendMessage','Resource':arn,'Condition':{'ArnEquals':{'aws:SourceArn':topic}}}]})})\nsns.subscribe(TopicArn=topic,Protocol='sqs',Endpoint=arn,Attributes={'RawMessageDelivery':'true'})\nsns.publish(TopicArn=topic,Message='live-messaging-delivery')\nprint(json.dumps({'topic':topic,'queue':url,'arn':arn}))`));
    await page.goto(`/sns/subscriptions?${new URLSearchParams({ topic: resources!.topic })}`);
    await expect(page.getByRole('heading', { name: `Topic: ${name}`, exact: true })).toBeVisible();
    const destination = page.getByRole('link', { name: new RegExp(`arn:aws:sqs:.*:${name}$`) });
    await destination.click();
    await expect(page.getByRole('heading', { name: `Queue: ${name}`, exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: `Queue: ${name}`, exact: true })).toBeVisible();
    await page.getByRole('tab', { name: 'Send and receive messages', exact: true }).click();
    await page.getByRole('button', { name: 'Poll for messages', exact: true }).click();
    await expect(page.getByRole('cell', { name: 'live-messaging-delivery', exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/messaging-live.png', fullPage: true });
  } finally {
    if (resources) run(`sns.delete_topic(TopicArn='${resources.topic}')\nsqs.delete_queue(QueueUrl='${resources.queue}')`);
  }
});

async function dataWorkflowFixture(body: string) {
  return execFileSync(python, ['-c', `import boto3,json\noptions=dict(endpoint_url='${flociEndpoint}',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')\nddb=boto3.client('dynamodb',**options)\nsfn=boto3.client('stepfunctions',**options)\n${body}`], { timeout: 30_000 }).toString();
}

test('DynamoDB live composite schema, indexes, TTL and item links survive reload', async ({ page }) => {
  const name = `ui-table-${Date.now()}`;
  let created = false;
  try {
    await dataWorkflowFixture(`ddb.create_table(TableName='${name}',KeySchema=[{'AttributeName':'customerId','KeyType':'HASH'},{'AttributeName':'sequence','KeyType':'RANGE'}],AttributeDefinitions=[{'AttributeName':'customerId','AttributeType':'S'},{'AttributeName':'sequence','AttributeType':'N'},{'AttributeName':'status','AttributeType':'S'}],BillingMode='PAY_PER_REQUEST',GlobalSecondaryIndexes=[{'IndexName':'ByStatus','KeySchema':[{'AttributeName':'status','KeyType':'HASH'}],'Projection':{'ProjectionType':'KEYS_ONLY'}}])`); created = true;
    await dataWorkflowFixture(`ddb.update_time_to_live(TableName='${name}',TimeToLiveSpecification={'Enabled':True,'AttributeName':'expiresOn'})\nddb.put_item(TableName='${name}',Item={'customerId':{'S':'customer-live'},'sequence':{'N':'7'},'status':{'S':'SHIPPED'}})`);
    await page.goto(`/dynamodb/items?table=${name}`);
    await expect(page.getByRole('heading', { name: `Table: ${name}`, exact: true })).toBeVisible();
    await expect(page.getByText('customerId (String)', { exact: true })).toBeVisible();
    await expect(page.getByText('sequence (Number)', { exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'customer-live', exact: true })).toBeVisible();
    await page.getByRole('tab', { name: /Indexes/ }).click();
    await page.reload();
    await expect(page.getByRole('cell', { name: 'ByStatus', exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'KEYS_ONLY', exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/dynamodb-live.png', fullPage: true });
  } finally {
    if (created) await dataWorkflowFixture(`ddb.delete_table(TableName='${name}')`);
  }
});

test('Step Functions live nested execution history shows real state input and output', async ({ page }) => {
  const name = `ui-workflow-${Date.now()}`;
  let machine = '';
  try {
    machine = (await dataWorkflowFixture(`response=sfn.create_state_machine(name='${name}',roleArn='arn:aws:iam::000000000000:role/workflow',definition=json.dumps({'StartAt':'Validate','States':{'Validate':{'Type':'Pass','Result':{'verified':True},'End':True}}}))\nprint(response['stateMachineArn'])`)).trim();
    const execution = (await dataWorkflowFixture(`response=sfn.start_execution(stateMachineArn='${machine}',name='review-run',input=json.dumps({'orderId':'live-order'}))\nprint(response['executionArn'])`)).trim();
    await expect.poll(async () => (await dataWorkflowFixture(`print(sfn.describe_execution(executionArn='${execution}')['status'])`)).trim()).toBe('SUCCEEDED');
    await page.goto(`/stepfunctions/machines?${new URLSearchParams({ machine, panel: 'history' })}`);
    await expect(page.getByRole('cell', { name: 'review-run', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'History', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Execution details', exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'ExecutionSucceeded', exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Execution details', exact: true })).toBeVisible();
    await page.getByRole('button', { name: /Pass.*Validate/ }).click();
    await expect(page.getByRole('heading', { name: 'State input / output', exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/stepfunctions-live.png', fullPage: true });
  } finally {
    if (machine) await dataWorkflowFixture(`sfn.delete_state_machine(stateMachineArn='${machine}')`);
  }
});

test('Route 53 live zone and record creation, editing, reload, and cleanup', async ({ page }) => {
  test.setTimeout(120_000);
  const name = `ui-dns-${Date.now()}.test`;
  let zone = '';
  let csrf = '';
  try {
    await page.goto('/route53/');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    await page.getByRole('button', { name: 'Create Hosted Zone', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel('Domain Name', { exact: true }).fill(name);
    const created = page.waitForResponse(r => r.url().endsWith('/api/route53/hosted-zones/') && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Zone', exact: true }).click();
    const response = await created;
    const result = await response.json();
    expect(response.ok(), JSON.stringify(result)).toBe(true);
    zone = result.hosted_zone.Id.replace('/hostedzone/', '');
    await expect(dialog).not.toBeVisible();
    await expect(page).toHaveURL(new RegExp(`zone=${zone}`));
    await page.getByRole('button', { name: 'Create Record', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('Record Name', { exact: true }).fill('app.dev');
    await dialog.getByLabel('TTL (Seconds)', { exact: true }).fill('0');
    await dialog.getByLabel('Value', { exact: true }).fill('192.0.2.10');
    await dialog.getByRole('button', { name: 'Create Record', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByPlaceholder('Find records by name, value, or routing policy…').fill('app.dev');
    await expect(page.getByText(`app.dev.${name}.`, { exact: true })).toBeVisible();
    await page.locator('tr', { hasText: 'app.dev' }).getByRole('button', { name: /Edit/ }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('Value', { exact: true }).fill('192.0.2.11');
    await dialog.getByRole('button', { name: 'Save Record', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.reload();
    await expect(page.getByText('192.0.2.11', { exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/route53-live-review.png', fullPage: true });
  } finally {
    if (zone) {
      const inventory = await (await page.request.get('/api/route53/')).json();
      const found = inventory.hosted_zones?.find((z: any) => z.id?.endsWith(zone));
      for (const record of found?.records || []) if (!['SOA', 'NS'].includes(record.Type)) {
        const removed = await page.request.post(`/api/route53/hosted-zones/${zone}/record-sets/`, { headers: { 'X-CSRFToken': csrf }, data: { action: 'DELETE', record_set: record } });
        expect(removed.ok(), await removed.text()).toBe(true);
      }
      const removed = await page.request.delete(`/api/route53/hosted-zones/${zone}/`, { headers: { 'X-CSRFToken': csrf } });
      expect(removed.ok(), await removed.text()).toBe(true);
    }
  }
});

test('Cognito live pool, public app client, user, and authentication', async ({ page }) => {
  test.setTimeout(120_000);
  const name = `ui-cognito-${Date.now()}`;
  let pool = '';
  let csrf = '';
  try {
    await page.goto('/cognito/');
    csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content') || '';
    await page.getByRole('button', { name: 'Create User Pool', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel('User Pool Name', { exact: true }).fill(name);
    const created = page.waitForResponse(r => r.url().endsWith('/api/cognito/user-pools/') && r.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Create Pool', exact: true }).click();
    const response = await created;
    const result = await response.json();
    expect(response.ok(), JSON.stringify(result)).toBe(true);
    pool = result.user_pool?.Id || result.user_pool_id || result.pool?.Id;
    expect(pool, JSON.stringify(result)).toBeTruthy();
    await expect(dialog).not.toBeVisible();
    await page.getByPlaceholder('Find user pool by name or ID...').fill(name);
    await page.getByRole('button', { name, exact: true }).click();
    await page.getByRole('tab', { name: 'App Clients (0)', exact: true }).click();
    await page.getByRole('button', { name: 'Create App Client', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('App Client Name', { exact: true }).fill('Browser app');
    await dialog.getByRole('button', { name: 'Create Client', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole('button', { name: 'Browser app', exact: true }).click();
    await page.getByRole('tab', { name: 'Users (0)', exact: true }).click();
    await page.getByRole('button', { name: 'Create User', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('Username', { exact: true }).fill('review-alice');
    await dialog.getByLabel('Email Address', { exact: true }).fill('alice@example.test');
    await dialog.getByRole('button', { name: 'Create User', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByText('review-alice', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Set Password', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('New Permanent Password', { exact: true }).fill('ReviewPassword123!');
    await dialog.getByRole('button', { name: 'Save Password', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole('tab', { name: 'App Clients (1)', exact: true }).click();
    await page.getByRole('button', { name: 'Test selected client', exact: true }).click();
    await page.getByRole('textbox', { name: 'Username', exact: true }).fill('review-alice');
    await page.getByLabel('Password', { exact: true }).fill('ReviewPassword123!');
    await page.getByRole('button', { name: 'Authenticate User', exact: true }).click();
    await expect(page.getByText('Authentication succeeded. Tokens returned by the endpoint.', { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: `Selected pool: ${name}`, exact: true })).toBeVisible();
    await page.screenshot({ path: 'test-results/screenshots/cognito-live-review.png', fullPage: true });
  } finally {
    if (pool) {
      const removed = await page.request.delete(`/api/cognito/user-pools/${encodeURIComponent(pool)}/`, { headers: { 'X-CSRFToken': csrf } });
      expect(removed.ok(), await removed.text()).toBe(true);
    }
  }
});
