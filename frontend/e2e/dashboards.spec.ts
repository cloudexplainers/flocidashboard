import { test, expect, Page } from '@playwright/test';
import { FLAT_CURRICULUM_LABS } from '../src/labCurriculum';

const serviceKeys = 'acm,apigateway,appconfig,appsync,amazonmq,athena,autoscaling,backup,batch,bcmdataexports,bedrockruntime,cloudfront,cloudmap,cloudcontrol,cloudtrail,cloudformation,cloudwatch,codebuild,codepipeline,codedeploy,config,cognito,costexplorer,cur,dynamodb,docdb,memorydb,ec2,ecr,ecs,emr,eks,elasticache,elasticbeanstalk,elasticloadbalancing,eventbridge,firehose,glue,iam,iot,kafka,kinesis,kms,lambda,neptune,opensearch,pipes,pricing,rds,rdsdata,resourcegroupstagging,route53,s3,s3vectors,scheduler,secretsmanager,ses,sns,sqs,ssm,stepfunctions,textract,transcribe,transfer,wafv2'.split(',');
async function inventory(page: Page, key: string, data: unknown) {
  await page.route(`**/api/${key}/`, route => route.fulfill({ json: data }));
}
test.beforeEach(async ({ page }) => {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/services/' || path.startsWith('/api/labs/catalog/')) return route.continue();
    if (path === '/api/session-identity/') return route.fulfill({ json: { region: 'eu-west-1', endpoint_url: 'http://local-floci:4566', caller_identity: { Account: '123456789012', UserId: 'review-user', Arn: 'arn:aws:iam::123456789012:user/review-user' } } });
    if (path === '/api/settings/') return route.fulfill({ json: { service_auth: { iam_enforcement: false } } });
    if (path === '/api/health/') return route.fulfill({ json: { ok: false, error: 'Endpoint unavailable' } });
    return route.fulfill({ json: {} });
  });
});

for (const key of serviceKeys) {
  test(`${key} dashboard renders without runtime errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/${key}/`);
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /review-user @ 123456789012/ })).toBeVisible();
    await expect(page.getByText('Service Not Found', { exact: true })).toHaveCount(0);
    if (key === 'appconfig') await expect(page.getByRole('heading', { level: 1 }).first()).toContainText('AppConfig');
    if (key === 'appsync') await expect(page.getByRole('heading', { level: 1 }).first()).toContainText('AppSync');
    expect(errors).toEqual([]);
  });
}

