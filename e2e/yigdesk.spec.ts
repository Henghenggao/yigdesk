import { expect, test } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  const reset = await request.post('/api/reset', { data: { scenario_id: 'ready' } });
  expect(reset.ok()).toBe(true);
});

test('complete evidence renders a five-cell read-only consequence packet', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /see the consequence/i })).toBeVisible();
  await expect(page.locator('#packet-export')).toBeHidden();
  await expect(page.locator('yig-grid')).toHaveAttribute('data-yig-state', 'ready');

  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
  await expect(page.locator('#net-arr')).toHaveText('$880k');
  await expect(page.locator('#byte-proof')).toHaveText('Workbook bytes unchanged');
  await expect(page.locator('#packet-status')).toHaveText('5 CELLS · COMPLETE');
  await expect(page.locator('yig-grid tr.is-affected')).toHaveCount(5);
  await expect(page.locator('yig-grid')).toContainText('$900k');
  await expect(page.locator('yig-grid')).toContainText('$880k');
  await expect(page.locator('#packet-export')).toBeVisible();
  await expect(page.locator('#memo-status')).toHaveText('DRAFT · NOT SENT');

  await page.locator('yig-grid tr[data-address="Deal Model!B4"]').click();
  await expect(page.locator('yig-model-inspector')).toContainText('Gross profit ÷ Net ARR');
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0);
});

test('missing evidence produces an honest partial packet without an action control', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Missing cost evidence' }).click();
  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('HOLD');
  await expect(page.locator('#gross-margin')).toHaveText('Unavailable');
  await expect(page.locator('#packet-status')).toHaveText('5 CELLS · PARTIAL');
  await expect(page.locator('#memo-status')).toHaveText('NOT PREPARED');
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0);
});
