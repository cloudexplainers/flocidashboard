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
  const filter = page.getByPlaceholder('Filter labs by number (#1-#66), service (S3, IAM, EC2, EKS), or keyword...');
  await filter.fill('no-such-lab');
  await expect(page.getByText('No matching labs found')).toBeVisible();
  await filter.fill('S3');
  await expect(page.getByRole('button', { name: 'Start Lab Lesson' }).first()).toBeVisible();
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
  await expect(page.getByText('Lesson #3 of 66', { exact: true })).toBeVisible();
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
  await expect(page.getByText('Lesson #2 of 66', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run step', exact: true })).toBeEnabled();
  await expect(page.getByText('Old lab output', { exact: true })).toHaveCount(0);
  expect(calls).toBe(1);
});
