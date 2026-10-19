import { test, expect } from '@playwright/test';

// These mutate the configured disposable emulator. Run separately from UI regressions.
test.describe.configure({ mode: 'serial' });

for (const lab of [
  { service: 'iam', key: 'create-user-alice', steps: 1 },
  { service: 's3', key: 'create-bucket', steps: 3 },
]) {
  test(`live ${lab.service}/${lab.key}: run, verify, reload, reset`, async ({ page }) => {
    test.setTimeout(90_000);
    await page.route('**/api/labs/progress/', route => route.fulfill({ json: { labs: [] } }));
    try {
      await page.goto(`/labs/?service=${lab.service}&lab=${lab.key}`);
      await page.getByRole('button', { name: 'Reset lab', exact: true }).click();
      await page.getByRole('button', { name: '▶ Run all remaining steps', exact: true }).click();
      await expect(page.getByRole('button', { name: '✓ Lab Fully Completed', exact: true })).toBeVisible({ timeout: 60_000 });
      await expect(page.getByText('Execution Result: Verified', { exact: true })).toHaveCount(lab.steps);
      await page.reload();
      await expect(page.getByRole('button', { name: '✓ Lab Fully Completed', exact: true })).toBeDisabled();
      await page.getByRole('button', { name: 'Reset lab', exact: true }).click();
      await expect(page.getByRole('button', { name: '▶ Run all remaining steps', exact: true })).toBeEnabled();
      await page.reload();
      await expect(page.getByRole('button', { name: '▶ Run all remaining steps', exact: true })).toBeEnabled();
    } finally {
      // Also clean up on assertion failure, using the real UI's CSRF-protected API.
      const token = await page.locator('meta[name="csrf-token"]').getAttribute('content');
      const response = await page.request.post(`/api/labs/${lab.service}/${lab.key}/reset/`, {
        headers: { 'X-CSRFToken': token || '' },
      });
      expect(response.ok()).toBeTruthy();
    }
  });
}
