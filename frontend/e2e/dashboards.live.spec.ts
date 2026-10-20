import { test, expect } from '@playwright/test';

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
