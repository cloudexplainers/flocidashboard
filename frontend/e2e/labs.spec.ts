import { test, expect, Page } from '@playwright/test';
import { FLAT_CURRICULUM_LABS } from '../src/labCurriculum';

async function mockProgress(page: Page) {
  await page.route('**/api/labs/progress/', route => route.fulfill({ json: { labs: [] } }));
  await page.route('**/api/labs/*/*/status/', route => route.fulfill({ json: { complete: false, steps: {} } }));
}

test.beforeEach(async ({ page }) => { await mockProgress(page); });

test('catalog and curriculum cover the same labs and step counts', async ({ request }) => {
  const response = await request.get('/api/labs/catalog/');
  expect(response.ok()).toBeTruthy();
  const catalog = await response.json();
  expect(catalog.total_labs).toBe(FLAT_CURRICULUM_LABS.length);
  for (const lesson of FLAT_CURRICULUM_LABS) {
    const lab = catalog.labs.find((item: any) => item.service === lesson.service && item.key === lesson.key);
    expect(lab, `${lesson.service}/${lesson.key}`).toBeTruthy();
    expect(lab.steps).toHaveLength(lesson.stepCount);
    for (const step of lab.steps) {
      expect(step.command).toBeTruthy();
      expect(step.snippets.boto3).toBeTruthy();
      expect(step.snippets.terraform).toBeTruthy();
    }
  }
});

for (const lesson of FLAT_CURRICULUM_LABS) {
  test(`lesson ${lesson.id}: ${lesson.service}/${lesson.key} renders and switches SDKs`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/labs/?service=${lesson.service}&lab=${lesson.key}`);
    await expect(page.getByText(`Lesson #${lesson.id} of ${FLAT_CURRICULUM_LABS.length}`, { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Run step', exact: true })).toHaveCount(lesson.stepCount);
    const python = page.getByRole('tab', { name: 'Python (boto3)', exact: true }).first();
    await python.click();
    await expect(python).toHaveAttribute('aria-selected', 'true');
    const terraform = page.getByRole('tab', { name: 'Terraform (HCL)', exact: true }).first();
    await terraform.click();
    await expect(terraform).toHaveAttribute('aria-selected', 'true');
    expect(errors).toEqual([]);
  });
}

test('directory search and empty results', async ({ page }) => {
  await page.goto('/labs/');
  const filter = page.getByPlaceholder(/^Filter labs by number/);
  await filter.fill('no-such-lab');
  await expect(page.getByText('No matching labs found')).toBeVisible();
  await filter.fill('S3');
  await expect(page.getByRole('button', { name: 'Start Lab Lesson' }).first()).toBeVisible();
  await filter.fill('SAA 4.4');
  await expect(page.getByRole('button', { name: 'Start Lab Lesson' })).toHaveCount(3);
});

test('catalog tags every lab with valid exam tasks', async ({ request }) => {
  const catalog = await (await request.get('/api/labs/catalog/')).json();
  const exams = await (await request.get('/api/labs/exams/')).json();
  const tasks = new Set<string>(
    exams.exams.flatMap((exam: any) => exam.domains.flatMap((domain: any) => domain.tasks.map((task: any) => `${exam.code} ${task.id}`))),
  );
  for (const lab of catalog.labs) {
    expect(Array.isArray(lab.exams), `${lab.service}/${lab.key}`).toBeTruthy();
    for (const tag of lab.exams) expect(tasks.has(`${tag.exam} ${tag.task}`)).toBeTruthy();
  }
});

