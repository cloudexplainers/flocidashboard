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
  await expect(page).toHaveURL(/s3/);
  await page.getByRole('link', { name: 'home', exact: true }).click();
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

test('top navigation includes Learn Cloud Computing link next to home', async ({ page }) => {
  await page.goto('/');
  const homeLink = page.getByRole('link', { name: 'home', exact: true });
  const learnLink = page.getByRole('link', { name: 'Learn Cloud Computing', exact: true });

  await expect(homeLink).toBeVisible();
  await expect(learnLink).toBeVisible();
  await expect(learnLink).toHaveAttribute('href', 'https://www.youtube.com/@cloudexplainers');
  await expect(learnLink).toHaveAttribute('target', '_blank');

  const homeBox = await homeLink.boundingBox();
  const learnBox = await learnLink.boundingBox();
  expect(homeBox).not.toBeNull();
  expect(learnBox).not.toBeNull();
  expect(learnBox!.x).toBeGreaterThan(homeBox!.x);

  const homeStyle = await homeLink.locator('span').evaluate(el => {
    const s = window.getComputedStyle(el);
    return { fontSize: s.fontSize, fontFamily: s.fontFamily, fontWeight: s.fontWeight };
  });
  const learnStyle = await learnLink.evaluate(el => {
    const s = window.getComputedStyle(el);
    return { fontSize: s.fontSize, fontFamily: s.fontFamily, fontWeight: s.fontWeight };
  });

  expect(learnStyle.fontSize).toBe(homeStyle.fontSize);
  expect(learnStyle.fontFamily).toBe(homeStyle.fontFamily);

  await page.screenshot({ path: 'test-results/screenshots/top-nav-desktop.png' });
  await page.setViewportSize({ width: 390, height: 800 });
  await page.screenshot({ path: 'test-results/screenshots/top-nav-mobile.png' });
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
  await expect(page).toHaveURL(/\/lambda\/monitor(?:\?function=review-worker)?$/);
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
  await page.getByRole('tab', { name: 'IAM user', exact: true }).click();
  await page.getByRole('combobox', { name: 'IAM user name', exact: true }).fill('switched-user');
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
  await expect(page.getByRole('heading', { name: 'Alarm details', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View metric', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Metrics', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect.poll(() => requests.some(url => url.searchParams.get('namespace') === 'App' && JSON.stringify(JSON.parse(url.searchParams.get('dimensions') || '[]')) === JSON.stringify(dimensions))).toBe(true);
  await expect(page.getByRole('button', { name: /^Metric / })).toContainText('App / Latency');
  await expect(page.getByRole('button', { name: 'Publish Metric Data', exact: true })).not.toBeVisible();
});

test('CloudWatch loads stream-specific events, filters them, and exposes retrieval failures', async ({ page }) => {
  await inventory(page, 'cloudwatch', { log_groups: [{ name: '/app/worker' }, { name: '/app/empty' }] });
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

test('generic inventory exposes readable details and contextual actions', async ({ page }) => {
  await inventory(page, 'acm', { certificates: [{ name: 'example.test', certificate: 'review-cert', status: 'FAILED' }] });
  await page.goto('/acm/');
  await expect(page.getByRole('button', { name: 'Delete resource', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'example.test', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Resource details', exact: true })).toBeVisible();
  await expect(page.getByText('FAILED', { exact: true })).toHaveCount(2);
  await expect(page.getByRole('button', { name: 'Raw resource JSON', exact: true })).toHaveAttribute('aria-expanded', 'false');
  await page.getByRole('button', { name: 'Delete resource', exact: true }).click();
  await expect(page.getByLabel('certificate', { exact: true })).toHaveValue('review-cert');
  await page.screenshot({ path: 'test-results/screenshots/resource-details.png', fullPage: true });
});

test('EKS resource links open overview and preserve failed node group states', async ({ page }) => {
  await inventory(page, 'eks', { clusters: [{ name: 'failed-cluster', status: 'FAILED', nodegroups: [{ nodegroupName: 'workers', status: 'CREATE_FAILED' }] }] });
  await page.goto('/eks/');
  await page.getByRole('button', { name: 'failed-cluster', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Overview & Config', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Not reported', { exact: true }).first()).toBeVisible();
  await page.getByRole('tab', { name: 'Node Groups (1)', exact: true }).click();
  await expect(page.getByText('CREATE_FAILED', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/eks-resource.png', fullPage: true });
});

test('ECS cluster shortcuts exclude similarly named clusters', async ({ page }) => {
  await inventory(page, 'ecs', { clusters: [{ clusterName: 'app' }], services: [
    { clusterArn: 'arn:aws:ecs:eu-west-1:123456789012:cluster/app', serviceName: 'worker', desiredCount: 1 },
    { clusterArn: 'arn:aws:ecs:eu-west-1:123456789012:cluster/app-other', serviceName: 'unrelated', desiredCount: 99 },
  ] });
  await page.goto('/ecs/');
  await page.getByRole('button', { name: 'View services', exact: true }).click();
  await expect(page.getByText('worker', { exact: true })).toBeVisible();
  await expect(page.getByText('unrelated', { exact: true })).toHaveCount(0);
});

test('CloudFront routing uses returned configuration without invented defaults', async ({ page }) => {
  await inventory(page, 'cloudfront', { distributions: [{ Id: 'D-REVIEW', DomainName: 'cdn.example.test', Origins: { Items: [{ Id: 'origin-a', DomainName: 'assets.example.test' }] }, DefaultCacheBehavior: { TargetOriginId: 'origin-a', Compress: false, AllowedMethods: { Items: ['GET', 'HEAD'] } } }] });
  await page.goto('/cloudfront/');
  await expect(page.getByRole('heading', { name: 'Distribution: D-REVIEW', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View origins and behaviors', exact: true }).click();
  await expect(page.getByText('Allowed HTTP Methods: GET, HEAD', { exact: true })).toBeVisible();
  await expect(page.getByText('Managed-CachingOptimized', { exact: false })).toHaveCount(0);
  await expect(page.getByText('Compress Objects Automatically: Disabled', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/cloudfront-routing.png', fullPage: true });
});

test('REST method tree opens request testing for the selected path', async ({ page }) => {
  await inventory(page, 'apigateway', { rest_apis: [{ id: 'rest-review', name: 'Orders API', resources: [{ id: 'root', path: '/' }, { id: 'orders', path: '/orders', resourceMethods: { POST: { methodIntegration: { type: 'AWS_PROXY', uri: 'arn:lambda:orders' } } } }], stages: [{ stageName: 'dev' }] }] });
  await page.goto('/apigateway/');
  await page.getByRole('list', { name: 'API resource paths' }).getByRole('button', { name: 'POST', exact: true }).click();
  await expect(page.getByRole('tab', { name: 'Test request', exact: true })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByLabel('Request path', { exact: true })).toHaveValue('/orders');
});

test('IAM trust summaries preserve principals, actions, and conditions', async ({ page }) => {
  await inventory(page, 'iam', { roles: [{ RoleName: 'worker-role', Arn: 'arn:aws:iam::123456789012:role/worker-role', AssumeRolePolicyDocument: { Statement: [{ Effect: 'Allow', Principal: { Service: 'lambda.amazonaws.com' }, Action: 'sts:AssumeRole', Condition: { StringEquals: { 'aws:SourceAccount': '123456789012' } } }] } }] });
  await page.goto('/iam/roles');
  await page.getByRole('tab', { name: 'Trust Relationships', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Policy statement summary', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Service: lambda.amazonaws.com', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'sts:AssumeRole', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: /aws:SourceAccount: 123456789012/ })).toBeVisible();
});

test('load balancer routing excludes listeners belonging to other resources', async ({ page }) => {
  await inventory(page, 'elasticloadbalancing', { load_balancers: [{ LoadBalancerName: 'public', LoadBalancerArn: 'arn:lb:public', DNSName: 'public.local' }], listeners: [
    { ListenerArn: 'listener-a', LoadBalancerArn: 'arn:lb:public', Protocol: 'HTTPS', Port: 443, DefaultActions: [{ TargetGroupArn: 'arn:target:worker' }] },
    { ListenerArn: 'listener-b', LoadBalancerArn: 'arn:lb:other', Protocol: 'HTTP', Port: 8080 },
  ] });
  await page.goto('/elasticloadbalancing/');
  await expect(page.getByRole('heading', { name: 'Traffic routing: public', exact: true })).toBeVisible();
  await expect(page.getByText('HTTPS:443', { exact: true })).toBeVisible();
  await expect(page.getByText('HTTP:8080', { exact: true })).toHaveCount(0);
  await expect(page.getByText('arn:target:worker', { exact: true })).toBeVisible();
});

test('Secrets Manager opens metadata without retrieving a secret value', async ({ page }) => {
  let reveals = 0;
  await inventory(page, 'secretsmanager', { secrets: [{ Name: 'database-password', ARN: 'arn:secret:database', Description: 'Application database credential' }] });
  await page.route('**/api/secretsmanager/secrets/**/value/', route => { reveals++; return route.fulfill({ json: {} }); });
  await page.goto('/secretsmanager/');
  await page.getByRole('button', { name: 'database-password', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Secret: database-password', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(reveals).toBe(0);
});

test('EC2 network navigation supports direct links and browser history', async ({ page }) => {
  await inventory(page, 'ec2', { route_tables: [{ RouteTableId: 'rtb-review', VpcId: 'vpc-review' }], internet_gateways: [{ InternetGatewayId: 'igw-review' }] });
  await page.goto('/ec2/routes');
  await expect(page.getByText('rtb-review', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Gateways and endpoints', exact: true }).click();
  await expect(page).toHaveURL(/\/ec2\/gateways$/);
  await expect(page.getByText('igw-review', { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByText('rtb-review', { exact: true })).toBeVisible();
});

test('home provides a connection recovery action and CloudWatch summarizes alarm health', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Review connection settings', exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await inventory(page, 'cloudwatch', { alarms: [{ AlarmName: 'Latency', StateValue: 'ALARM' }, { AlarmName: 'Availability', StateValue: 'OK' }] });
  await page.goto('/cloudwatch/');
  await expect(page.getByRole('button', { name: 'ALARM', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Alarm health', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'OK', exact: true })).toBeVisible();
});

test('resource details remain usable with long identifiers on a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await inventory(page, 'acm', { certificates: [{ name: 'example.test', certificate: `arn:aws:acm:eu-west-1:123456789012:certificate/${'a'.repeat(120)}`, status: 'ISSUED' }] });
  await page.goto('/acm/');
  await page.getByRole('button', { name: 'example.test', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Resource details', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'test-results/screenshots/resource-details-mobile.png', fullPage: true });
});

const ecsNested = {
  clusters: [{ name: 'app blue', arn: 'arn:aws:ecs:eu-west-1:123456789012:cluster/app blue', status: 'ACTIVE',
    services: [{ serviceName: 'worker', serviceArn: 'arn:service:worker', desiredCount: 1, runningCount: 1, status: 'ACTIVE', deployments: [{ id: 'deploy-1', rolloutState: 'COMPLETED' }], events: [{ message: 'Steady state', createdAt: '2026-10-20' }] }],
    tasks: [{ taskArn: 'arn:aws:ecs:eu-west-1:123456789012:task/app blue/task-1', group: 'service:worker', lastStatus: 'RUNNING', containers: [{ name: 'web', image: 'nginx:latest', lastStatus: 'RUNNING' }] }],
  }, { name: 'other', arn: 'arn:aws:ecs:eu-west-1:123456789012:cluster/other', services: [{ serviceName: 'worker', desiredCount: 99 }], tasks: [] }],
};

test('ECS resource hierarchy survives refresh, deep linking, breadcrumbs and browser Back', async ({ page }) => {
  await inventory(page, 'ecs', ecsNested);
  await page.goto('/ecs/clusters');
  await page.getByRole('button', { name: 'app blue', exact: true }).click();
  await expect(page).toHaveURL(/\/ecs\/clusters\/app%20blue$/);
  await page.getByRole('button', { name: 'worker', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Service: worker', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Tasks (1)', exact: true }).click();
  await page.getByRole('button', { name: 'task-1', exact: true }).click();
  await expect(page).toHaveURL(/\/services\/worker\/tasks\/task-1$/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Task: task-1', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'nginx:latest', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'worker', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Service: worker', exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Task: task-1', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/ecs-task-details.png', fullPage: true });
});

test('ECS missing resources and failed loads never fall back to a different cluster', async ({ page }) => {
  await inventory(page, 'ecs', ecsNested);
  await page.goto('/ecs/clusters/missing/services/worker');
  await expect(page.getByText('ECS resource not found', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Update service', exact: true })).toHaveCount(0);
  await page.route('**/api/ecs/', route => route.fulfill({ status: 503, json: { error: 'ECS is offline' } }));
  await page.reload();
  await expect(page.getByText('Unable to load ECS resource', { exact: true })).toBeVisible();
  await expect(page.getByText('ECS is offline', { exact: true })).toBeVisible();
});

test('ECS service updates use the cluster in the deep link and retain zero desired count', async ({ page }) => {
  await inventory(page, 'ecs', ecsNested);
  let submitted: any;
  await page.route('**/api/ecs/services/update/', async route => { submitted = route.request().postDataJSON(); await route.fulfill({ json: { desired_count: 0 } }); });
  await page.goto('/ecs/clusters/app%20blue/services/worker');
  await page.getByRole('button', { name: 'Update service', exact: true }).click();
  await page.getByRole('dialog').getByLabel('Desired Tasks Count', { exact: true }).fill('0');
  await page.getByRole('button', { name: 'Update Desired Count', exact: true }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  expect(submitted).toMatchObject({ cluster: 'app blue', service: 'worker', desired_count: 0 });
});

const eksSeparated = {
  clusters: [{ name: 'team blue', arn: 'arn:cluster:blue', status: 'ACTIVE', role_arn: 'arn:role:cluster', created_at: '2026-10-20', platform_version: 'eks.test', resources_vpc_config: { vpcId: 'vpc-team', subnetIds: ['subnet-team'], securityGroupIds: ['sg-team'], endpointPublicAccess: false, endpointPrivateAccess: true }, nodegroups: ['workers'], health: { issues: [] } }, { name: 'other', nodegroups: ['workers'] }],
  nodegroups: [{ name: 'workers', cluster_name: 'team blue', status: 'CREATE_FAILED', instance_types: ['t3.medium'], capacity_type: 'ON_DEMAND', scaling_config: { minSize: 0, desiredSize: 0, maxSize: 2 }, node_role: 'arn:role:workers', subnets: ['subnet-team'], resources: { autoScalingGroups: [{ name: 'asg-workers' }] }, health: { issues: [{ code: 'NodeCreationFailure', message: 'Workers failed to join', resourceIds: ['i-worker'] }] } }, { name: 'workers', cluster_name: 'other', status: 'ACTIVE', instance_types: ['m5.large'] }],
};

test('EKS resource navigation joins separate details and survives refresh and browser Back', async ({ page }) => {
  await inventory(page, 'eks', eksSeparated);
  await page.goto('/eks/clusters');
  await page.getByRole('button', { name: 'team blue', exact: true }).click();
  await expect(page).toHaveURL(/\/eks\/clusters\/team%20blue$/);
  await expect(page.getByText('arn:role:cluster', { exact: true })).toBeVisible();
  await expect(page.getByText('No health issues reported.', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Node Groups (1)', exact: true }).click();
  await expect(page).toHaveURL(/\?tab=nodegroups$/);
  await page.reload();
  await expect(page.getByRole('tab', { name: 'Node Groups (1)', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', { name: 'workers', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Node group: workers', exact: true })).toBeVisible();
  await expect(page.getByText('Workers failed to join', { exact: true })).toBeVisible();
  await expect(page.getByText('m5.large', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'asg-workers', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Node group: workers', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/eks-nodegroup-details.png', fullPage: true });
  await page.goBack();
  await expect(page.getByRole('tab', { name: 'Node Groups (1)', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.goForward();
  await page.getByRole('link', { name: 'team blue', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Cluster: team blue', exact: true })).toBeVisible();
});

test('EKS missing or inaccessible resources never show another clusters configuration', async ({ page }) => {
  await inventory(page, 'eks', eksSeparated);
  await page.goto('/eks/clusters/missing/nodegroups/workers');
  await expect(page.getByText('EKS resource not found', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete node group', exact: true })).toHaveCount(0);
  await page.route('**/api/eks/', route => route.fulfill({ status: 403, json: { error: 'Access denied for EKS' } }));
  await page.reload();
  await expect(page.getByText('Access denied for EKS', { exact: true })).toBeVisible();
  await expect(page.getByText('EKS resource not found', { exact: true })).toHaveCount(0);
});

test('EKS networking links open EC2 and node group pages fit narrow viewports', async ({ page }) => {
  await inventory(page, 'eks', eksSeparated);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/eks/clusters/team%20blue/nodegroups/workers');
  await expect(page.getByRole('heading', { name: 'Node group: workers', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'test-results/screenshots/eks-nodegroup-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'subnet-team', exact: true }).click();
  await expect(page).toHaveURL(/\/ec2\/subnets$/);
});

test('API Gateway request flow preserves API context and selects a route and stage for testing', async ({ page }) => {
  await inventory(page, 'apigateway', gatewayInventory);
  await page.goto('/apigateway/resources?api=http-review');
  await expect(page.getByRole('heading', { name: 'Request flow', exact: true })).toBeVisible();
  await expect(page.getByText('arn:orders', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Test selected route', exact: true }).click();
  await expect(page).toHaveURL(/\/apigateway\/test\?api=http-review$/);
  await expect(page.getByLabel('Request path', { exact: true })).toHaveValue('/orders');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Orders HTTP', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Test request', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.goBack();
  await expect(page.getByRole('tab', { name: 'Routes', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.screenshot({ path: 'test-results/screenshots/apigateway-request-flow.png', fullPage: true });
});

test('API Gateway missing API links do not fall back to another API', async ({ page }) => {
  await inventory(page, 'apigateway', gatewayInventory);
  await page.goto('/apigateway/resources?api=deleted');
  await expect(page.getByText('API not found', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Request flow', exact: true })).toHaveCount(0);
});

const eventRoutingInventory = { event_buses: [{ name: 'orders', rules: [{ name: 'created', state: 'ENABLED', event_pattern: '{"source":["ecommerce.orders"],"detail-type":["OrderPlaced"]}', targets: [{ Id: 'queue-target', Arn: 'arn:aws:sqs:us-east-1:000000000000:orders' }] }] }, { name: 'other', rules: [{ name: 'created', state: 'DISABLED', event_pattern: '{"source":["other"]}', targets: [] }] }] };

test('EventBridge routing and pattern tester preserve rule context across reloads', async ({ page }) => {
  await inventory(page, 'eventbridge', eventRoutingInventory);
  let submitted: any;
  await page.route('**/api/eventbridge/patterns/test/', route => { submitted = route.request().postDataJSON(); return route.fulfill({ json: { result: true, engine: 'emulator', summary: 'Matched by Floci' } }); });
  await page.goto('/eventbridge/rules?bus=orders&rule=created');
  await expect(page.getByRole('heading', { name: 'Event routing', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Test rule pattern', exact: true }).click();
  await expect(page).toHaveURL(/eventbridge\/tester\?bus=orders&rule=created$/);
  await page.reload();
  await expect(page.getByLabel('Pattern JSON', { exact: true })).toHaveValue(/ecommerce.orders/);
  await page.getByRole('button', { name: 'Test pattern', exact: true }).click();
  await expect(page.getByText('Pattern matches sample event', { exact: true })).toBeVisible();
  expect(submitted).toMatchObject({ engine: 'emulator', event_pattern: { source: ['ecommerce.orders'] } });
  await page.getByLabel('Sample event JSON', { exact: true }).fill('{}');
  await expect(page.getByText('Pattern matches sample event', { exact: true })).toHaveCount(0);
  await page.getByLabel('Pattern JSON', { exact: true }).fill('[]');
  await page.getByRole('button', { name: 'Test pattern', exact: true }).click();
  await expect(page.getByText('Pattern and sample event must be JSON objects.', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/eventbridge-pattern-tester.png', fullPage: true });
});

test('EventBridge pattern builder and routing views remain usable on narrow screens', async ({ page }) => {
  await inventory(page, 'eventbridge', eventRoutingInventory);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/eventbridge/tester?bus=orders&rule=created');
  await page.getByLabel('Event source', { exact: true }).fill('custom.checkout');
  await page.getByLabel('Detail type', { exact: true }).fill('Paid');
  await page.getByRole('button', { name: 'Build pattern from fields', exact: true }).click();
  await expect(page.getByLabel('Pattern JSON', { exact: true })).toHaveValue(/custom.checkout/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'test-results/screenshots/eventbridge-mobile.png', fullPage: true });
});

test('EventBridge missing rule links do not silently test a default pattern', async ({ page }) => {
  await inventory(page, 'eventbridge', eventRoutingInventory);
  await page.goto('/eventbridge/tester?bus=missing&rule=created');
  await expect(page.getByText('Rule not found', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Test pattern', exact: true })).toHaveCount(0);
});

const deliveryInventory = {
  v2_load_balancers: [{ name: 'public', arn: 'arn:lb:public', dns_name: 'public.local', state: { Code: 'active' } }, { name: 'private', arn: 'arn:lb:private' }],
  listeners: [{ arn: 'arn:listener:a', load_balancer_name: 'public', protocol: 'HTTP', port: 80, default_actions: [{ Type: 'forward', TargetGroupArn: 'arn:tg:orders' }] }, { arn: 'arn:listener:b', load_balancer_name: 'private', protocol: 'HTTP', port: 8080 }],
  target_groups: [{ name: 'orders', arn: 'arn:tg:orders', protocol: 'HTTP', port: 80, health_check_path: '/ready', target_type: 'ip' }, { name: 'other', arn: 'arn:tg:other' }],
  target_health: [{ target_group_arn: 'arn:tg:orders', target_health_descriptions: [{ Target: { Id: '10.0.1.10', Port: 80 }, TargetHealth: { State: 'unhealthy', Reason: 'Target.ResponseCodeMismatch', Description: 'Health check returned 503' } }] }, { target_group_arn: 'arn:tg:other', target_health_descriptions: [{ Target: { Id: '10.0.2.10' }, TargetHealth: { State: 'healthy' } }] }],
};

test('load balancing normalizes live inventory and scopes target health to the deep-linked group', async ({ page }) => {
  await inventory(page, 'elasticloadbalancing', deliveryInventory);
  await page.goto('/elasticloadbalancing/targets?lb=arn%3Alb%3Apublic&tg=arn%3Atg%3Aorders');
  await expect(page.getByRole('heading', { name: 'Target group: orders', exact: true })).toBeVisible();
  await expect(page.getByText('Health check returned 503', { exact: true })).toBeVisible();
  await expect(page.getByText('10.0.2.10', { exact: true })).toHaveCount(0);
  await expect(page.getByText('HTTP:8080', { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText('Target.ResponseCodeMismatch', { exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/elb-target-health.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.screenshot({ path: 'test-results/screenshots/elb-target-health-mobile.png', fullPage: true });
});

test('load balancing never inserts sample healthy targets when inventory is empty', async ({ page }) => {
  await inventory(page, 'elasticloadbalancing', { target_groups: [{ name: 'empty', arn: 'arn:tg:empty' }], target_health: [{ target_group_arn: 'arn:tg:empty', target_health_descriptions: [] }] });
  await page.goto('/elasticloadbalancing/targets?tg=arn%3Atg%3Aempty');
  await expect(page.getByText('i-0123456789abcdef0', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('cell', { name: 'healthy', exact: true })).toHaveCount(0);
  await expect(page.getByText('No targets reported for this target group.', { exact: true }).first()).toBeVisible();
});

test('CloudFront behaviors link to real origins and retain distribution context', async ({ page }) => {
  await inventory(page, 'cloudfront', { distributions: [{ Id: 'D-PATHS', DomainName: 'cdn.local', Origins: { Items: [{ Id: 'static', DomainName: 'static.local' }, { Id: 'api', DomainName: 'api.local', OriginPath: '/v1' }] }, DefaultCacheBehavior: { TargetOriginId: 'static' }, CacheBehaviors: { Items: [{ PathPattern: '/api/*', TargetOriginId: 'api', ViewerProtocolPolicy: 'https-only', CachePolicyId: 'policy-api', Compress: false }] } }, { Id: 'D-OTHER' }] });
  await page.goto('/cloudfront/origins?distribution=D-PATHS');
  await expect(page.getByRole('cell', { name: '/api/*', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'api', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Origin: api', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Distribution: D-PATHS', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/cloudfront-behaviors.png', fullPage: true });
});

test('delivery deep links never silently select a different resource and support narrow screens', async ({ page }) => {
  await inventory(page, 'elasticloadbalancing', deliveryInventory);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/elasticloadbalancing/targets?tg=missing');
  await expect(page.getByText('Target group not found in the active account and Region.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Target group: orders', exact: true })).toHaveCount(0);
  await inventory(page, 'cloudfront', { distributions: [{ Id: 'other' }] });
  await page.goto('/cloudfront/origins?distribution=missing');
  await expect(page.getByText('Distribution not found in the active account and Region.', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});

test('delivery table selection updates bookmarked resource context', async ({ page }) => {
  await inventory(page, 'cloudfront', { distributions: [{ Id: 'FIRST' }, { Id: 'SECOND' }] });
  await page.goto('/cloudfront/distributions?distribution=FIRST');
  await page.getByRole('radio').nth(1).check();
  await expect(page).toHaveURL(/distribution=SECOND$/);
  await expect(page.getByRole('heading', { name: 'Distribution: SECOND', exact: true })).toBeVisible();
  await inventory(page, 'elasticloadbalancing', deliveryInventory);
  await page.goto('/elasticloadbalancing/targetgroups?tg=arn%3Atg%3Aorders');
  await page.getByRole('radio').nth(1).check();
  await expect(page).toHaveURL(/tg=arn%3Atg%3Aother$/);
  await expect(page.getByRole('heading', { name: 'Target group: other', exact: true })).toBeVisible();
  await expect(page.getByText('10.0.1.10', { exact: true })).toHaveCount(0);
});

test('S3 bucket links preserve selection across tabs and reload without claiming private access', async ({ page }) => {
  await inventory(page, 's3', { buckets: [{ Name: 'first-bucket' }, { Name: 'photos & reports', Region: 'eu-west-1' }] });
  await page.goto('/s3/objects?bucket=photos+%26+reports');
  await expect(page.getByRole('heading', { name: 'Bucket: photos & reports', exact: true })).toBeVisible();
  await expect(page.getByText('Bucket and objects not public', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Permissions & Policy', exact: true }).click();
  await expect(page).toHaveURL(/s3\/permissions\?bucket=photos\+%26\+reports/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Bucket: photos & reports', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/s3-resource.png', fullPage: true });
});

test('S3 missing bucket link does not expose another bucket details', async ({ page }) => {
  await inventory(page, 's3', { buckets: [{ Name: 'available-bucket' }] });
  await page.goto('/s3/objects?bucket=missing-bucket');
  await expect(page.getByText('Bucket “missing-bucket” was not found. Select an available bucket.')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Bucket: available-bucket', exact: true })).toHaveCount(0);
});

test('Lambda function links retain context and show actual execution state', async ({ page }) => {
  await inventory(page, 'lambda', { functions: [{ FunctionName: 'first-worker' }, { FunctionName: 'failed-worker', Runtime: 'python3.12', State: 'Failed', StateReason: 'Execution role unavailable', LastUpdateStatus: 'Failed', Role: 'arn:aws:iam::123456789012:role/worker' }] });
  await page.goto('/lambda/code?function=failed-worker');
  await expect(page.getByRole('heading', { name: 'Function: failed-worker', exact: true })).toBeVisible();
  await expect(page.getByText('Execution role unavailable', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Monitor', exact: true }).click();
  await expect(page).toHaveURL(/lambda\/monitor\?function=failed-worker/);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Function: failed-worker', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/lambda-resource.png', fullPage: true });
});

test('Lambda rejects invalid test events before invoking', async ({ page }) => {
  await inventory(page, 'lambda', { functions: [{ FunctionName: 'validation-worker' }] });
  await page.goto('/lambda/code?function=validation-worker');
  await page.getByRole('button', { name: 'Test / Invoke Function', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Event JSON Payload').fill('{invalid');
  await dialog.getByRole('button', { name: 'Execute Function', exact: true }).click();
  await expect(dialog.getByText('Test event must be valid JSON.', { exact: true })).toBeVisible();
});

const messagingQueueArn = 'arn:aws:sqs:eu-west-1:123456789012:orders-dead';
const messagingTopicArn = 'arn:aws:sns:eu-west-1:123456789012:orders-events';
test('SQS delivery overview links dead-letter queues and retains resource context', async ({ page }) => {
  await inventory(page, 'sqs', { queues: [
    { name: 'orders', attributes: { ApproximateNumberOfMessages: '4', ApproximateNumberOfMessagesNotVisible: '2', ApproximateNumberOfMessagesDelayed: '1', RedrivePolicy: JSON.stringify({ deadLetterTargetArn: messagingQueueArn, maxReceiveCount: 5 }) } },
    { name: 'orders-dead', arn: messagingQueueArn, attributes: { VisibilityTimeout: '0' } },
  ] });
  await page.goto('/sqs/send-receive?queue=orders');
  await expect(page.getByRole('heading', { name: 'Queue delivery overview', exact: true })).toBeVisible();
  await page.getByRole('link', { name: messagingQueueArn, exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Queue: orders-dead', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Queue attributes & configuration', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Queue: orders-dead', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/sqs-delivery.png', fullPage: true });
});

test('SQS invalid redrive policies stay readable and missing queue links never select another queue', async ({ page }) => {
  await inventory(page, 'sqs', { queues: [{ name: 'orders', attributes: { RedrivePolicy: '{bad' } }] });
  await page.goto('/sqs/dlq?queue=orders');
  await expect(page.getByText('The redrive policy could not be read.', { exact: false })).toBeVisible();
  await page.goto('/sqs/attributes?queue=missing');
  await expect(page.getByText('Requested queue was not found. Select an available resource.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Queue: orders', exact: true })).toHaveCount(0);
});

test('SNS delivery settings show real confirmation counts and link to SQS destinations', async ({ page }) => {
  await inventory(page, 'sns', { topics: [{ arn: messagingTopicArn, attributes: { SubscriptionsConfirmed: '0', SubscriptionsPending: '1' }, subscriptions: [{ SubscriptionArn: 'PendingConfirmation', Protocol: 'email', Endpoint: 'review@example.test' }, { SubscriptionArn: `${messagingTopicArn}:sub`, Protocol: 'sqs', Endpoint: messagingQueueArn }] }], subscriptions: [{ arn: `${messagingTopicArn}:sub`, attributes: { RawMessageDelivery: 'false', FilterPolicy: '{"kind":["order"]}', FilterPolicyScope: 'MessageBody' } }] });
  await inventory(page, 'sqs', { queues: [{ name: 'orders-dead', arn: messagingQueueArn }] });
  await page.goto(`/sns/subscriptions?${new URLSearchParams({ topic: messagingTopicArn })}`);
  await expect(page.getByText('0 confirmed · 1 pending', { exact: true })).toBeVisible();
  await expect(page.getByText('Raw delivery: false', { exact: false })).toBeVisible();
  await expect(page.getByText('Filter scope: MessageBody', { exact: false })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/sns-delivery.png', fullPage: true });
  await page.getByRole('link', { name: messagingQueueArn, exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Queue: orders-dead', exact: true })).toBeVisible();
});

test('SNS policy view uses actual statements and preserves topic on reload', async ({ page }) => {
  await inventory(page, 'sns', { topics: [{ arn: messagingTopicArn, attributes: { Policy: JSON.stringify({ Statement: [{ Effect: 'Allow', Principal: { Service: 's3.amazonaws.com' }, Action: 'sns:Publish', Resource: messagingTopicArn }] }) } }] });
  await page.goto('/sns/');
  await page.getByRole('tab', { name: 'Access policy', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Policy statement summary', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'sns:Publish', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/screenshots/sns-mobile.png', fullPage: true });
});

test('SNS never invents a default access policy', async ({ page }) => {
  await inventory(page, 'sns', { topics: [{ arn: messagingTopicArn }] });
  await page.goto(`/sns/policy?${new URLSearchParams({ topic: messagingTopicArn })}`);
  await expect(page.getByText('Policy document not reported.', { exact: true })).toBeVisible();
  await expect(page.locator('textarea').filter({ hasText: 'No access policy reported for this topic.' })).toBeVisible();
  await expect(page.getByText('TopicOwnerAccess', { exact: false })).toHaveCount(0);
});

test('SQS ignores a receive response after switching queues', async ({ page }) => {
  await inventory(page, 'sqs', { queues: [{ name: 'orders' }, { name: 'other' }] });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/sqs/queues/orders/messages/', async route => {
    await held;
    await route.fulfill({ json: { messages: [{ MessageId: 'old-message', ReceiptHandle: 'old-handle', Body: 'old-queue-message' }] } });
  });
  await page.goto('/sqs/send-receive?queue=orders');
  const request = page.waitForRequest('**/api/sqs/queues/orders/messages/');
  await page.getByRole('button', { name: 'Poll for messages', exact: true }).click();
  await request;
  await page.getByRole('button', { name: 'other', exact: true }).click();
  const response = page.waitForResponse('**/api/sqs/queues/orders/messages/');
  release();
  await response;
  await expect(page.getByRole('heading', { name: 'Queue: other', exact: true })).toBeVisible();
  await expect(page.getByText('old-queue-message', { exact: true })).toHaveCount(0);
});

test('SNS filter policies tab renders returned settings rather than sample configuration', async ({ page }) => {
  await inventory(page, 'sns', { topics: [{ arn: messagingTopicArn, subscriptions: [{ SubscriptionArn: `${messagingTopicArn}:sub`, Protocol: 'sqs', Endpoint: messagingQueueArn, attributes: { RawMessageDelivery: 'true', FilterPolicyScope: 'MessageBody', FilterPolicy: '{"orderType":["priority"]}' } }] }] });
  await page.goto(`/sns/filtering?${new URLSearchParams({ topic: messagingTopicArn })}`);
  await expect(page.getByRole('cell', { name: 'MessageBody', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '{"orderType":["priority"]}', exact: true })).toBeVisible();
  await expect(page.getByText('example_corp', { exact: false })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/screenshots/sns-filters.png', fullPage: true });
});

test('SQS delivery overview remains usable on mobile and selects exactly one queue', async ({ page }) => {
  await inventory(page, 'sqs', { queues: [{ name: 'orders' }, { name: 'orders-dead', arn: messagingQueueArn }] });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/sqs/attributes?queue=orders-dead');
  await expect(page.getByRole('heading', { name: 'Queue delivery overview', exact: true })).toBeVisible();
  await expect(page.getByRole('radio', { checked: true })).toHaveCount(1);
  await page.screenshot({ path: 'test-results/screenshots/sqs-mobile.png', fullPage: true });
});

const dynamoReviewTable = { name: 'Orders', arn: 'arn:aws:dynamodb:eu-west-1:123456789012:table/Orders', status: 'ACTIVE', key_schema: [{ AttributeName: 'customerId', KeyType: 'HASH' }, { AttributeName: 'orderDate', KeyType: 'RANGE' }], attribute_definitions: [{ AttributeName: 'customerId', AttributeType: 'S' }, { AttributeName: 'orderDate', AttributeType: 'N' }], billing_mode: { BillingMode: 'PROVISIONED' }, provisioned_throughput: { ReadCapacityUnits: 17, WriteCapacityUnits: 9 }, ttl: { TimeToLiveStatus: 'ENABLED', AttributeName: 'expiresOn' }, stream_specification: { StreamEnabled: true, StreamViewType: 'NEW_AND_OLD_IMAGES' }, global_secondary_indexes: [{ IndexName: 'ByStatus', IndexStatus: 'CREATING', KeySchema: [{ AttributeName: 'status', KeyType: 'HASH' }], Projection: { ProjectionType: 'KEYS_ONLY' } }] };
test('DynamoDB normalizes live schema, TTL, streams and capacity and preserves the table URL', async ({ page }) => {
  await inventory(page, 'dynamodb', { tables: [{ name: 'First' }, dynamoReviewTable] });
  await page.goto('/dynamodb/items?table=Orders');
  await expect(page.getByRole('heading', { name: 'Table: Orders', exact: true })).toBeVisible();
  await expect(page.getByText('customerId (String)', { exact: true })).toBeVisible();
  await expect(page.getByText('orderDate (Number)', { exact: true })).toBeVisible();
  await expect(page.getByText('ENABLED · expiresOn', { exact: true })).toBeVisible();
  await expect(page.getByText('NEW_AND_OLD_IMAGES', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Capacity & Backups', exact: true }).click();
  await expect(page.getByLabel('Read Capacity Units (RCU)', { exact: true })).toHaveValue('17');
  await expect(page.getByLabel('Write Capacity Units (WCU)', { exact: true })).toHaveValue('9');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Table: Orders', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: /Indexes/ }).click();
  await expect(page.getByRole('cell', { name: 'ByStatus', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'KEYS_ONLY', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/dynamodb-schema.png', fullPage: true });
});

test('DynamoDB ignores an old scan after switching tables and reports scan failures', async ({ page }) => {
  await inventory(page, 'dynamodb', { tables: [{ TableName: 'Orders', PartitionKey: 'id' }, { TableName: 'Other', PartitionKey: 'id' }] });
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/dynamodb/tables/Orders/scan/**', async route => { await held; await route.fulfill({ json: { items: [{ id: 'old-table-item' }] } }); });
  await page.route('**/api/dynamodb/tables/Other/scan/**', route => route.fulfill({ status: 502, json: { error: 'Scan access unavailable' } }));
  const started = page.waitForRequest('**/api/dynamodb/tables/Orders/scan/**');
  await page.goto('/dynamodb/items?table=Orders');
  await started;
  await page.getByRole('button', { name: 'Other', exact: true }).click();
  const response = page.waitForResponse('**/api/dynamodb/tables/Orders/scan/**'); release(); await response;
  await expect(page.getByText('old-table-item', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Scan access unavailable', { exact: true })).toBeVisible();
});

const workflowReviewArn = 'arn:aws:states:eu-west-1:123456789012:stateMachine:orders';
const workflowRunArn = 'arn:aws:states:eu-west-1:123456789012:execution:orders:failed-run';
const workflowReview = { name: 'orders', arn: workflowReviewArn, definition: JSON.stringify({ StartAt: 'Validate', States: { Validate: { Type: 'Pass', End: true } } }), executions: [{ name: 'failed-run', arn: workflowRunArn, status: 'FAILED', started: '2026-10-20T12:00:00Z', state_machine_arn: workflowReviewArn }] };
test('Step Functions scopes nested executions and opens shareable failure history with real events', async ({ page }) => {
  await inventory(page, 'stepfunctions', { state_machines: [{ name: 'other', arn: 'arn:other', executions: [{ name: 'other-run', status: 'RUNNING' }] }, workflowReview] });
  await page.route('**/api/stepfunctions/executions/history/**', route => route.fulfill({ json: { events: [{ id: 1, type: 'PassStateEntered', stateEnteredEventDetails: { name: 'Validate', input: '{"orderId":7}' } }, { id: 2, type: 'ExecutionFailed', executionFailedEventDetails: { error: 'OrderRejected', cause: 'Missing payment' } }] } }));
  await page.route('**/api/stepfunctions/executions/detail/**', route => route.fulfill({ json: { stateMachineArn: workflowReviewArn, status: 'FAILED', error: 'OrderRejected', cause: 'Missing payment', input: '{"orderId":7}' } }));
  await page.goto(`/stepfunctions/machines?${new URLSearchParams({ machine: workflowReviewArn, panel: 'history' })}`);
  await expect(page.getByRole('cell', { name: 'failed-run', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'other-run', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'History', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'ExecutionFailed', exact: true })).toBeVisible();
  await expect(page.getByText('Missing payment', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Execution details', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/stepfunctions-history.png', fullPage: true });
});

test('Step Functions history failures stay visible and missing resource links do not select another workflow', async ({ page }) => {
  await inventory(page, 'stepfunctions', { state_machines: [workflowReview] });
  await page.route('**/api/stepfunctions/executions/history/**', route => route.fulfill({ status: 502, json: { error: 'History access unavailable' } }));
  await page.goto(`/stepfunctions/machines?${new URLSearchParams({ machine: workflowReviewArn, panel: 'history', execution: workflowRunArn })}`);
  await expect(page.getByText('History access unavailable', { exact: true })).toBeVisible();
  await page.goto('/stepfunctions/machines?machine=missing');
  await expect(page.getByText('Requested state machine was not found. Select an available workflow.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Machine: orders', exact: true })).toHaveCount(0);
  await page.goto('/dynamodb/overview?table=missing');
  await expect(page.getByText('Requested table was not found. Select an available table.', { exact: true })).toBeVisible();
});

test('DynamoDB loads another scan page using the returned key', async ({ page }) => {
  await inventory(page, 'dynamodb', { tables: [{ TableName: 'Orders', PartitionKey: 'id' }] });
  let cursor: any;
  await page.route('**/api/dynamodb/tables/Orders/scan/**', route => {
    cursor = route.request().postDataJSON().exclusive_start_key;
    return route.fulfill({ json: cursor ? { items: [{ id: 'second-page' }] } : { items: [{ id: 'first-page' }], last_evaluated_key: { id: 'first-page' } } });
  });
  await page.goto('/dynamodb/items?table=Orders');
  await page.getByRole('button', { name: 'Load more items', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'second-page', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'first-page', exact: true })).toBeVisible();
  expect(cursor).toEqual({ id: 'first-page' });
  await expect(page.getByRole('button', { name: 'Load more items', exact: true })).toHaveCount(0);
});

test('DynamoDB schema and Step Functions execution panels remain usable on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await inventory(page, 'dynamodb', { tables: [dynamoReviewTable] });
  await page.goto('/dynamodb/overview?table=Orders');
  await expect(page.getByRole('heading', { name: 'Table schema and storage', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/dynamodb-mobile.png', fullPage: true });
  await inventory(page, 'stepfunctions', { state_machines: [workflowReview] });
  await page.goto(`/stepfunctions/machines?${new URLSearchParams({ machine: workflowReviewArn, panel: 'history' })}`);
  await expect(page.getByRole('cell', { name: 'failed-run', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/stepfunctions-mobile.png', fullPage: true });
});

const dnsReviewInventory = { hosted_zones: [
  { id: '/hostedzone/ZPRIVATE', name: 'internal.test.', private_zone: true, resource_record_set_count: 0, record_count: 0, records: [], vpcs: [{ VPCId: 'vpc-private', VPCRegion: 'eu-west-1' }] },
  { id: '/hostedzone/ZPUBLIC', name: 'example.test.', private_zone: false, record_count: 3, delegation_set: { NameServers: ['ns1.example.test'] }, records: [
    { Name: 'www.example.test.', Type: 'A', TTL: 0, ResourceRecords: [{ Value: '192.0.2.1' }], Weight: 10, SetIdentifier: 'primary' },
    { Name: 'cdn.example.test.', Type: 'A', AliasTarget: { DNSName: 'edge.example.test.', HostedZoneId: 'ZEDGE', EvaluateTargetHealth: false } },
    { Name: 'example.test.', Type: 'TXT', TTL: 300, ResourceRecords: [{ Value: '"verification"' }] },
  ] },
] };
const authReviewInventory = { user_pools: [
  { id: 'eu-west-1_pool', name: 'Customer directory', created: '2026-10-01', details: { MfaConfiguration: 'OPTIONAL', AutoVerifiedAttributes: ['email'] }, discovery_url: 'http://local/pool/.well-known/openid-configuration',
    users: [{ name: 'alice', status: 'FORCE_CHANGE_PASSWORD', enabled: false }], groups: [{ GroupName: 'Developers', Description: 'Application developers' }],
    clients: [{ name: 'Web application', client_id: 'web-client', details: { ExplicitAuthFlows: ['ALLOW_USER_PASSWORD_AUTH'], CallbackURLs: ['https://example.test/callback'], AllowedOAuthScopes: ['openid', 'email'] } }] },
  { id: 'eu-west-1_empty', name: 'Empty directory', users: [], clients: [], groups: [] },
] };

test('Route 53 zone links preserve private metadata, zero counts, and detail tabs across reload and Back', async ({ page }) => {
  await inventory(page, 'route53', dnsReviewInventory);
  await page.goto('/route53/');
  await page.getByRole('button', { name: 'internal.test.', exact: true }).click();
  await expect(page).toHaveURL(/zone=ZPRIVATE/);
  await expect(page.getByRole('tab', { name: 'Records (0)', exact: true })).toBeVisible();
  await expect(page.getByText('Private', { exact: true }).last()).toBeVisible();
  await page.getByRole('tab', { name: 'Zone details', exact: true }).click();
  await page.reload();
  await expect(page.getByText('vpc-private (eu-west-1)', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'example.test.', exact: true }).click();
  await expect(page.getByText('Weighted · 10', { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByText('vpc-private (eu-west-1)', { exact: true })).toBeVisible();
});

test('Route 53 filters records and edits through the dedicated endpoint while preserving routing settings and TTL zero', async ({ page }) => {
  await inventory(page, 'route53', dnsReviewInventory);
  let payload: any;
  await page.route('**/api/route53/hosted-zones/ZPUBLIC/record-sets/', async route => { payload = route.request().postDataJSON(); await route.fulfill({ json: { change_info: { Status: 'INSYNC' } } }); });
  await page.goto('/route53?zone=ZPUBLIC&tab=records');
  const filter = page.getByPlaceholder('Find records by name, value, or routing policy…');
  await filter.fill('www.');
  await expect(page.getByText('cdn.example.test.', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Edit', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Value', { exact: true }).fill('192.0.2.99');
  await dialog.getByRole('button', { name: 'Save Record', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(payload).toMatchObject({ action: 'UPSERT', record_set: { Name: 'www.example.test.', Type: 'A', TTL: 0, Weight: 10, SetIdentifier: 'primary', ResourceRecords: [{ Value: '192.0.2.99' }] } });
  await filter.fill('cdn.');
  await expect(page.getByText('edge.example.test.', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit', exact: true })).toBeDisabled();
  await expect(page.getByText('Managed by target', { exact: true })).toBeVisible();
});

test('Route 53 creates records with relative dotted names and apex names without overwriting existing records', async ({ page }) => {
  await inventory(page, 'route53', dnsReviewInventory);
  const payloads: any[] = [];
  await page.route('**/api/route53/hosted-zones/ZPUBLIC/record-sets/', async route => { payloads.push(route.request().postDataJSON()); await route.fulfill({ json: { change_info: { Status: 'PENDING' } } }); });
  await page.goto('/route53?zone=ZPUBLIC');
  for (const name of ['api.dev', '@']) {
    await page.getByRole('button', { name: 'Create Record', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Record Name', { exact: true }).fill(name);
    await dialog.getByLabel('Value', { exact: true }).fill('192.0.2.5');
    await dialog.getByRole('button', { name: 'Create Record', exact: true }).click();
    await expect(dialog).not.toBeVisible();
  }
  expect(payloads.map(p => p.action)).toEqual(['CREATE', 'CREATE']);
  expect(payloads.map(p => p.record_set.Name)).toEqual(['api.dev.example.test.', 'example.test.']);
});

test('Route 53 failed inventory never displays fabricated records or service health', async ({ page }) => {
  await page.route('**/api/route53/', route => route.fulfill({ status: 502, json: { error: 'DNS permission denied' } }));
  await page.goto('/route53?zone=ZPUBLIC');
  await expect(page.getByText('DNS permission denied', { exact: true })).toBeVisible();
  await expect(page.getByText('DNS Service Ready', { exact: true })).toHaveCount(0);
  await expect(page.getByText('ns-1.awsdns.org', { exact: true })).toHaveCount(0);
});

test('Cognito normalizes real inventory, preserves pool and client URLs, and scopes app client details', async ({ page }) => {
  await inventory(page, 'cognito', authReviewInventory);
  await page.goto('/cognito/');
  await page.getByRole('button', { name: 'Customer directory', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Authentication flow', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Users (1)', exact: true }).click();
  await expect(page.getByText('alice', { exact: true })).toBeVisible();
  await expect(page.getByText('FORCE_CHANGE_PASSWORD', { exact: true })).toBeVisible();
  await expect(page.getByText('False', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'App Clients (1)', exact: true }).click();
  await page.getByRole('button', { name: 'Web application', exact: true }).click();
  await expect(page).toHaveURL(/client=web-client/);
  await expect(page.getByText('https://example.test/callback', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('ALLOW_USER_PASSWORD_AUTH', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Test selected client', exact: true }).click();
  await expect(page.getByLabel('App Client ID', { exact: true })).toHaveValue('web-client');
  await page.goBack();
  await expect(page.getByText('https://example.test/callback', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Empty directory', exact: true }).click();
  await expect(page.getByText('https://example.test/callback', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Test selected client', exact: true })).toBeDisabled();
});

test('Cognito creates a public app client and refreshes the selected pool inventory', async ({ page }) => {
  let updated = false;
  let body: any;
  await page.route('**/api/cognito/', route => route.fulfill({ json: updated ? { user_pools: [{ ...authReviewInventory.user_pools[1], clients: [{ name: 'Browser client', client_id: 'browser-client' }] }] } : authReviewInventory }));
  await page.route('**/api/cognito/user-pools/eu-west-1_empty/clients/', async route => { body = route.request().postDataJSON(); updated = true; await route.fulfill({ json: { client_id: 'browser-client' } }); });
  await page.goto('/cognito/users?pool=eu-west-1_empty&panel=pool-clients');
  await page.getByRole('button', { name: 'Create App Client', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('App Client Name', { exact: true }).fill('Browser client');
  await dialog.getByRole('button', { name: 'Create Client', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(body).toEqual({ name: 'Browser client', generate_secret: false, explicit_auth_flows: ['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'] });
  await expect(page.getByRole('button', { name: 'Browser client', exact: true })).toBeVisible();
});

test('Cognito reports authentication challenges accurately and clears invalid decoded tokens', async ({ page }) => {
  await inventory(page, 'cognito', authReviewInventory);
  await page.route('**/api/cognito/auth/initiate/', route => route.fulfill({ json: { ChallengeName: 'NEW_PASSWORD_REQUIRED', Session: 'challenge-session' } }));
  await page.goto('/cognito/auth?pool=eu-west-1_pool&client=web-client');
  await page.getByRole('button', { name: 'Authenticate User', exact: true }).click();
  await expect(page.getByText('Authentication challenge: NEW_PASSWORD_REQUIRED.', { exact: true })).toBeVisible();
  const token = page.getByLabel('Paste Raw JWT Token', { exact: true });
  await token.fill(`${Buffer.from('{}').toString('base64url')}.${Buffer.from('{"sub":"decoded-user"}').toString('base64url')}.signature`);
  await expect(page.getByRole('heading', { name: 'Decoded Claims Payload', exact: true })).toBeVisible();
  await token.fill('invalid');
  await expect(page.getByRole('heading', { name: 'Decoded Claims Payload', exact: true })).toHaveCount(0);
});

for (const width of [1440, 390]) {
  test(`Route 53 and Cognito resource views fit ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await inventory(page, 'route53', dnsReviewInventory);
    await inventory(page, 'cognito', authReviewInventory);
    for (const [service, path] of [['route53', '/route53?zone=ZPUBLIC'], ['cognito', '/cognito/users?pool=eu-west-1_pool&panel=pool-clients&client=web-client']]) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      await expect(page.getByRole('tab', { selected: true }).last()).toBeVisible();
      await page.screenshot({ path: `test-results/screenshots/${service}-review-${width}.png`, fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
      if (service === 'cognito') {
        await page.getByRole('tab', { name: 'Overview', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Authentication flow', exact: true })).toBeVisible();
        await page.screenshot({ path: `test-results/screenshots/cognito-overview-${width}.png`, fullPage: true });
      }
    }
  });
}

const ecrFixture = {
  repositories: [
    { name: 'team/backend', arn: 'arn:aws:ecr:eu-west-1:123456789012:repository/team/backend', registry_id: '123456789012', uri: 'localhost:5100/team/backend', tag_mutability: 'IMMUTABLE', created: '2026-10-19T12:00:00Z', encryption_configuration: { encryptionType: 'AES256' }, scanning_configuration: { scanOnPush: false }, tags: [{ Key: 'env', Value: 'development' }], lifecycle_policy: { lifecyclePolicyText: '{"rules":[{"rulePriority":1}]}' }, images: [{ imageDigest: 'sha256:backend', imageTag: 'v1' }, { imageDigest: 'sha256:backend', imageTag: 'stable' }, { imageDigest: 'sha256:untagged' }], image_details: [{ imageDigest: 'sha256:backend', imageTags: ['v1', 'stable'], imageSizeInBytes: 0, imagePushedAt: '2026-10-19T12:00:00Z' }, { imageDigest: 'sha256:untagged' }] },
    { name: 'team/worker', uri: 'localhost:5101/team/worker', tag_mutability: 'MUTABLE', images: [{ imageDigest: 'sha256:worker', imageTag: 'release' }], image_details: [{ imageDigest: 'sha256:worker', imageTags: ['release'], imageSizeInBytes: 1048576 }] },
  ],
};

test('ECR browses nested repositories and image details with accurate metadata and durable routes', async ({ page }) => {
  await inventory(page, 'ecr', ecrFixture);
  await page.goto('/ecr/');
  await page.getByRole('button', { name: 'team/backend', exact: true }).click();
  await expect(page).toHaveURL(/repository=team%2Fbackend/);
  await expect(page.getByRole('heading', { name: 'team/backend', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'sha256:worker', exact: true })).toHaveCount(0);
  await expect(page.getByText('0.00 MiB', { exact: true })).toBeVisible();
  await expect(page.getByText('Untagged', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'sha256:backend', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Image details', exact: true })).toBeVisible();
  await expect(page.getByText('localhost:5100/team/backend@sha256:backend', { exact: true })).toBeVisible();
  await expect(page.getByText("docker pull 'localhost:5100/team/backend@sha256:backend'", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Image details', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'View all images', exact: true }).click();
  await page.getByRole('button', { name: 'sha256:untagged', exact: true }).click();
  await expect(page.getByText('Unavailable', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('50.0 MB', { exact: true })).toHaveCount(0);
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'team/backend', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/ecr-repository-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/screenshots/ecr-repository-mobile.png', fullPage: true });
});

test('ECR push commands use the actual registry, endpoint and Region; repository policies stay scoped', async ({ page }) => {
  await inventory(page, 'ecr', ecrFixture);
  await page.goto('/ecr/images?repository=team%2Fbackend');
  await page.getByRole('button', { name: 'View push commands', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(/aws ecr get-login-password/)).toContainText("--region 'eu-west-1' --endpoint-url 'http://local-floci:4566'");
  await expect(dialog.getByText(/aws ecr get-login-password/)).toContainText("--password-stdin 'localhost:5100'");
  await dialog.getByRole('textbox', { name: 'Image tag', exact: true }).fill('v2');
  await expect(dialog.getByText("docker push 'localhost:5100/team/backend:v2'", { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Done' }).click();
  await page.getByRole('tab', { name: 'Lifecycle policy & settings' }).click();
  await expect(page.getByRole('textbox', { name: 'Lifecycle policy JSON' })).toHaveValue('{"rules":[{"rulePriority":1}]}');
  await page.goto('/ecr/lifecycle?repository=team%2Fworker');
  await expect(page.getByRole('textbox', { name: 'Lifecycle policy JSON' })).toHaveValue('');
  await expect(page.getByText('No lifecycle policy is configured for this repository.')).toBeVisible();
  await page.getByRole('button', { name: 'View push commands', exact: true }).click();
  await expect(dialog.getByText("docker push 'localhost:5101/team/worker:latest'", { exact: true })).toBeVisible();
});

test('ECR handles missing resources, failed loads, and partial image deletion without false success', async ({ page }) => {
  await inventory(page, 'ecr', ecrFixture);
  await page.goto('/ecr/images?repository=missing');
  await expect(page.getByText('Repository "missing" was not found in the current account and Region.')).toBeVisible();
  await page.goto('/ecr/images?repository=team%2Fbackend');
  let deletion: any;
  await page.route('**/api/ecr/images/delete/', async route => {
    deletion = route.request().postDataJSON();
    await route.fulfill({ json: { failures: [{ failureCode: 'ImageNotFound', failureReason: 'Image no longer exists' }] } });
  });
  await page.getByRole('table').getByRole('checkbox').first().check();
  await page.getByRole('button', { name: 'Delete images', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('Deleting an image by digest removes all of its tags.');
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByText(/Some images could not be deleted/)).toBeVisible();
  expect(deletion.repository_name).toBe('team/backend');
  expect(deletion.image_ids).toEqual([{ imageDigest: 'sha256:backend' }, { imageDigest: 'sha256:untagged' }]);
  await page.route('**/api/ecr/', route => route.fulfill({ status: 503, json: { error: 'Registry unavailable' } }));
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Unable to load ECR resources', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'team/backend', exact: true })).toBeVisible();
});

function kmsFixture() {
  return { keys: [
    { key_id: 'key-app', key_arn: 'arn:aws:kms:eu-west-1:123456789012:key/key-app', metadata: { KeyId: 'key-app', KeyState: 'Enabled', KeySpec: 'SYMMETRIC_DEFAULT', KeyUsage: 'ENCRYPT_DECRYPT', Origin: 'AWS_KMS', KeyManager: 'CUSTOMER', CreationDate: '2026-10-19T12:00:00Z', Description: 'Application encryption', MultiRegion: false }, aliases: [{ AliasName: 'alias/application' }, { AliasName: 'alias/backup' }], policy: { Version: '2012-10-17', Statement: [] }, rotation_enabled: true, tags: [{ TagKey: 'env', TagValue: 'development' }], grants: [{ GrantId: 'grant-app', GranteePrincipal: 'arn:aws:iam::123456789012:role/application', Operations: ['Decrypt'] }] },
    { key_id: 'key-pending', metadata: { KeyState: 'PendingDeletion', DeletionDate: '2026-11-01T12:00:00Z', KeySpec: 'SYMMETRIC_DEFAULT', KeyUsage: 'ENCRYPT_DECRYPT', Origin: 'AWS_KMS', KeyManager: 'CUSTOMER' }, policy: { Statement: [{ Sid: 'PendingKeyPolicy' }] }, rotation_enabled: false, grants: [], tags: [] },
    { key_id: 'key-sign', metadata: { KeyState: 'Enabled', KeySpec: 'ECC_NIST_P256', KeyUsage: 'SIGN_VERIFY', Origin: 'AWS_KMS', KeyManager: 'CUSTOMER' }, grants: [], tags: [] },
    { key_id: 'key-unknown', metadata: { error: 'Access denied to DescribeKey' }, rotation_enabled: { error: 'Rotation unavailable' }, grants: { error: 'Grants unavailable' } },
  ] };
}

test('KMS reads nested metadata and preserves key routes, policies, and real states', async ({ page }) => {
  await inventory(page, 'kms', kmsFixture());
  await page.goto('/kms/keys');
  const pendingRow = page.getByRole('row').filter({ hasText: 'key-pending' });
  await expect(pendingRow).toContainText('PendingDeletion');
  const unknownRow = page.getByRole('row').filter({ hasText: 'key-unknown' });
  await expect(unknownRow).not.toContainText('Enabled');
  await page.getByRole('button', { name: 'key-app', exact: true }).click();
  await expect(page).toHaveURL(/key=key-app/);
  await expect(page.getByRole('heading', { name: 'alias/application', exact: true })).toBeVisible();
  await expect(page.getByText('alias/application, alias/backup', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Key policy', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Key policy JSON' })).toHaveValue(/2012-10-17/);
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Key policy JSON' })).toHaveValue(/2012-10-17/);
  await page.goto('/kms/policy?key=key-pending');
  await expect(page.getByRole('textbox', { name: 'Key policy JSON' })).toHaveValue(/PendingKeyPolicy/);
  await expect(page.getByText('Key pending deletion', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enable key', exact: true })).toHaveCount(0);
  await page.goto('/kms/details?key=key-app');
  await page.screenshot({ path: 'test-results/screenshots/kms-key-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/screenshots/kms-key-mobile.png', fullPage: true });
});

function kmsRequestAction(path: string, method: string) {
  const actions: Record<string, string> = {
    '/api/kms/keys/': 'create_key', '/api/kms/aliases/': 'create_alias',
    '/api/kms/rotation/': 'set_key_rotation', '/api/kms/key-state/': 'set_key_enabled',
    '/api/kms/crypto/encrypt/': 'encrypt', '/api/kms/crypto/decrypt/': 'decrypt',
    '/api/kms/data-keys/': 'generate_data_key', '/api/kms/key-policy/': 'put_key_policy',
    '/api/kms/grants/': method === 'DELETE' ? 'revoke_grant' : 'create_grant',
  };
  if (!actions[path]) throw new Error(`Unexpected KMS mutation endpoint: ${path}`);
  return actions[path];
}

test('KMS rotation and state changes use supported actions and refresh the active key', async ({ page }) => {
  const fixture = kmsFixture();
  const requests: any[] = [];
  await page.route('**/api/kms/**', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: fixture });
    const body = { ...route.request().postDataJSON(), action: kmsRequestAction(new URL(route.request().url()).pathname, route.request().method()) }; requests.push(body);
    if (body.action === 'set_key_rotation') fixture.keys[0].rotation_enabled = body.enabled;
    if (body.action === 'set_key_enabled') fixture.keys[0].metadata.KeyState = body.enabled ? 'Enabled' : 'Disabled';
    return route.fulfill({ json: {} });
  });
  await page.goto('/kms/rotation?key=key-app');
  await expect(page.getByRole('button', { name: 'Disable automatic rotation' })).toBeEnabled();
  await page.getByRole('button', { name: 'Disable automatic rotation' }).click();
  await expect(page.getByRole('button', { name: 'Enable automatic rotation' })).toBeEnabled();
  expect(requests[0]).toEqual({ action: 'set_key_rotation', key_id: 'key-app', enabled: false });
  await page.getByRole('button', { name: 'Disable key', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('Applications using this key');
  await page.getByRole('dialog').getByRole('button', { name: 'Disable key', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Enable key', exact: true })).toBeVisible();
  expect(requests[1]).toEqual({ action: 'set_key_enabled', key_id: 'key-app', enabled: false });
  await expect(page.getByRole('button', { name: 'Enable automatic rotation' })).toBeDisabled();
  await page.goto('/kms/rotation?key=key-sign');
  await expect(page.getByRole('button', { name: 'Enable automatic rotation' })).toBeDisabled();
  await page.goto('/kms/rotation?key=key-unknown');
  await expect(page.getByText('Some key details are unavailable', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enable automatic rotation' })).toBeDisabled();
});

test('KMS crypto tools use backend response fields, mask data keys and clear results between keys', async ({ page }) => {
  const fixture = kmsFixture();
  const requests: any[] = [];
  await page.route('**/api/kms/**', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: fixture });
    const body = { ...route.request().postDataJSON(), action: kmsRequestAction(new URL(route.request().url()).pathname, route.request().method()) }; requests.push(body);
    return route.fulfill({ json: body.action === 'encrypt' ? { ciphertext_blob: 'ZW5jcnlwdGVk' } : body.action === 'decrypt' ? { plaintext: 'Hello KMS', key_id: 'key-app' } : { plaintext_base64: 'c2VjcmV0LWtleQ==', ciphertext_blob: 'ZW5jcnlwdGVkLWtleQ==', key_id: 'key-app' } });
  });
  await page.goto('/kms/tools?key=key-app');
  await page.getByRole('textbox', { name: 'Plaintext', exact: true }).fill('Hello KMS');
  await page.getByRole('button', { name: 'Encrypt', exact: true }).click();
  await expect(page.getByRole('textbox', { name: 'Ciphertext (Base64)', exact: true })).toHaveValue('ZW5jcnlwdGVk');
  await page.getByRole('button', { name: 'Decrypt', exact: true }).click();
  await expect(page.locator('span').getByText('Hello KMS', { exact: true })).toBeVisible();
  expect(requests[1]).toEqual({ action: 'decrypt', ciphertext_blob: 'ZW5jcnlwdGVk' });
  await page.getByRole('button', { name: 'Generate data key', exact: true }).click();
  await expect(page.getByText('Hidden', { exact: true })).toBeVisible();
  await expect(page.getByText('c2VjcmV0LWtleQ==', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Reveal plaintext key' }).click();
  await expect(page.getByText('c2VjcmV0LWtleQ==', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'AWS KMS', exact: true }).click();
  await page.getByRole('button', { name: 'key-sign', exact: true }).click();
  await page.getByRole('tab', { name: 'Cryptographic tools', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Encrypt', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Generate data key', exact: true })).toBeDisabled();
  await expect(page.getByText('c2VjcmV0LWtleQ==', { exact: true })).toHaveCount(0);
});

test('KMS creation keeps compatible specs and reports a created key when alias creation fails', async ({ page }) => {
  const fixture = kmsFixture();
  let creates = 0;
  let createBody: any;
  await page.route('**/api/kms/**', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: fixture });
    const body = { ...route.request().postDataJSON(), action: kmsRequestAction(new URL(route.request().url()).pathname, route.request().method()) };
    if (body.action === 'create_key') {
      creates++; createBody = body;
      return route.fulfill({ json: { key_id: 'key-app' } });
    }
    return route.fulfill({ status: 400, json: { error: 'Alias already exists' } });
  });
  await page.goto('/kms/keys');
  await page.getByRole('button', { name: 'Create key', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'SYMMETRIC_DEFAULT' }).click();
  await page.getByRole('option', { name: 'ECC_NIST_P256', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'SIGN_VERIFY' })).toBeVisible();
  await dialog.getByRole('textbox', { name: 'Alias (optional)' }).fill('alias/application');
  await dialog.getByRole('button', { name: 'Create key', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText(/Key created, but its alias could not be created: Alias already exists/)).toBeVisible();
  expect(creates).toBe(1);
  expect(createBody.key_usage).toBe('SIGN_VERIFY');
  expect(createBody.key_spec).toBe('ECC_NIST_P256');
});

test('KMS validates policy JSON and confirms grant revocation, with visible load failures', async ({ page }) => {
  const fixture = kmsFixture();
  const requests: any[] = [];
  await page.route('**/api/kms/**', async route => {
    if (route.request().method() === 'GET') return route.fulfill({ json: fixture });
    const body = { ...route.request().postDataJSON(), action: kmsRequestAction(new URL(route.request().url()).pathname, route.request().method()) }; requests.push(body);
    if (body.action === 'revoke_grant') fixture.keys[0].grants = [];
    return route.fulfill({ json: {} });
  });
  await page.goto('/kms/policy?key=key-app');
  await page.getByRole('textbox', { name: 'Key policy JSON' }).fill('{');
  await page.getByRole('button', { name: 'Save policy' }).click();
  await expect(page.getByText('Enter valid JSON for the key policy.')).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.getByRole('tab', { name: 'Grants (1)', exact: true }).click();
  await page.getByRole('button', { name: 'Revoke grant', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('grant-app');
  expect(requests).toHaveLength(0);
  await page.getByRole('dialog').getByRole('button', { name: 'Revoke grant', exact: true }).click();
  await expect(page.getByText('Grant revoked.', { exact: true })).toBeVisible();
  expect(requests[0]).toEqual({ action: 'revoke_grant', key_id: 'key-app', grant_id: 'grant-app' });
  await page.route('**/api/kms/', route => route.fulfill({ status: 503, json: { error: 'KMS unavailable' } }));
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Unable to load KMS resources', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Disable key', exact: true })).toBeDisabled();
});

function ssmFixture() {
  return { parameters: [
    { name: '/app/prod/password', type: 'SecureString', version: 3, key_id: 'alias/customer-key', description: 'Database password', tier: 'Advanced', data_type: 'text', last_modified: '2026-10-19T12:00:00Z', last_modified_user: 'arn:aws:iam::123456789012:role/deployer' },
    { name: '/app/prod/endpoint', type: 'String', version: 1 },
    { name: '/apple/config', type: 'StringList', version: 2 },
    { name: 'flat-setting', type: 'String' },
  ], documents: [{ name: 'RunTask', document_type: 'Command', document_format: 'JSON', document_version: '1', owner: '123456789012', status: 'Active' }] };
}

test('SSM browses path boundaries and durable parameter details without invented metadata', async ({ page }) => {
  await inventory(page, 'ssm', ssmFixture());
  await page.goto('/ssm/parameters');
  await page.getByRole('button', { name: '/app/', exact: true }).click();
  await expect(page.getByRole('button', { name: '/apple/config', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '/app/prod/', exact: true }).click();
  await page.getByRole('button', { name: '/app/prod/password', exact: true }).click();
  await expect(page).toHaveURL(/parameter=%2Fapp%2Fprod%2Fpassword/);
  await expect(page.getByText('alias/customer-key', { exact: true })).toBeVisible();
  await expect(page.getByText('Advanced', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: '/app/prod/password', exact: true })).toBeVisible();
  await page.screenshot({ path: 'test-results/screenshots/ssm-parameter-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/screenshots/ssm-parameter-mobile.png', fullPage: true });
  await page.goto('/ssm/details?parameter=%2Fapp%2Fprod%2Fendpoint');
  await expect(page.getByText('Unavailable', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Active value', { exact: true })).toHaveCount(0);
});

test('SSM reveals values and historical versions only on request and hides them on navigation', async ({ page }) => {
  let reveals = 0;
  let revealedPath = '';
  await inventory(page, 'ssm', ssmFixture());
  await page.route('**/api/ssm/history/**', route => route.fulfill({ json: { parameters: [{ Version: 3, LastModifiedDate: '2026-10-19T12:00:00Z', Labels: ['current'] }, { Version: 2 }] } }));
  await page.route('**/api/ssm/parameters/**', route => { reveals++; revealedPath = decodeURIComponent(new URL(route.request().url()).pathname); return route.fulfill({ json: { value: '  secret value\n', version: revealedPath.includes(':2') ? 2 : 3 } }); });
  await page.goto('/ssm/details?parameter=%2Fapp%2Fprod%2Fpassword');
  await expect(page.getByRole('button', { name: 'Reveal decrypted value' })).toBeVisible();
  expect(reveals).toBe(0);
  await page.getByRole('button', { name: 'Reveal decrypted value' }).click();
  await expect(page.getByText('secret value', { exact: true })).toBeVisible();
  expect(revealedPath).toBe('/api/ssm/parameters//app/prod/password/value/');
  await page.getByRole('button', { name: 'Hide value' }).click();
  await expect(page.getByText('secret value', { exact: true })).toHaveCount(0);
  await page.getByRole('tab', { name: 'Version history', exact: true }).click();
  await expect(page.getByRole('button', { name: 'View version 2' })).toBeVisible();
  expect(reveals).toBe(1);
  await page.getByRole('button', { name: 'View version 2' }).click();
  await expect(page.getByRole('heading', { name: 'Value · version 2', exact: true })).toBeVisible();
  expect(revealedPath).toContain('/app/prod/password:2/value/');
  await page.getByRole('link', { name: 'Parameter Store', exact: true }).first().click();
  await page.getByRole('checkbox', { name: 'Include nested paths' }).check();
  await page.getByRole('button', { name: '/app/prod/endpoint', exact: true }).click();
  await expect(page.getByText('secret value', { exact: true })).toHaveCount(0);
});

test('SSM creation preserves whitespace without overwriting and editing creates a new version', async ({ page }) => {
  const fixture = ssmFixture();
  const requests: any[] = [];
  await page.route('**/api/ssm/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/ssm/') return route.fulfill({ json: fixture });
    requests.push({ method: route.request().method(), path: decodeURIComponent(url.pathname), ...route.request().postDataJSON() });
    fixture.parameters.push({ name: '/app/prod/new', type: 'String', version: 1 });
    return route.fulfill({ json: { name: '/app/prod/new', version: 1 } });
  });
  await page.goto('/ssm/parameters');
  await page.getByRole('button', { name: 'Create parameter', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'Parameter name', exact: true }).fill('/app/prod/new');
  await dialog.getByRole('textbox', { name: 'Value', exact: true }).fill('  keep whitespace\n');
  await dialog.getByRole('button', { name: 'Create parameter', exact: true }).click();
  await expect(page.getByRole('heading', { name: '/app/prod/new', exact: true })).toBeVisible();
  expect(requests[0].overwrite).toBe(false);
  expect(requests[0].value).toBe('  keep whitespace\n');
  await page.getByRole('button', { name: 'Edit parameter', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'New value', exact: true }).fill(' next version ');
  await dialog.getByRole('button', { name: 'Save new version', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[1].method).toBe('PUT');
  expect(requests[1].path).toBe('/api/ssm/parameters//app/prod/new/value/');
  expect(requests[1].value).toBe(' next version ');
});

test('SSM tags accept an empty value and parameter deletion requires confirmation', async ({ page }) => {
  const fixture = ssmFixture();
  let tags: any[] = [];
  let deletion = '';
  await inventory(page, 'ssm', fixture);
  await page.route('**/api/ssm/tags/**', route => {
    if (route.request().method() === 'POST') tags = route.request().postDataJSON().tags;
    if (route.request().method() === 'DELETE') tags = [];
    return route.fulfill({ json: { tags } });
  });
  await page.route('**/api/ssm/parameters/**', route => { deletion = decodeURIComponent(new URL(route.request().url()).pathname); return route.fulfill({ json: {} }); });
  await page.goto('/ssm/tags?parameter=%2Fapp%2Fprod%2Fpassword');
  await page.getByRole('button', { name: 'Add tag', exact: true }).click();
  await page.getByRole('dialog').getByRole('textbox', { name: 'Tag key' }).fill('owner');
  await page.getByRole('dialog').getByRole('button', { name: 'Save tag' }).click();
  await expect(page.getByRole('table').getByText('owner', { exact: true })).toBeVisible();
  expect(tags).toEqual([{ Key: 'owner', Value: '' }]);
  await page.getByRole('button', { name: 'Remove tag', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Remove tag', exact: true }).click();
  await expect(page.getByText('No tags associated with this parameter.')).toBeVisible();
  await page.getByRole('button', { name: 'Delete parameter', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('All versions of this parameter will be deleted.');
  expect(deletion).toBe('');
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page).toHaveURL(/ssm\/parameters/);
  expect(deletion).toBe('/api/ssm/parameters//app/prod/password/value/');
});

test('SSM shows real history errors and validates document creation', async ({ page }) => {
  await inventory(page, 'ssm', ssmFixture());
  await page.route('**/api/ssm/history/**', route => route.fulfill({ status: 403, json: { error: 'History access denied' } }));
  await page.goto('/ssm/history?parameter=%2Fapp%2Fprod%2Fpassword');
  await expect(page.getByText('History access denied', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /View version/ })).toHaveCount(0);
  await page.goto('/ssm/documents');
  await expect(page.getByRole('table')).toContainText('RunTask');
  await page.getByRole('button', { name: 'Create document', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'Document name' }).fill('NewTask');
  await dialog.getByRole('textbox', { name: 'Document content (JSON)' }).fill('{');
  await dialog.getByRole('button', { name: 'Create document', exact: true }).click();
  await expect(dialog.getByText('Enter valid JSON for the document.', { exact: true })).toBeVisible();
});

function sesFixture() {
  return {
    account: { ProductionAccessEnabled: false, SendingEnabled: true },
    account_sending_enabled: { Enabled: true }, send_quota: { Max24HourSend: 200, MaxSendRate: 1, SentLast24Hours: 0 },
    identities: [
      { name: 'sender@example.com', type: 'email', verification: { VerificationStatus: 'Success' }, dkim: { DkimEnabled: true, DkimVerificationStatus: 'Success' } },
      { name: 'example.org', type: 'domain', verification: { VerificationStatus: 'Pending', VerificationToken: 'domain-token' }, dkim: { DkimEnabled: false, DkimVerificationStatus: 'Pending', DkimTokens: ['dkim-token'] }, notifications: { BounceTopic: 'arn:aws:sns:eu-west-1:123456789012:bounces', ForwardingEnabled: false } },
      { name: 'verified.example.com', verification: { VerificationStatus: 'Success' } },
      { name: 'unknown@example.com', type: 'email' },
    ],
    templates: [{ name: 'Welcome', created: '2026-10-19T12:00:00Z', details: { SubjectPart: 'Hello {{name}}', TextPart: 'Welcome {{name}}' } }],
    v2_configuration_sets: [{ name: 'transactional', sending_enabled: false, event_destinations: [], delivery_options: { TlsPolicy: 'REQUIRE' } }],
    mailbox: { messages: [] },
  };
}

test('SES reports actual sending readiness and browses identity verification details with durable routes', async ({ page }) => {
  await inventory(page, 'ses', sesFixture());
  await page.goto('/ses/overview');
  await expect(page.getByText('Sandbox', { exact: true })).toBeVisible();
  await expect(page.getByText('0', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Manage identities' }).click();
  const table = page.getByRole('table');
  await expect(table.getByRole('row').filter({ hasText: 'unknown@example.com' })).toContainText('Unavailable');
  await expect(table.getByRole('row').filter({ hasText: 'unknown@example.com' })).not.toContainText('Verified');
  await page.getByRole('button', { name: 'example.org', exact: true }).click();
  await expect(page).toHaveURL(/identity=example.org/);
  await expect(page.getByText('Verification is not confirmed', { exact: true })).toBeVisible();
  await expect(page.getByText('_amazonses.example.org', { exact: true })).toBeVisible();
  await expect(page.getByText('domain-token', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'DKIM authentication' }).click();
  await expect(page.getByText('dkim-token', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('tab', { name: 'DKIM authentication' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Notifications', exact: true }).click();
  await expect(page.getByText('arn:aws:sns:eu-west-1:123456789012:bounces', { exact: true })).toBeVisible();
  await page.goto('/ses/details?identity=example.org');
  await page.screenshot({ path: 'test-results/screenshots/ses-identity-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/screenshots/ses-identity-mobile.png', fullPage: true });
});

test('SES requests verification without claiming success and surfaces account errors', async ({ page }) => {
  const fixture = sesFixture();
  await inventory(page, 'ses', fixture);
  await page.route('**/api/ses/identities/email/', async route => {
    const body = route.request().postDataJSON();
    fixture.identities.push({ name: body.email_address, type: 'email', verification: { VerificationStatus: 'Pending' } });
    await route.fulfill({ json: { identity: body.email_address } });
  });
  await page.goto('/ses/identities');
  await page.getByRole('button', { name: 'Create identity', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'Email address', exact: true }).fill('new@example.com');
  await dialog.getByRole('button', { name: 'Request verification' }).click();
  await expect(page.getByRole('heading', { name: 'new@example.com', exact: true })).toBeVisible();
  await expect(page.getByText('Verification is not confirmed', { exact: true })).toBeVisible();
  await expect(page.getByText('Pending', { exact: true })).toBeVisible();
  await page.route('**/api/ses/', route => route.fulfill({ json: { account_sending_enabled: { error: 'Sending status access denied' }, identities: ['legacy@example.com'] } }));
  await page.goto('/ses/overview');
  await expect(page.getByText('Sending status access denied', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Enable account sending' })).toBeDisabled();
  await page.goto('/ses/send?source=legacy%40example.com');
  await expect(page.getByRole('button', { name: 'Send test email', exact: true })).toBeDisabled();
});

test('SES composes local test email with verified sender gating, exact content and acceptance ID', async ({ page }) => {
  await inventory(page, 'ses', sesFixture());
  let sent: any;
  await page.route('**/api/ses/email/send/', async route => { sent = route.request().postDataJSON(); await route.fulfill({ json: { message_id: 'accepted-message-id' } }); });
  await page.goto('/ses/send');
  await page.getByRole('textbox', { name: 'From address' }).fill('attacker@notverified.example.com');
  await page.getByRole('textbox', { name: 'To addresses' }).fill('recipient@example.com');
  await page.getByRole('textbox', { name: 'Subject', exact: true }).fill('Test subject');
  await page.getByRole('textbox', { name: 'Text body', exact: true }).fill('  preserve body\n');
  await expect(page.getByRole('button', { name: 'Send test email', exact: true })).toBeDisabled();
  await page.getByRole('textbox', { name: 'From address' }).fill('sender@verified.example.com');
  await expect(page.getByRole('button', { name: 'Send test email', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Send test email', exact: true }).click();
  await expect(page.getByText('Message accepted', { exact: true })).toBeVisible();
  await expect(page.getByText(/Message ID: accepted-message-id/)).toBeVisible();
  expect(sent).toEqual({ source: 'sender@verified.example.com', to_addresses: ['recipient@example.com'], subject: 'Test subject', text: '  preserve body\n' });
  await page.getByRole('textbox', { name: 'From address' }).fill('sender@evilverified.example.com');
  await expect(page.getByRole('button', { name: 'Send test email', exact: true })).toBeDisabled();
});

test('SES templates use backend field names and clear rendered output between templates', async ({ page }) => {
  await inventory(page, 'ses', sesFixture());
  let created: any;
  await page.route('**/api/ses/templates/', async route => { created = route.request().postDataJSON(); await route.fulfill({ json: { template_name: created.template_name } }); });
  await page.route('**/api/ses/templates/Welcome/render/', route => route.fulfill({ json: { rendered_template: 'Hello Alice' } }));
  await page.goto('/ses/templates');
  await expect(page.getByRole('table')).toContainText('Hello {{name}}');
  await page.getByRole('button', { name: 'Create template', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'Name', exact: true }).fill('NewTemplate');
  await dialog.getByRole('textbox', { name: 'Subject', exact: true }).fill('Hello');
  await dialog.getByRole('textbox', { name: 'Text body', exact: true }).fill('  template body\n');
  await dialog.getByRole('button', { name: 'Create', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(created).toEqual({ template_name: 'NewTemplate', subject: 'Hello', html: '', text: '  template body\n' });
  await page.getByRole('button', { name: 'Test render', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Template data (JSON)' }).fill('{');
  await dialog.getByRole('button', { name: 'Render template', exact: true }).click();
  await expect(dialog.getByText('Enter valid JSON for the template data.')).toBeVisible();
  await dialog.getByRole('textbox', { name: 'Template data (JSON)' }).fill('{"name":"Alice"}');
  await dialog.getByRole('button', { name: 'Render template', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'Rendered message source' })).toHaveValue('Hello Alice');
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'Test render', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'Rendered message source' })).toHaveCount(0);
});

test('SES account sending uses PUT after confirmation and configuration sets retain paused status', async ({ page }) => {
  const fixture = sesFixture(); let method = '';
  await inventory(page, 'ses', fixture);
  await page.route('**/api/ses/account/sending/', async route => { method = route.request().method(); fixture.account_sending_enabled.Enabled = route.request().postDataJSON().enabled; await route.fulfill({ json: { enabled: false } }); });
  await page.goto('/ses/overview');
  await page.getByRole('button', { name: 'Pause account sending', exact: true }).click();
  expect(method).toBe('');
  await expect(page.getByRole('dialog')).toContainText('every application');
  await page.getByRole('dialog').getByRole('button', { name: 'Confirm', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Enable account sending', exact: true })).toBeEnabled();
  expect(method).toBe('PUT');
  await page.goto('/ses/configsets');
  await expect(page.getByRole('row').filter({ hasText: 'transactional' })).toContainText('Paused');
  await expect(page.getByRole('row').filter({ hasText: 'transactional' })).toContainText('REQUIRE');
});

test('SES local mailbox preserves real message metadata and confirms DELETE clearing', async ({ page }) => {
  await inventory(page, 'ses', sesFixture());
  let cleared = false;
  await page.route('**/api/inspector/ses/messages/', route => route.fulfill({ json: { messages: cleared ? [] : [{ MessageId: 'real-message', Source: 'sender@example.com', Destination: { ToAddresses: ['recipient@example.com'] }, Message: { Subject: { Data: 'Captured subject' }, Body: { Text: { Data: 'Captured text' }, Html: { Data: '<script>window.untrustedEmail=true</script>' } } } }] } }));
  let method = '';
  await page.route('**/api/ses/mailbox/', async route => { method = route.request().method(); cleared = true; await route.fulfill({ json: {} }); });
  await page.goto('/ses/mailbox');
  await expect(page.getByRole('table')).toContainText('real-message');
  await expect(page.getByRole('table')).toContainText('recipient@example.com');
  await page.getByRole('button', { name: 'Captured subject', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Captured text', { exact: true })).toBeVisible();
  await dialog.getByRole('tab', { name: 'HTML source', exact: true }).click();
  await expect(dialog.getByText('<script>window.untrustedEmail=true</script>', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).untrustedEmail)).toBeUndefined();
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'Clear local mailbox', exact: true }).click();
  expect(method).toBe('');
  await dialog.getByRole('button', { name: 'Clear mailbox', exact: true }).click();
  await expect(page.getByText(/No captured messages. Send a local test email/)).toBeVisible();
  expect(method).toBe('DELETE');
});

function sessionFixture(user = 'alice') {
  return {
    region: 'eu-west-1', endpoint_url: 'http://local-floci:4566', identity_resolved: true,
    credential_source: 'environment', caller_identity: { account: '123456789012', arn: `arn:aws:iam::123456789012:user/${user}`, user_id: user },
    session_identity: null as any,
  };
}
const identityOptions = { users: [{ name: 'alice', arn: 'arn:aws:iam::123456789012:user/alice' }, { name: 'bob', arn: 'arn:aws:iam::123456789012:user/bob' }], roles: [{ name: 'AppRole', arn: 'arn:aws:iam::123456789012:role/team/application/AppRole', path: '/team/application/' }], errors: {} };

test('Session identity normalizes real lowercase caller metadata without administrator assumptions', async ({ page }) => {
  await page.route('**/api/session-identity/', route => route.fulfill({ json: sessionFixture() }));
  await page.route('**/api/session-identity/options/', route => route.fulfill({ json: identityOptions }));
  await page.goto('/environment/');
  await expect(page.getByRole('button', { name: 'alice @ 123456789012', exact: true })).toBeVisible();
  await expect(page.getByRole('main').getByText('arn:aws:iam::123456789012:user/alice', { exact: true })).toBeVisible();
  await expect(page.getByText('Verified by STS', { exact: true })).toBeVisible();
  await expect(page.getByText('Root Admin', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Full AdministratorAccess', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Persistent (Never expires)', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: 'test-results/screenshots/session-identity-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/screenshots/session-identity-mobile.png', fullPage: true });
});

test('Session identity shows unresolved STS and request failures without trusting local hints', async ({ page }) => {
  await page.route('**/api/session-identity/', route => route.fulfill({ json: { ...sessionFixture(), identity_resolved: false, identity_error: 'STS unavailable', caller_identity: { account: 'local', arn: 'test (local credentials)', user_id: 'test' } } }));
  await page.goto('/environment/');
  await expect(page.getByText('Caller identity could not be verified', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Identity unavailable', exact: true })).toBeVisible();
  await expect(page.getByText('test (local credentials)', { exact: true })).toHaveCount(0);
  await expect(page.getByText('000000000000', { exact: true })).toHaveCount(0);
  await page.route('**/api/session-identity/', route => route.fulfill({ status: 503, json: { error: 'Session service unavailable' } }));
  await page.getByRole('button', { name: 'Refresh identity', exact: true }).click();
  await expect(page.getByText('Unable to load session identity', { exact: true })).toBeVisible();
  await expect(page.getByRole('main').getByText(/Session service unavailable/)).toBeVisible();
});

test('Session identity uses listed role ARNs with paths and updates global identity and expiry', async ({ page }) => {
  await page.route('**/api/session-identity/', route => route.fulfill({ json: sessionFixture() }));
  await page.route('**/api/session-identity/options/', route => route.fulfill({ json: identityOptions }));
  let request: any;
  const expires = new Date(Date.now() + 3600_000).toISOString();
  await page.route('**/api/session-identity/assume-role/', async route => {
    request = route.request().postDataJSON();
    await route.fulfill({ json: { ...sessionFixture('role-session'), caller_identity: { account: '123456789012', user_id: 'role-session', arn: 'arn:aws:sts::123456789012:assumed-role/AppRole/review-session' }, session_identity: { type: 'assumed_role', label: 'AppRole/review-session', expires_at: expires, has_session_token: true }, changed: true } });
  });
  await page.goto('/environment/role');
  await page.getByRole('combobox', { name: 'Role ARN', exact: true }).fill('AppRole');
  await page.getByRole('option', { name: /^AppRole / }).click();
  await page.getByRole('textbox', { name: 'Role session name', exact: true }).fill('review-session');
  await expect(page.getByText('team/application/AppRole', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Switch role', exact: true }).click();
  await expect(page.getByRole('button', { name: 'role-session @ 123456789012', exact: true })).toBeVisible();
  await expect(page.getByText('Assumed role', { exact: true })).toBeVisible();
  expect(request).toEqual({ role_name: 'AppRole', session_name: 'review-session', account_id: '123456789012', role_arn: 'arn:aws:iam::123456789012:role/team/application/AppRole' });
  await page.getByRole('tab', { name: 'IAM user', exact: true }).click();
  await expect(page).toHaveURL(/environment\/user$/);
});

test('Session identity permits manual user entry when listing fails and confirms replacing all keys', async ({ page }) => {
  await page.route('**/api/session-identity/', route => route.fulfill({ json: sessionFixture() }));
  await page.route('**/api/session-identity/options/', route => route.fulfill({ json: { users: [], roles: [], errors: { users: 'ListUsers denied' } } }));
  const requests: any[] = [];
  await page.route('**/api/session-identity/use-user/', async route => { requests.push(route.request().postDataJSON()); await route.fulfill({ json: { ...sessionFixture('bob'), changed: true, session_identity: { type: 'user', label: 'bob' } } }); });
  await page.goto('/environment/user');
  await expect(page.getByText('User list unavailable', { exact: true })).toBeVisible();
  await page.getByRole('combobox', { name: 'IAM user name', exact: true }).fill('bob');
  await page.getByRole('checkbox', { name: 'Replace all existing access keys for this user', exact: true }).check();
  await page.getByRole('button', { name: 'Switch user', exact: true }).click();
  expect(requests).toHaveLength(0);
  await expect(page.getByRole('dialog')).toContainText('All existing access keys for bob will be deleted');
  await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(requests).toHaveLength(0);
  await page.getByRole('button', { name: 'Switch user', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Replace keys and switch user', exact: true }).click();
  await expect(page.getByRole('button', { name: 'bob @ 123456789012', exact: true })).toBeVisible();
  expect(requests).toEqual([{ user_name: 'bob', rotate_access_keys: true }]);
});

test('Session identity reports expired sessions and restores configured credentials without admin claims', async ({ page }) => {
  const role = { ...sessionFixture('expired-role'), session_identity: { type: 'assumed_role', label: 'AppRole/review', expires_at: '2000-01-01T00:00:00Z', has_session_token: true, secret_access_key: 'NEVER-SHOW-SECRET', session_token: 'NEVER-SHOW-TOKEN' } };
  await page.route('**/api/session-identity/', route => route.fulfill({ json: role }));
  let restores = 0;
  await page.route('**/api/session-identity/use-admin/', async route => { restores++; await route.fulfill({ json: { ...sessionFixture('configured-user'), changed: true } }); });
  await page.goto('/environment/configured');
  await expect(page.getByText('Session expired', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Session metadata', exact: true }).click();
  await expect(page.getByText(/NEVER-SHOW-SECRET/)).toHaveCount(0);
  await expect(page.getByText(/NEVER-SHOW-TOKEN/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Use configured credentials', exact: true }).click();
  await expect(page.getByRole('button', { name: 'configured-user @ 123456789012', exact: true })).toBeVisible();
  await expect(page.getByText('Session expired', { exact: true })).toHaveCount(0);
  expect(restores).toBe(1);
});

test('Session identity ignores older refresh responses after a newer session change', async ({ page }) => {
  let delayed = false;
  await page.route('**/api/session-identity/', async route => {
    if (delayed) await new Promise(resolve => setTimeout(resolve, 350));
    await route.fulfill({ json: sessionFixture('old-user') });
  });
  await page.goto('/environment/');
  await expect(page.getByRole('button', { name: 'old-user @ 123456789012', exact: true })).toBeVisible();
  delayed = true;
  const staleResponse = page.waitForResponse(response => response.url().endsWith('/api/session-identity/'));
  await page.evaluate(() => window.dispatchEvent(new Event('floci_session_changed')));
  await page.evaluate(payload => window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: payload })), sessionFixture('new-user'));
  await staleResponse;
  await expect(page.getByRole('button', { name: 'new-user @ 123456789012', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'old-user @ 123456789012', exact: true })).toHaveCount(0);
});

const cliResponse = (stdout = '{"Account":"123456789012"}\n', stderr = '', exitCode = 0) => ({
  command: 'aws sts get-caller-identity', exit_code: exitCode, stdout, stderr, duration_ms: 18,
  endpoint_url: 'http://local-floci:4566', region: 'eu-west-1', credential_source: 'environment',
  executed_args: ['aws', '--endpoint-url', 'http://local-floci:4566', 'sts', 'get-caller-identity'],
});

test('CLI shows actual exit status, timing and both output streams', async ({ page }) => {
  await page.route('**/api/console/run/', route => route.fulfill({ json: cliResponse('{"partial":true}\n', 'An operation failed\n', 7) }));
  await page.goto('/console/');
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  await expect(page.locator('pre[aria-label="stdout"]')).toHaveText('{"partial":true}\n');
  await expect(page.locator('pre[aria-label="stderr"]')).toHaveText('An operation failed\n');
  await expect(page.getByText('Exit code 7', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('18 ms', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Execution details', exact: true }).click();
  await expect(page.locator('pre[aria-label="Executed command"]')).toContainText('--endpoint-url http://local-floci:4566');
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screenshots/cli-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screenshots/cli-mobile.png', fullPage: true });
});

test('CLI handles real 409 confirmation responses and runs the frozen command once', async ({ page }) => {
  const requests: any[] = [];
  await page.route('**/api/console/run/', async route => {
    const body = route.request().postDataJSON(); requests.push(body);
    if (!body.confirmed) return route.fulfill({ status: 409, json: { requires_confirmation: true, command: body.command, message: 'This command removes a local bucket.' } });
    await new Promise(resolve => setTimeout(resolve, 100));
    return route.fulfill({ json: { ...cliResponse('', ''), command: body.command } });
  });
  await page.goto('/console/');
  const input = page.getByRole('textbox', { name: 'AWS CLI command', exact: true });
  await input.fill('aws s3 rb s3://test-bucket');
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('This command removes a local bucket.');
  await expect(input).toBeDisabled();
  await expect(dialog.locator('pre')).toHaveText('aws s3 rb s3://test-bucket');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(requests).toEqual([{ command: 'aws s3 rb s3://test-bucket', confirmed: false }]);
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  await dialog.getByRole('button', { name: 'Confirm and run', exact: true }).evaluate((element: HTMLButtonElement) => { element.click(); element.click(); });
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText('Exit code 0', { exact: true }).first()).toBeVisible();
  expect(requests).toHaveLength(3);
  expect(requests[2]).toEqual({ command: 'aws s3 rb s3://test-bucket', confirmed: true });
});

test('CLI presets and history recall load commands without executing them', async ({ page }) => {
  const commands: string[] = [];
  await page.route('**/api/console/run/', async route => { commands.push(route.request().postDataJSON().command); await route.fulfill({ json: cliResponse('bucket-list\n') }); });
  await page.goto('/console/');
  await page.getByRole('button', { name: /^Command presets / }).click();
  await page.getByRole('option', { name: 'S3 · List buckets', exact: true }).click();
  const input = page.getByRole('textbox', { name: 'AWS CLI command', exact: true });
  await expect(input).toHaveValue('aws s3 ls');
  expect(commands).toHaveLength(0);
  await input.press('Control+Enter');
  await expect(page.locator('pre[aria-label="stdout"]')).toHaveText('bucket-list\n');
  await input.fill('aws sqs list-queues');
  await input.press('Alt+ArrowUp');
  await expect(input).toHaveValue('aws s3 ls');
  await input.press('Alt+ArrowDown');
  await expect(input).toHaveValue('aws sqs list-queues');
  await page.getByRole('button', { name: 'Load command', exact: true }).click();
  await expect(input).toHaveValue('aws s3 ls');
  expect(commands).toEqual(['aws s3 ls']);
  await page.getByRole('button', { name: 'Manage session', exact: true }).click();
  await page.goBack();
  await expect(page.getByRole('table')).toContainText('aws s3 ls');
  expect(commands).toHaveLength(1);
  await page.getByRole('button', { name: 'Clear history', exact: true }).click();
  await expect(page.getByText('No commands run in this session context yet.', { exact: true })).toBeVisible();
  await expect(page.locator('pre[aria-label="stdout"]')).toHaveCount(0);
});

test('CLI formats JSON primitives, preserves empty output, and reports truncation and request errors', async ({ page }) => {
  let output = 'false'; let truncate = false; let fail = false;
  await page.route('**/api/console/run/', route => fail ? route.fulfill({ status: 408, json: { error: 'Command timed out after 30 seconds.' } }) : route.fulfill({ json: { ...cliResponse(output), stdout_truncated: truncate, output_limit: 100000 } }));
  await page.goto('/console/');
  for (const value of ['false', '0', '[]']) {
    output = value;
    await page.getByRole('button', { name: 'Run command', exact: true }).click();
    await page.getByRole('tab', { name: 'Formatted JSON', exact: true }).click();
    await expect(page.locator('pre[aria-label="JSON output"]')).toHaveText(value);
  }
  truncate = true; output = '[1]';
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  await expect(page.getByText(/Output was truncated/)).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Formatted JSON', exact: true })).toHaveCount(0);
  await page.getByRole('checkbox', { name: 'Wrap output lines', exact: true }).uncheck();
  await expect(page.locator('pre[aria-label="stdout"]')).toHaveCSS('white-space', 'pre');
  output = ''; truncate = false;
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  await expect(page.getByText('No standard output returned.', { exact: true })).toBeVisible();
  fail = true;
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  await expect(page.getByText('Command request failed', { exact: true })).toBeVisible();
  await expect(page.getByText('Command timed out after 30 seconds.', { exact: true })).toBeVisible();
});

test('CLI prevents repeated keyboard submission and isolates history after a session change', async ({ page }) => {
  let count = 0;
  await page.route('**/api/console/run/', async route => { count++; await new Promise(resolve => setTimeout(resolve, 150)); await route.fulfill({ json: cliResponse('old-session-output') }); });
  await page.goto('/console/');
  const input = page.getByRole('textbox', { name: 'AWS CLI command', exact: true });
  await input.press('Control+Enter');
  await input.press('Control+Enter');
  await expect(page.locator('pre[aria-label="stdout"]')).toHaveText('old-session-output');
  expect(count).toBe(1);
  await page.evaluate(payload => window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: payload })), sessionFixture('different-user'));
  await expect(page.getByRole('button', { name: 'different-user @ 123456789012', exact: true })).toBeVisible();
  await expect(page.getByText('No commands run in this session context yet.', { exact: true })).toBeVisible();
  await expect(page.getByText('old-session-output', { exact: true })).toHaveCount(0);
});

test('CLI ignores late output from a command submitted under a previous session', async ({ page }) => {
  await page.route('**/api/console/run/', async route => { await new Promise(resolve => setTimeout(resolve, 400)); await route.fulfill({ json: cliResponse('late-private-output') }); });
  await page.goto('/console/');
  const completed = page.waitForResponse(response => response.url().endsWith('/api/console/run/'));
  await page.getByRole('button', { name: 'Run command', exact: true }).click();
  await page.evaluate(payload => window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: payload })), sessionFixture('new-cli-user'));
  await completed;
  await expect(page.getByRole('button', { name: 'new-cli-user @ 123456789012', exact: true })).toBeVisible();
  await expect(page.getByText('late-private-output', { exact: true })).toHaveCount(0);
  await expect(page.getByText('No commands run in this session context yet.', { exact: true })).toBeVisible();
});

const polishedSecret = {
  name: 'production/database/credentials', arn: 'arn:aws:secretsmanager:eu-west-1:123456789012:secret:production/database/credentials-AbCdEf',
  description: 'Credentials used by the production application', kms_key_id: 'alias/application-secrets',
  created: '2026-10-01T12:00:00Z', last_changed: '2026-10-20T12:00:00Z', rotation_enabled: true,
  rotation_lambda_arn: 'arn:aws:lambda:eu-west-1:123456789012:function:rotate-database',
  rotation_rules: { AutomaticallyAfterDays: 30, Duration: '2h' }, next_rotation: '2026-11-19T12:00:00Z',
  tags: [{ Key: 'environment', Value: 'production' }],
  versions: [{ VersionId: 'version-current', VersionStages: ['AWSCURRENT'], CreatedDate: '2026-10-20T12:00:00Z' },
    { VersionId: 'version-previous', VersionStages: ['AWSPREVIOUS'], CreatedDate: '2026-10-01T12:00:00Z' }],
};
const secretDetailsUrl = `/secretsmanager/details?secret=${encodeURIComponent(polishedSecret.name)}`;

test('Secrets Manager metadata, lifecycle, rotation and versions use real inventory fields', async ({ page }) => {
  let reveals = 0;
  await inventory(page, 'secretsmanager', { secrets: [polishedSecret, { name: 'pending-cleanup', deleted: '2026-10-27T12:00:00Z', rotation_enabled: false }] });
  await page.route('**/api/secretsmanager/secrets/**/value/**', route => { reveals++; return route.fulfill({ json: {} }); });
  await page.goto('/secretsmanager/');
  await expect(page.getByText('Active Credentials', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: polishedSecret.name, exact: true }).click();
  await expect(page).toHaveURL(new RegExp('details\\?secret='));
  await expect(page.getByText('alias/application-secrets', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: `Secret: ${polishedSecret.name}`, exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Rotation', exact: true }).click();
  await expect(page.getByText('30 days', { exact: true })).toBeVisible();
  await expect(page.getByText(polishedSecret.rotation_lambda_arn, { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Versions (2)', exact: true }).click();
  await expect(page.getByText('AWSPREVIOUS', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Tags (1)', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'production', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Secret details', exact: true })).toBeVisible();
  expect(reveals).toBe(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screenshots/secretsmanager-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screenshots/secretsmanager-mobile.png', fullPage: true });
});

test('Secrets Manager explicitly retrieves and edits values through dedicated endpoints', async ({ page }) => {
  const requests: { method: string; payload?: any; url: string }[] = [];
  await inventory(page, 'secretsmanager', { secrets: [polishedSecret] });
  await page.route('**/api/secretsmanager/secrets/**/value/**', async route => {
    requests.push({ method: route.request().method(), payload: route.request().postDataJSON(), url: route.request().url() });
    return route.fulfill({ json: route.request().method() === 'GET' ? {
      type: 'string', value: '{"username":"dbadmin","password":"current-secret"}', size_bytes: 49,
      version_id: 'version-current', version_stages: ['AWSCURRENT'],
    } : { version_id: 'version-new' } });
  });
  await page.goto(secretDetailsUrl);
  await page.getByRole('button', { name: 'Retrieve secret value', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.locator('pre')).toContainText('current-secret');
  await dialog.getByRole('checkbox', { name: 'Format JSON', exact: true }).check();
  await expect(dialog.locator('pre')).toContainText('"username": "dbadmin"');
  await dialog.getByRole('button', { name: 'Edit value', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'New secret value', exact: true }).fill('replacement-secret');
  await dialog.getByRole('button', { name: 'Save new version', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests.map(r => r.method)).toEqual(['GET', 'PUT']);
  expect(requests[1].payload).toEqual({ value: 'replacement-secret' });
  expect(requests[0].url).toContain(encodeURIComponent(polishedSecret.arn));
  await expect(page.getByText('Stored a new version of production/database/credentials.', { exact: true })).toBeVisible();
  await expect(page.getByText('replacement-secret', { exact: true })).toHaveCount(0);
});

test('Secrets Manager retrieves specific binary versions and handles value errors without fake values', async ({ page }) => {
  let fail = false;
  await inventory(page, 'secretsmanager', { secrets: [polishedSecret] });
  await page.route('**/api/secretsmanager/secrets/**/value/**', route => {
    if (fail) return route.fulfill({ status: 403, json: { error: 'Access denied to this version' } });
    expect(new URL(route.request().url()).searchParams.get('version_id')).toBe('version-previous');
    return route.fulfill({ json: { type: 'binary', value: 'AP8=', size_bytes: 2, version_id: 'version-previous', version_stages: ['AWSPREVIOUS'] } });
  });
  await page.goto(secretDetailsUrl);
  await page.getByRole('tab', { name: 'Versions (2)', exact: true }).click();
  await page.getByRole('row').filter({ hasText: 'version-previous' }).getByRole('button', { name: 'Retrieve version' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Binary (base64)', { exact: true })).toBeVisible();
  await expect(dialog.locator('pre')).toHaveText('AP8=');
  await expect(dialog.getByRole('button', { name: 'Edit value', exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  fail = true;
  await page.getByRole('row').filter({ hasText: 'version-current' }).getByRole('button', { name: 'Retrieve version' }).click();
  await expect(dialog.getByText('Access denied to this version', { exact: true })).toBeVisible();
  await expect(dialog.locator('pre')).toHaveCount(0);
});

test('Secrets Manager creates blank-default secrets and edits metadata without a fake action dispatch', async ({ page }) => {
  const requests: any[] = [];
  await inventory(page, 'secretsmanager', { secrets: [polishedSecret] });
  await page.route('**/api/secretsmanager/secrets/', route => { requests.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); });
  await page.route('**/api/secretsmanager/secrets/**/metadata/', route => { requests.push(route.request().postDataJSON()); expect(route.request().method()).toBe('PATCH'); return route.fulfill({ json: {} }); });
  await page.goto('/secretsmanager/');
  await page.getByRole('button', { name: 'Store a new secret', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('textbox', { name: 'Secret value', exact: true })).toHaveValue('');
  await expect(dialog.getByRole('button', { name: 'Store secret', exact: true })).toBeDisabled();
  await dialog.getByRole('textbox', { name: 'Secret name', exact: true }).fill('new/application');
  await dialog.getByRole('textbox', { name: 'Secret value', exact: true }).fill('false');
  await dialog.getByRole('button', { name: 'Store secret', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[0]).toEqual({ name: 'new/application', value: 'false', description: '', kms_key_id: '' });
  await page.goto(secretDetailsUrl);
  await page.getByRole('button', { name: 'Edit details', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill('Updated description');
  await expect(dialog.getByRole('textbox', { name: 'Encryption key', exact: true })).toHaveValue('alias/application-secrets');
  await dialog.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[1]).toEqual({ description: 'Updated description', kms_key_id: 'alias/application-secrets' });
});

test('Secrets Manager schedules recoverable deletion and can cancel deletion', async ({ page }) => {
  const requests: any[] = [];
  let deleted = false;
  await page.route('**/api/secretsmanager/', route => route.fulfill({ json: { secrets: [{ ...polishedSecret, deleted: deleted ? '2026-10-27T12:00:00Z' : undefined }] } }));
  await page.route('**/api/secretsmanager/secrets/**/value/', route => {
    expect(route.request().method()).toBe('DELETE'); requests.push(route.request().postDataJSON()); deleted = true;
    return route.fulfill({ json: { deletion_date: '2026-10-27T12:00:00Z' } });
  });
  await page.route('**/api/secretsmanager/secrets/**/restore/', route => { expect(route.request().method()).toBe('POST'); deleted = false; return route.fulfill({ json: { restored: true } }); });
  await page.goto(secretDetailsUrl);
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('checkbox')).not.toBeChecked();
  await dialog.getByRole('button', { name: 'Schedule deletion', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests).toEqual([{ recovery_window_days: 7, force_delete_without_recovery: false }]);
  await page.getByRole('button', { name: polishedSecret.name, exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retrieve secret value', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Cancel deletion', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Retrieve secret value', exact: true })).toBeEnabled();
});

test('Secrets Manager ignores late retrievals after the value dialog closes', async ({ page }) => {
  let finish: (() => void) | undefined;
  let started: (() => void) | undefined;
  const pending = new Promise<void>(resolve => { started = resolve; });
  await inventory(page, 'secretsmanager', { secrets: [polishedSecret] });
  await page.route('**/api/secretsmanager/secrets/**/value/', async route => {
    started?.(); await new Promise<void>(resolve => { finish = resolve; });
    return route.fulfill({ json: { type: 'string', value: 'late-secret', version_id: 'version-current' } });
  });
  await page.goto(secretDetailsUrl);
  await page.getByRole('button', { name: 'Retrieve secret value', exact: true }).click();
  await pending;
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
  finish?.();
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('late-secret', { exact: true })).toHaveCount(0);
});

test('Secrets Manager clears revealed values when the session changes and displays inventory failures', async ({ page }) => {
  let fail = false;
  await page.route('**/api/secretsmanager/', route => fail
    ? route.fulfill({ status: 502, json: { error: 'Inventory unavailable' } })
    : route.fulfill({ json: { secrets: [polishedSecret] } }));
  await page.route('**/api/secretsmanager/secrets/**/value/', route => route.fulfill({ json: {
    type: 'string', value: '0', version_id: 'version-current', version_stages: ['AWSCURRENT'], size_bytes: 1,
  } }));
  await page.goto(secretDetailsUrl);
  await page.getByRole('button', { name: 'Retrieve secret value', exact: true }).click();
  await expect(page.getByRole('dialog').locator('pre')).toHaveText('0');
  await page.getByRole('dialog').getByRole('checkbox', { name: 'Format JSON', exact: true }).check();
  await expect(page.getByRole('dialog').locator('pre')).toHaveText('0');
  await page.evaluate(payload => window.dispatchEvent(new CustomEvent('floci_session_changed', { detail: payload })), sessionFixture('new-secret-user'));
  await expect(page.getByRole('dialog')).toHaveCount(0);
  fail = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('Unable to load secrets', { exact: true })).toBeVisible();
  await expect(page.getByText('Inventory unavailable', { exact: true })).toBeVisible();
});

const polishedAlarm = {
  AlarmName: 'Payments failure rate', AlarmArn: 'arn:aws:cloudwatch:eu-west-1:123456789012:alarm:Payments failure rate',
  AlarmDescription: 'Detect failed payment requests', Namespace: 'Payments/API', MetricName: 'FailedRequests',
  Dimensions: [{ Name: 'Environment', Value: 'production' }], StateValue: 'ALARM',
  StateReason: '2 of the last 3 datapoints were at or above the threshold.', StateUpdatedTimestamp: '2026-10-20T12:30:00Z',
  ComparisonOperator: 'GreaterThanOrEqualToThreshold', Threshold: 0, Statistic: 'Sum', Period: 60,
  EvaluationPeriods: 3, DatapointsToAlarm: 2, TreatMissingData: 'notBreaching', ActionsEnabled: true,
  AlarmActions: ['arn:aws:sns:eu-west-1:123456789012:payments-alerts'],
};

test('CloudWatch renders true alarm conditions, unknown states and state filters', async ({ page }) => {
  await inventory(page, 'cloudwatch', { alarms: [polishedAlarm,
    { AlarmName: 'Low throughput', StateValue: 'OK', ComparisonOperator: 'LessThanThreshold', Threshold: 10 },
    { AlarmName: 'Maximum latency', StateValue: 'INSUFFICIENT_DATA', ComparisonOperator: 'LessThanOrEqualToThreshold', Threshold: 100 },
    { AlarmName: 'Unknown state', ComparisonOperator: 'GreaterThanUpperThreshold' },
  ] });
  await page.goto('/cloudwatch/');
  await expect(page.getByRole('cell', { name: '≥ 0', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '< 10', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: '≤ 100', exact: true })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'Above anomaly detection band', exact: true })).toBeVisible();
  await expect(page.getByRole('row').filter({ hasText: 'Unknown state' }).getByText('Not reported', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'OK', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Low throughput', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: polishedAlarm.AlarmName, exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'All alarms', exact: true }).click();
  await page.getByRole('button', { name: polishedAlarm.AlarmName, exact: true }).click();
  await expect(page).toHaveURL(/alarm=Payments/);
  await expect(page.getByText('2 of 3', { exact: true })).toBeVisible();
  await expect(page.getByText(polishedAlarm.StateReason, { exact: true })).toBeVisible();
  await expect(page.getByText('Environment = production', { exact: true })).toBeVisible();
  await expect(page.getByText(polishedAlarm.AlarmActions[0], { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screenshots/cloudwatch-alarms-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: 'test-results/screenshots/cloudwatch-alarms-mobile.png', fullPage: true });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Alarm details', exact: true })).toBeVisible();
});

test('CloudWatch alarm creation preserves zero thresholds and configurable evaluation settings', async ({ page }) => {
  const requests: any[] = [];
  await inventory(page, 'cloudwatch', {});
  await page.route('**/api/cloudwatch/alarms/', route => { requests.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); });
  await page.goto('/cloudwatch/');
  await page.getByRole('button', { name: 'Create Alarm', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox', { name: 'Alarm Name', exact: true }).fill('Zero threshold');
  await dialog.getByRole('spinbutton', { name: 'Threshold (Value)', exact: true }).fill('0');
  await dialog.getByRole('spinbutton', { name: 'Evaluation periods', exact: true }).fill('3');
  await dialog.getByRole('spinbutton', { name: 'Datapoints to alarm', exact: true }).fill('2');
  await dialog.getByRole('textbox', { name: 'Dimensions (JSON)', exact: true }).fill('[{"Name":"Service","Value":"worker"}]');
  await dialog.getByRole('button', { name: 'Create Alarm', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[0]).toMatchObject({ threshold: 0, evaluation_periods: 3, datapoints_to_alarm: 2, dimensions: [{ Name: 'Service', Value: 'worker' }] });
});

test('CloudWatch requires explicit state and delete operations and keeps failures visible', async ({ page }) => {
  const requests: any[] = [];
  let fail = true;
  await inventory(page, 'cloudwatch', { alarms: [polishedAlarm] });
  await page.route('**/api/cloudwatch/alarms/state/', route => { requests.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); });
  await page.route('**/api/cloudwatch/alarms/', route => {
    expect(route.request().method()).toBe('DELETE');
    requests.push(route.request().postDataJSON());
    return route.fulfill(fail ? { status: 403, json: { error: 'Alarm deletion denied' } } : { json: {} });
  });
  await page.goto(`/cloudwatch/alarms?alarm=${encodeURIComponent(polishedAlarm.AlarmName)}`);
  await page.getByRole('button', { name: 'Set state for testing', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: /^State / }).click();
  await page.getByRole('option', { name: 'INSUFFICIENT_DATA', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'State reason', exact: true }).fill('Testing missing observations');
  await dialog.getByRole('button', { name: 'Set state', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[0]).toEqual({ alarm_name: polishedAlarm.AlarmName, state_value: 'INSUFFICIENT_DATA', state_reason: 'Testing missing observations' });
  await page.getByRole('button', { name: 'Delete alarm', exact: true }).click();
  expect(requests).toHaveLength(1);
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(requests).toHaveLength(1);
  await page.getByRole('button', { name: 'Delete alarm', exact: true }).click();
  await dialog.getByRole('button', { name: 'Delete alarm', exact: true }).click();
  await expect(dialog.getByText('Alarm deletion denied', { exact: true })).toBeVisible();
  fail = false;
  await dialog.getByRole('button', { name: 'Delete alarm', exact: true }).click();
  await expect(dialog).toHaveCount(0);
});

test('CloudWatch uses log group metadata and removes retention for Never expire', async ({ page }) => {
  const requests: any[] = [];
  await inventory(page, 'cloudwatch', { log_groups: [{ name: '/app/worker', retention_days: 7, stored_bytes: 1024, arn: 'arn:aws:logs:eu-west-1:123456789012:log-group:/app/worker' }] });
  await page.route('**/api/cloudwatch/log-streams/', route => route.fulfill({ json: { streams: [] } }));
  await page.route('**/api/cloudwatch/retention/', route => { requests.push({ method: route.request().method(), ...route.request().postDataJSON() }); return route.fulfill({ json: {} }); });
  await page.route('**/api/cloudwatch/log-streams/create/', route => { requests.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); });
  await page.goto('/cloudwatch/logs');
  await expect(page.getByText('7 days', { exact: true })).toBeVisible();
  await expect(page.getByText('1024', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Retention Settings', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: /^Retention Period / }).click();
  await page.getByRole('option', { name: 'Never expire', exact: true }).click();
  await dialog.getByRole('button', { name: 'Save Retention', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[0]).toEqual({ method: 'DELETE', log_group_name: '/app/worker' });
  await page.getByRole('button', { name: 'Create Log Stream', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Log Stream Name', exact: true }).fill('manual-test-stream');
  await dialog.getByRole('button', { name: 'Create Stream', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[1]).toEqual({ log_group_name: '/app/worker', log_stream_name: 'manual-test-stream' });
});

test('CloudWatch metric statistic selection requests real Sum samples and publishes zero', async ({ page }) => {
  const requests: URL[] = [];
  let published: any;
  await inventory(page, 'cloudwatch', { metrics: [{ Namespace: 'App', MetricName: 'Errors' }] });
  await page.route('**/api/cloudwatch/metric-statistics/**', route => { requests.push(new URL(route.request().url())); return route.fulfill({ json: { datapoints: [{ Timestamp: '2026-10-20T12:00:00Z', Average: 0, Sum: 0 }] } }); });
  await page.route('**/api/cloudwatch/metric-data/', route => { published = route.request().postDataJSON(); return route.fulfill({ json: {} }); });
  await page.goto('/cloudwatch/metrics');
  await page.getByRole('button', { name: /^Statistic / }).click();
  await page.getByRole('option', { name: 'Sum', exact: true }).click();
  await expect.poll(() => requests.at(-1)?.searchParams.get('statistic')).toBe('Sum');
  await page.getByRole('button', { name: 'Publish Custom Metric Data', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Value', exact: true }).fill('0');
  await page.getByRole('button', { name: 'Publish Metric Data', exact: true }).click();
  await expect.poll(() => published?.value).toBe(0);
  await expect(page.getByRole('button', { name: /^Metric / })).toContainText('App / Errors');
});