test('home reports actual health, curriculum totals, endpoint, and recently visited services', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Unavailable', { exact: true })).toBeVisible();
  await expect(page.getByText('http://local-floci:4566', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('heading', { name: `${FLAT_CURRICULUM_LABS.length} Labs`, exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Recommended services', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Open S3', exact: true }).click();
  await page.getByRole('link', { name: 'floci', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Recently visited services', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'S3', exact: true }).first()).toBeVisible();
});

test('global search supports keyboard navigation and region opens real configuration', async ({ page }) => {
  await page.goto('/');
  const search = page.getByRole('combobox', { name: 'Search AWS services' });
  await search.fill('CloudWatch');
  await expect(page.getByRole('option', { name: /CloudWatch/ })).toBeVisible();
  await search.press('ArrowDown');
  await search.press('Enter');
  await expect(page).toHaveURL(/\/cloudwatch$/);
  await page.getByRole('button', { name: 'eu-west-1', exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
});

test('shared tables paginate, sort, clear selection, and apply column preferences', async ({ page }) => {
  await inventory(page, 'eks', { clusters: Array.from({ length: 26 }, (_, i) => ({ name: `cluster-${String(i).padStart(2, '0')}`, status: i === 0 ? 'FAILED' : 'ACTIVE', version: '1.30' })) });
  await page.goto('/eks/');
  const table = page.getByRole('table').first();
  await expect(table.getByRole('row')).toHaveCount(11);
  await page.getByRole('button', { name: 'Next page', exact: true }).first().click();
  await expect(table.getByText('cluster-10', { exact: true })).toBeVisible();
  await expect(table.getByText('cluster-00', { exact: true })).toHaveCount(0);
  await table.getByRole('columnheader', { name: /Cluster name/ }).click();
  await expect(table.getByText('cluster-00', { exact: true })).toBeVisible();
  await table.getByRole('columnheader', { name: /Cluster name/ }).click();
  await expect(table.getByText('cluster-25', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Table preferences', exact: true }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('25 resources', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(table.getByRole('row')).toHaveCount(26);
});

test('generic workbench leads with inventory and displays resource details and retrieval errors', async ({ page }) => {
  await inventory(page, 'acm', { certificates: [{ name: 'example.test', status: 'ISSUED', arn: 'arn:certificate:1' }] });
  await page.goto('/acm/');
  await expect(page.getByRole('heading', { name: 'Resource inventory', exact: true })).toBeVisible();
  await page.getByRole('radio').first().check();
  await expect(page.getByRole('heading', { name: 'Resource details', exact: true })).toBeVisible();
  await inventory(page, 'acm', { error: 'Inventory unavailable for this identity' });
  await page.getByRole('button', { name: 'Refresh resources', exact: true }).click();
  await expect(page.getByText('Inventory unavailable for this identity', { exact: true })).toBeVisible();
});

test('CloudWatch charts returned samples, retains dimensions, and handles empty and failed time windows', async ({ page }) => {
  await inventory(page, 'cloudwatch', { metrics: [{ Namespace: 'Test/App', MetricName: 'Latency', Dimensions: [{ Name: 'Service', Value: 'worker' }] }], alarms: [{ AlarmName: 'Latency alarm', StateValue: 'ALARM', MetricName: 'Latency', Namespace: 'Test/App' }] });
  const requests: URL[] = [];
  await page.route('**/api/cloudwatch/metric-statistics/**', route => {
    const url = new URL(route.request().url()); requests.push(url);
    if (url.searchParams.get('hours') === '3') return route.fulfill({ json: { datapoints: [] } });
    if (url.searchParams.get('hours') === '12') return route.fulfill({ status: 502, json: { error: 'Metric endpoint unavailable' } });
    return route.fulfill({ json: { datapoints: [{ Timestamp: '2026-10-20T10:00:00Z', Average: 12, Unit: 'Milliseconds' }, { Timestamp: '2026-10-20T10:01:00Z', Average: 20, Unit: 'Milliseconds' }] } });
  });
  await page.goto('/cloudwatch/');
  await expect(page.getByText('Latency alarm', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Metrics', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Metric monitoring', exact: true })).toBeVisible();
  await expect(page.getByText('Milliseconds', { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/cloudwatch.png', fullPage: true });
  expect(JSON.parse(requests[0].searchParams.get('dimensions')!)).toEqual([{ Name: 'Service', Value: 'worker' }]);
  await page.getByRole('button', { name: 'Time range' }).click();
  await page.getByRole('option', { name: 'Last 3 hours', exact: true }).click();
  await expect(page.getByText(/No metric samples in this time range/)).toBeVisible();
  await page.getByRole('button', { name: 'Time range' }).click();
  await page.getByRole('option', { name: 'Last 12 hours', exact: true }).click();
  await expect(page.getByText('Metric endpoint unavailable', { exact: true })).toBeVisible();
});

test('Step Functions draws real Choice and Parallel transitions and exposes nested states', async ({ page }) => {
  const definition = { StartAt: 'Decide', States: { Decide: { Type: 'Choice', Choices: [{ Variable: '$.ok', BooleanEquals: true, Next: 'FanOut' }], Default: 'Rejected' }, Rejected: { Type: 'Fail' }, FanOut: { Type: 'Parallel', Branches: [{ StartAt: 'Worker', States: { Worker: { Type: 'Pass', End: true } } }], End: true } } };
  await inventory(page, 'stepfunctions', { state_machines: [{ name: 'branching-workflow', definition, stateMachineArn: 'arn:aws:states:eu-west-1:123456789012:stateMachine:branching-workflow' }] });
  await page.goto('/stepfunctions/');
  await page.getByRole('tab', { name: 'Visual State Map', exact: true }).click();
  await expect(page.getByRole('img', { name: 'State machine transitions' })).toBeVisible();
  await expect(page.locator('svg[role="img"]:visible title').filter({ hasText: 'Decide → FanOut (Choice 1)' })).toHaveCount(1);
  await expect(page.locator('svg[role="img"]:visible title').filter({ hasText: 'Decide → Rejected (Default)' })).toHaveCount(1);
  await expect(page.locator('svg[role="img"]:visible title').filter({ hasText: 'Rejected → FanOut' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Parallel · End FanOut', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Parallel branch 1', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start · Pass · End Worker', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/stepfunctions.png', fullPage: true });
});

test('EC2 uses actual lower-case inventory values and never invents network resources', async ({ page }) => {
  await inventory(page, 'ec2', { instances: [{ id: 'i-review', instance_type: 'm5.large', state: 'stopped', private_ip: '10.2.0.8', vpc_id: 'vpc-review', subnet_id: 'subnet-review', security_groups: [{ GroupId: 'sg-review', GroupName: 'review' }] }] });
  await page.goto('/ec2/');
  await expect(page.getByRole('heading', { name: 'Resource summary', exact: true })).toBeVisible();
  await expect(page.getByRole('table').first().getByText('m5.large', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Networking', exact: true }).click();
  await expect(page.getByLabel('Instance network path')).toContainText('vpc-review');
  await expect(page.getByText('54.210.12.34', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: /VPCs & Subnets/ }).click();
  await expect(page.getByText('vpc-0a1b2c3d4e5f', { exact: true })).toHaveCount(0);
});

test('Lambda overview and monitor use actual function metadata and filter logs', async ({ page }) => {
  await inventory(page, 'lambda', { functions: [{ name: 'review-worker', arn: 'arn:aws:lambda:eu-west-1:123456789012:function:review-worker', runtime: 'nodejs22.x', handler: 'worker.handler', configuration: { CodeSize: 123 } }] });
  await page.route('**/api/inspector/lambda/log-events/**', route => route.fulfill({ json: { events: [{ timestamp: 1000, message: 'processed order' }, { timestamp: 2000, message: 'heartbeat' }] } }));
  await page.goto('/lambda/');
  await expect(page.getByRole('heading', { name: 'Function overview', exact: true })).toBeVisible();
  await expect(page.getByRole('table').first().getByText('nodejs22.x', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Monitor', exact: true }).click();
  await expect(page.getByText('processed order', { exact: true })).toBeVisible();
  await page.getByPlaceholder('Find function log events').fill('order');
  await expect(page.getByText('heartbeat', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/screenshots/lambda.png', fullPage: true });
  await expect(page).toHaveURL(/\/lambda\/monitor$/);
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Monitor', exact: true })).toHaveAttribute('aria-selected', 'true');
});

test('inventory failures show an actionable error instead of silently appearing empty', async ({ page }) => {
  await page.route('**/api/ec2/', route => route.fulfill({ status: 502, json: { error: 'Endpoint offline' } }));
  await page.goto('/ec2/');
  await expect(page.getByText(/Unable to load ec2 resources/)).toBeVisible();
});

for (const key of ['home', 'topology', 'inspector', 'settings', 'environment', 'activity', 'console', 'labs']) {
  test(`${key} utility dashboard renders without runtime errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/${key}/`);
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
    await expect(page.getByText('Service Not Found', { exact: true })).toHaveCount(0);
    if (key === 'appconfig') await expect(page.getByRole('heading', { level: 1 }).first()).toContainText('AppConfig');
    if (key === 'appsync') await expect(page.getByRole('heading', { level: 1 }).first()).toContainText('AppSync');
    expect(errors).toEqual([]);
  });
}

test('home and service overview remain usable on a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'AWS Management Console', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/home-mobile.png', fullPage: true });
  await inventory(page, 'lambda', { functions: [{ name: 'mobile-worker', runtime: 'python3.12', handler: 'worker.handler' }] });
  await page.goto('/lambda/');
  await expect(page.getByRole('heading', { name: 'Function overview', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Manage triggers', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/lambda-mobile.png', fullPage: true });
});

test('session switch updates global caller identity without a reload', async ({ page }) => {
  let switched = false;
  await page.route('**/api/session-identity/', route => route.fulfill({ json: {
    region: 'eu-west-1', endpoint_url: 'http://local-floci:4566',
    caller_identity: { Account: '123456789012', UserId: switched ? 'switched-user' : 'review-user', Arn: `arn:aws:iam::123456789012:user/${switched ? 'switched-user' : 'review-user'}` },
  } }));
  await page.route('**/api/session-identity/use-user/', route => { switched = true; return route.fulfill({ json: { changed: true } }); });
  await page.goto('/environment/');
  await page.getByRole('tab', { name: 'Switch to IAM User', exact: true }).click();
  await page.getByLabel('IAM User Name', { exact: true }).fill('switched-user');
  await page.getByRole('button', { name: /Switch.*User/i }).click();
  await expect(page.getByRole('button', { name: /switched-user @ 123456789012/ })).toBeVisible();
});

test('DynamoDB item explorer renders attributes as columns and exposes full documents', async ({ page }) => {
  await inventory(page, 'dynamodb', { tables: [{ TableName: 'Orders', PartitionKey: 'orderId' }] });
  await page.route('**/api/dynamodb/tables/Orders/scan/**', route => route.fulfill({ json: { items: [{ orderId: 'order-123', status: 'SHIPPED', total: 42, details: { destination: 'NY' } }] } }));
  await page.goto('/dynamodb/');
  const items = page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: 'status', exact: true }) });
  await expect(items.getByText('SHIPPED', { exact: true })).toBeVisible();
  await expect(items.getByRole('columnheader', { name: 'total', exact: true })).toBeVisible();
  await items.getByRole('radio').check();
  await expect(page.getByRole('heading', { name: 'Selected item document', exact: true })).toBeVisible();
});

test('AppSync sends the query to the backend and displays returned data', async ({ page }) => {
  await inventory(page, 'appsync', { graphql_apis: [{ apiId: 'api-review', name: 'Review API' }] });
  let submitted: any;
  await page.route('**/api/appsync/graphql/run/', route => { submitted = route.request().postDataJSON(); return route.fulfill({ json: { data: { listPosts: [{ id: 'real-post', title: 'Returned by endpoint' }] } } }); });
  await page.goto('/appsync/');
  await page.getByRole('tab', { name: 'GraphQL Query Sandbox', exact: true }).click();
  await page.getByRole('button', { name: 'Execute Query', exact: true }).click();
  await expect(page.getByRole('textbox').last()).toHaveValue(/Returned by endpoint/);
  expect(submitted.api_id).toBe('api-review');
  expect(submitted.query).toBeTruthy();
  expect(submitted.variables).toEqual({ limit: 5 });
});

test('ECS cluster summary reconciles desired, running and pending tasks', async ({ page }) => {
  await inventory(page, 'ecs', { clusters: [{ clusterName: 'review-cluster' }], services: [{ clusterArn: 'arn:aws:ecs:eu-west-1:123456789012:cluster/review-cluster', serviceName: 'worker', desiredCount: 3, runningCount: 2, pendingCount: 1 }] });
  await page.goto('/ecs/');
  const summary = page.getByTestId('ecs-cluster-summary');
  await expect(summary.getByText('Desired tasks', { exact: true })).toBeVisible();
  await expect(summary.getByText('3', { exact: true })).toBeVisible();
  await expect(summary.getByText('2', { exact: true })).toBeVisible();
  await expect(summary.getByText('1', { exact: true })).toBeVisible();
});

test('Athena reads backend result envelopes and preserves empty results', async ({ page }) => {
  await page.route('**/api/athena/queries/start/', route => route.fulfill({ json: { query_execution_id: 'query-review' } }));
  await page.route('**/api/athena/queries/detail/', route => route.fulfill({ json: { query_execution: { Status: { State: 'SUCCEEDED' }, Statistics: { DataScannedInBytes: 0 } } } }));
  await page.route('**/api/athena/queries/results/', route => route.fulfill({ json: { result_set: { Rows: [] } } }));
  await page.goto('/athena/');
  await page.getByRole('button', { name: 'Run Query', exact: true }).click();
  await expect(page.getByText('Status: SUCCEEDED', { exact: true })).toBeVisible();
  await expect(page.getByText('No data returned by query.', { exact: true })).toBeVisible();
  await expect(page.getByText('UserSignUp', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Data Scanned: 0.0 KB/)).toBeVisible();
});

test('EC2 focuses network resources, links security groups, and clears removed selections on refresh', async ({ page }) => {
  let removed = false;
  await page.route('**/api/ec2/', route => route.fulfill({ json: {
    instances: removed ? [] : [{ id: 'i-focused', state: 'running', tags: [{ Key: 'Name', Value: 'worker' }], security_groups: [{ GroupId: 'sg-focused', GroupName: 'web' }] }],
    vpcs: [{ id: 'vpc-focused', cidr_block: '10.0.0.0/16' }], subnets: [{ id: 'subnet-focused', vpc_id: 'vpc-focused', cidr_block: '10.0.1.0/24' }],
    security_groups: [{ GroupId: 'sg-focused', GroupName: 'web', VpcId: 'vpc-focused', IpPermissions: [], IpPermissionsEgress: [] }],
  } }));
  await page.goto('/ec2/');
  await page.getByRole('tab', { name: 'Security', exact: true }).click();
  await page.getByRole('button', { name: 'sg-focused', exact: true }).click();
  await expect(page.getByRole('tab', { name: /Security Groups/ })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: /VPCs & Subnets/ }).click();
  await expect(page.getByRole('heading', { name: /Virtual Private Clouds/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Subnets (1)', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /^Network resource type/ }).click();
  await page.getByRole('option', { name: 'Subnets', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Subnets (1)', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/ec2-network.png', fullPage: true });
  await page.getByRole('tab', { name: 'Instances (1)', exact: true }).click();
  removed = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('heading', { name: /Instance: i-focused/ })).toHaveCount(0);
  await expect(page.getByText('No instances found.', { exact: true })).toBeVisible();
});

test('CloudWatch alarm drill-down retains the selected metric dimensions', async ({ page }) => {
  const dimensions = [{ Name: 'Service', Value: 'payments' }];
  await inventory(page, 'cloudwatch', { metrics: [{ Namespace: 'Other', MetricName: 'Count' }], alarms: [{ AlarmName: 'Payment latency', Namespace: 'App', MetricName: 'Latency', Dimensions: dimensions, StateValue: 'ALARM' }] });
  const requests: URL[] = [];
  await page.route('**/api/cloudwatch/metric-statistics/**', route => { requests.push(new URL(route.request().url())); return route.fulfill({ json: { datapoints: [] } }); });
  await page.goto('/cloudwatch/');
  await page.getByRole('button', { name: 'Payment latency', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Metrics', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect.poll(() => requests.some(url => url.searchParams.get('namespace') === 'App' && JSON.stringify(JSON.parse(url.searchParams.get('dimensions') || '[]')) === JSON.stringify(dimensions))).toBe(true);
  await expect(page.getByRole('button', { name: /^Metric / })).toContainText('App / Latency');
  await expect(page.getByRole('button', { name: 'Publish Metric Data', exact: true })).not.toBeVisible();
});

test('CloudWatch loads stream-specific events, filters them, and exposes retrieval failures', async ({ page }) => {
  await page.route('**/api/inspector/lambda/log-groups/**', route => route.fulfill({ json: { log_groups: [{ logGroupName: '/app/worker' }, { logGroupName: '/app/empty' }] } }));
  await page.route('**/api/cloudwatch/log-streams/', route => route.fulfill({ json: { streams: route.request().postDataJSON().log_group_name === '/app/empty' ? [] : [{ logStreamName: 'stream-a' }, { logStreamName: 'stream-b' }] } }));
  let fail = false;
  const eventRequests: any[] = [];
  await page.route('**/api/cloudwatch/log-events/', route => {
    const body = route.request().postDataJSON(); eventRequests.push(body);
    return fail ? route.fulfill({ status: 502, json: { error: 'Stream is unavailable' } }) : route.fulfill({ json: { events: [{ timestamp: 1, message: `${body.log_stream_name}: order processed` }, { timestamp: 2, message: 'heartbeat' }] } });
  });
  await page.goto('/cloudwatch/logs');
  await expect(page.getByText('stream-a: order processed', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^Log stream/ }).click();
  await page.getByRole('option', { name: 'stream-b', exact: true }).click();
  await expect(page.getByText('stream-b: order processed', { exact: true })).toBeVisible();
  expect(eventRequests.at(-1)).toMatchObject({ log_group_name: '/app/worker', log_stream_name: 'stream-b' });
  await page.getByPlaceholder('Filter log messages', { exact: true }).fill('order');
  await expect(page.getByText('heartbeat', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/screenshots/cloudwatch-streams.png', fullPage: true });
  fail = true;
  await page.getByRole('button', { name: 'Refresh logs', exact: true }).click();
  await expect(page.getByText('Stream is unavailable', { exact: true })).toBeVisible();
});

const gatewayInventory = {
  rest_apis: [{ id: 'rest-review', name: 'Orders REST', resources: [{ id: 'resource-orders', path: '/orders', resourceMethods: { GET: { methodIntegration: { type: 'AWS_PROXY', uri: 'arn:aws:lambda:eu-west-1:123456789012:function:orders' } } } }], stages: [], authorizers: [] }],
  http_apis: [{ id: 'http-review', name: 'Orders HTTP', protocol_type: 'HTTP', routes: [{ RouteId: 'route-orders', RouteKey: 'GET /orders', Target: 'integrations/orders' }], integrations: [{ IntegrationId: 'orders', IntegrationType: 'AWS_PROXY', IntegrationUri: 'arn:orders' }], stages: [{ StageName: '$default', AutoDeploy: true }], authorizers: [] }],
};

test('API Gateway uses real REST and HTTP inventories and never invents deployments', async ({ page }) => {
  await inventory(page, 'apigateway', gatewayInventory);
  await page.goto('/apigateway/');
  await expect(page.getByRole('tab', { name: 'Resources and methods', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '/orders', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Stages (0)', exact: true }).click();
  await expect(page.getByText('No stages deployed.', { exact: true })).toBeVisible();
  await expect(page.getByText('d-live', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Test request', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Send request', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Orders HTTP', exact: true }).click();
  await page.getByRole('tab', { name: 'Routes', exact: true }).click();
  await expect(page.getByRole('button', { name: 'GET /orders', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Integrations', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'arn:orders', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/apigateway.png', fullPage: true });
});

test('API Gateway route tests and create/delete send the backend contract', async ({ page }) => {
  await inventory(page, 'apigateway', gatewayInventory);
  let testPayload: any; let createPayload: any; let deletePayload: any;
  await page.route('**/api/apigateway/requests/test/', route => { testPayload = route.request().postDataJSON(); return route.fulfill({ json: { status_code: 200, body: 'real response' } }); });
  await page.route('**/api/apigateway/apis/create/', route => { createPayload = route.request().postDataJSON(); return route.fulfill({ json: { api_id: 'created' } }); });
  await page.route('**/api/apigateway/apis/delete/', route => { deletePayload = route.request().postDataJSON(); return route.fulfill({ json: { deleted: true } }); });
  await page.goto('/apigateway/');
  await page.getByRole('button', { name: 'Orders HTTP', exact: true }).click();
  await page.getByRole('button', { name: 'GET /orders', exact: true }).click();
  await page.getByRole('button', { name: 'Send request', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Response · HTTP 200', exact: true })).toBeVisible();
  expect(testPayload).toMatchObject({ api_type: 'http', api_id: 'http-review', method: 'GET', path: '/orders', stage: '$default' });
  await page.getByRole('button', { name: 'Delete API', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('Orders HTTP (http-review)');
  expect(deletePayload).toBeUndefined();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect.poll(() => deletePayload).toMatchObject({ api_id: 'http-review', api_type: 'http' });
  await page.getByRole('button', { name: 'Create API', exact: true }).click();
  await page.getByRole('dialog').getByLabel('API name', { exact: true }).fill('New HTTP');
  await page.getByRole('dialog').getByRole('button', { name: 'Create API', exact: true }).click();
  await expect.poll(() => createPayload).toMatchObject({ name: 'New HTTP', api_type: 'http' });
});

test('generic workbench opens one task, prefills resource fields, and clears stale resource context', async ({ page }) => {
  await inventory(page, 'acm', { certificates: [{ name: 'example.test', certificate: 'certificate-review', status: 'ISSUED' }] });
  await page.route('**/api/acm/certificates/certificate-review/', route => route.fulfill({ json: { Certificate: 'returned-certificate' } }));
  await page.goto('/acm/');
  await expect(page.getByRole('button', { name: /^Execute / })).toHaveCount(0);
  await page.getByRole('radio').first().check();
  await page.getByRole('button', { name: /^Operation / }).click();
  await page.getByRole('option', { name: 'Get certificate PEM', exact: true }).click();
  await expect(page.getByLabel('certificate', { exact: true })).toHaveValue('certificate-review');
  await expect(page.getByRole('button', { name: /^Execute / })).toHaveCount(1);
  await page.getByRole('button', { name: 'Execute Get certificate PEM', exact: true }).click();
  await expect(page.getByText('Operation Succeeded', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Response details', exact: true }).click();
  await expect(page.getByText(/returned-certificate/)).toBeVisible();
  await expect(page.getByLabel('certificate', { exact: true })).toHaveValue('');
  await page.screenshot({ path: 'test-results/screenshots/generic-operation.png', fullPage: true });
});

test('resource details preserve returned SQS and DynamoDB ARNs across Regions', async ({ page }) => {
  const queueArn = 'arn:aws:sqs:eu-west-1:123456789012:Orders';
  await inventory(page, 'sqs', { queues: [{ name: 'Orders', url: 'http://local/Orders', attributes: { QueueArn: queueArn, VisibilityTimeout: '0' } }] });
  await page.goto('/sqs/');
  await page.getByRole('tab', { name: 'Queue attributes & configuration', exact: true }).click();
  await expect(page.getByText(queueArn, { exact: true })).toBeVisible();
  await expect(page.getByText('0 seconds', { exact: true }).first()).toBeVisible();
  await page.getByRole('tab', { name: 'Access policy & permissions', exact: true }).click();
  await expect(page.locator('textarea').filter({ hasText: 'No access policy reported for this queue.' })).toBeVisible();
  const tableArn = 'arn:aws:dynamodb:eu-west-1:123456789012:table/Orders';
  await inventory(page, 'dynamodb', { tables: [{ TableName: 'Orders', TableArn: tableArn, PartitionKey: 'orderId' }] });
  await page.goto('/dynamodb/');
  await page.getByRole('tab', { name: 'Table Overview', exact: true }).click();
  await expect(page.getByText(tableArn, { exact: true })).toBeVisible();
});

test('topology nodes support keyboard selection on light surfaces', async ({ page }) => {
  await page.route('**/api/resource-graph/**', route => route.fulfill({ json: { layers: ['compute'], nodes: [
    { id: 'node-1', name: 'First function', service: 'lambda', kind: 'function', layer: 'compute', status: 'healthy' },
    { id: 'node-2', name: 'Second function', service: 'lambda', kind: 'function', layer: 'compute', status: 'healthy' },
  ], edges: [], summary: {} } }));
  await page.goto('/topology/');
  const node = page.getByRole('button', { name: /Second function function/ });
  await node.focus();
  await page.keyboard.press('Enter');
  await expect(node).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('heading', { name: 'Selected Node: Second function', exact: true })).toBeVisible();
  await expect(node).toHaveCSS('background-color', 'rgb(241, 250, 255)');
  await page.screenshot({ path: 'test-results/screenshots/topology.png', fullPage: true });
});

test('API Gateway and generic task forms remain usable on narrow screens', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await inventory(page, 'apigateway', gatewayInventory);
  await page.goto('/apigateway/');
  await page.getByRole('button', { name: 'Orders HTTP', exact: true }).click();
  await page.getByRole('button', { name: 'GET /orders', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Send request', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/apigateway-mobile.png', fullPage: true });
  await page.goto('/acm/');
  await page.getByRole('button', { name: /^Operation / }).click();
  await page.getByRole('option', { name: 'Request certificate', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Execute Request certificate', exact: true })).toBeDisabled();
  await page.getByLabel('Domain name', { exact: true }).fill('example.test');
  await expect(page.getByRole('button', { name: 'Execute Request certificate', exact: true })).toBeEnabled();
  await page.screenshot({ path: 'test-results/screenshots/generic-mobile.png', fullPage: true });
});