test('SAA study track groups labs by domain and opens labs from tasks', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/labs/?track=SAA-C03');
  await expect(page.getByText('SAA-C03 · AWS Certified Solutions Architect – Associate')).toBeVisible();
  await expect(page.getByText('30% of scored content · 0 of', { exact: false })).toBeVisible();

  await page.getByText('Domain 3: Design High-Performing Architectures', { exact: true }).click();
  const ingestion = page.getByTestId('exam-task-SAA-C03-3.5');
  await expect(ingestion.getByRole('button', { name: 'Stream clickstream events into an S3 data lake and query them with Athena' })).toBeVisible();
  await page.getByText('Domain 4: Design Cost-Optimized Architectures', { exact: true }).click();
  const databaseCost = page.getByTestId('exam-task-SAA-C03-4.3');
  await expect(databaseCost.getByRole('button', { name: 'Run DynamoDB at scale with capacity planning, auto scaling, TTL, streams, and global tables' })).toBeVisible();
  await databaseCost.getByText('Concept: Billing, budgets, and cost management tools').click();
  await expect(databaseCost.getByText(/Planned lab: W3-6/)).toBeVisible();

  const secureAccess = page.getByTestId('exam-task-SAA-C03-1.1');
  await secureAccess.getByRole('button', { name: 'Create a role with a trust policy' }).click();
  await expect(page).toHaveURL(/lab=role-trust-policy/);
  await expect(page.getByText('SAA 1.1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '← Back to Labs Directory' }).click();
  await expect(page).toHaveURL(/track=SAA-C03/);
  await expect(page.getByText('SAA-C03 · AWS Certified Solutions Architect – Associate')).toBeVisible();
  expect(errors).toEqual([]);
});

test('exam progress is weighted by domain', async ({ page, request }) => {
  const exams = await (await request.get('/api/labs/exams/')).json();
  const saa = exams.exams.find((exam: any) => exam.code === 'SAA-C03');
  const labsIn = (domain: any) => new Set<string>(domain.tasks.flatMap((task: any) => task.labs.map((lab: any) => `${lab.service}:${lab.key}`)));
  const completed = labsIn(saa.domains.find((domain: any) => domain.id === '1'));
  // Labs can practice tasks in several domains, so completing Domain 1 also moves other domains.
  const expected = Math.round(
    saa.domains.reduce((sum: number, domain: any) => {
      const labs = [...labsIn(domain)];
      return labs.length ? sum + domain.weight * (labs.filter((id) => completed.has(id)).length / labs.length) : sum;
    }, 0),
  );
  expect(expected).toBeGreaterThanOrEqual(30);
  expect(expected).toBeLessThan(100);
  const labs = [...completed].map((id) => ({ service: id.split(':')[0], key: id.split(':')[1], complete: true }));
  await page.route('**/api/labs/progress/', route => route.fulfill({ json: { labs } }));
  await page.goto('/labs/?track=SAA-C03');
  await expect(page.getByText(`${labsIn(saa.domains[0]).size} of ${labsIn(saa.domains[0]).size} labs complete`, { exact: false })).toBeVisible();
  await expect(page.getByLabel('Exam-weighted lab progress')).toHaveAttribute('value', String(expected));
});

test('study track choice switches views and unknown tracks fall back to levels', async ({ page }) => {
  await page.goto('/labs/?track=NOT-AN-EXAM');
  await expect(page.getByPlaceholder(/^Filter labs by number/)).toBeVisible();
  await page.getByText('CLF-C02 Cloud Practitioner', { exact: true }).click();
  await expect(page).toHaveURL(/track=CLF-C02/);
  await expect(page.getByText('CLF-C02 · AWS Certified Cloud Practitioner')).toBeVisible();
  await page.getByText('Levels', { exact: true }).click();
  await expect(page).not.toHaveURL(/track=/);
  await expect(page.getByPlaceholder(/^Filter labs by number/)).toBeVisible();
});

test('Run all stops on execution errors and permits retry', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/labs/iam/create-admin-user/steps/*/run/', route => {
    calls++;
    return route.fulfill({ status: 400, json: { error: 'Test permission denied' } });
  });
  await page.goto('/labs/?service=iam&lab=create-admin-user');
  await page.getByRole('button', { name: '▶ Run all remaining steps', exact: true }).click();
  await expect(page.getByText('Execution Result: Failed')).toBeVisible();
  await expect(page.getByText(/Run all stopped/)).toBeVisible();
  expect(calls).toBe(1);
  await expect(page.getByRole('button', { name: 'Run step', exact: true }).first()).toBeEnabled();
});

test('Run all stops when a successful request cannot be verified', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/labs/iam/create-admin-user/steps/*/run/', route => {
    calls++;
    return route.fulfill({ json: { verified: false, verification: { message: 'Not ready yet' } } });
  });
  await page.goto('/labs/?service=iam&lab=create-admin-user');
  await page.getByRole('button', { name: '▶ Run all remaining steps', exact: true }).click();
  await expect(page.getByText('Execution Result: Not verified')).toBeVisible();
  await expect(page.getByText(/Run all stopped/)).toBeVisible();
  expect(calls).toBe(1);
});

test('verified step completes and reset clears progress', async ({ page }) => {
  await page.route('**/api/labs/iam/create-user-alice/steps/create-user/run/', route => route.fulfill({ json: { verified: true, stdout: 'Alice created' } }));
  await page.route('**/api/labs/iam/create-user-alice/reset/', route => route.fulfill({ json: { reset: true } }));
  await page.goto('/labs/?service=iam&lab=create-user-alice');
  await page.getByRole('button', { name: 'Run step', exact: true }).click();
  await expect(page.getByRole('button', { name: '✓ Lab Fully Completed', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Reset lab', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run step', exact: true })).toBeEnabled();
  await expect(page.getByText('Execution Result: Verified')).toHaveCount(0);
});

test('reset errors and status errors are visible', async ({ page }) => {
  await page.route('**/api/labs/iam/create-user-alice/status/', route => route.fulfill({ json: { complete: false, steps: {}, error: 'Status unavailable' } }));
  await page.route('**/api/labs/iam/create-user-alice/reset/', route => route.fulfill({ status: 400, json: { error: 'Reset unavailable' } }));
  await page.goto('/labs/?service=iam&lab=create-user-alice');
  await expect(page.getByText('Status unavailable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Reset lab', exact: true }).click();
  await expect(page.getByText('Reset unavailable', { exact: true })).toBeVisible();
});

test('live status replaces stale completion saved in browser', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('floci_completed_labs', JSON.stringify({ 'iam:create-user-alice': true })));
  await page.goto('/labs/?service=iam&lab=create-user-alice');
  await expect(page.getByRole('button', { name: '▶ Run all remaining steps', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '✓ Lab Fully Completed', exact: true })).toHaveCount(0);
});

test('late status from a previous lab cannot complete the next lab', async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/labs/iam/create-user-alice/status/', async route => {
    await pending;
    await route.fulfill({ json: { complete: true, steps: { 'create-user': { verified: true } } } });
  });
  await page.goto('/labs/?service=iam&lab=create-user-alice');
  await page.getByRole('button', { name: /Next: #3\./ }).click();
  release();
  await expect(page.getByText('Lesson #3 of 78', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run step', exact: true })).toHaveCount(4);
  await expect(page.getByRole('button', { name: '▶ Run all remaining steps', exact: true })).toBeEnabled();
});

test('navigating during Run all prevents stale outputs and subsequent steps', async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let calls = 0;
  await page.route('**/api/labs/iam/create-admin-user/steps/*/run/', async route => {
    calls++;
    await pending;
    await route.fulfill({ json: { verified: true, stdout: 'Old lab output' } });
  });
  await page.goto('/labs/?service=iam&lab=create-admin-user');
  await page.getByRole('button', { name: '▶ Run all remaining steps', exact: true }).click();
  await expect.poll(() => calls).toBe(1);
  await page.getByRole('button', { name: /Next: #2\./ }).click();
  release();
  await expect(page.getByText('Lesson #2 of 78', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run step', exact: true })).toBeEnabled();
  await expect(page.getByText('Old lab output', { exact: true })).toHaveCount(0);
  expect(calls).toBe(1);
});
