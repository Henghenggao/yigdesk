import { expect, test } from '@playwright/test';

test.beforeEach(async ({ request }) => {
  const reset = await request.post('/api/reset', { data: { scenario_id: 'ready' } });
  expect(reset.ok()).toBe(true);
});

test('complete evidence renders a five-cell read-only consequence packet', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /see the consequence/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /complete evidence/i })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#packet-export')).toBeHidden();
  await expect(page.locator('yig-grid')).toHaveAttribute('data-yig-state', 'ready');
  await expect(page.getByTestId('analyze')).toHaveAccessibleName(/preview consequence locally/i);
  await expect(page.locator('#read-wall')).toContainText('Codex runtime is not configured');

  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
  await expect(page.locator('#net-arr')).toHaveText('$880k');
  await expect(page.locator('#byte-proof')).toHaveText('Workbook bytes unchanged');
  await expect(page.locator('#packet-status')).toHaveText('5 CELLS · COMPLETE');
  await expect(page.locator('yig-grid tr.is-affected')).toHaveCount(5);
  await expect(page.locator('yig-grid')).toContainText('$900k');
  await expect(page.locator('yig-grid')).toContainText('$880k');
  await expect(page.locator('#packet-export')).toBeVisible();
  await expect(page.locator('body')).toHaveAttribute('data-outcome', 'ready');
  await expect(page.locator('#packet-heading')).toHaveText('ConsequencePacket prepared.');
  await expect(page.locator('#memo-status')).toHaveText('DRAFT · NOT SENT');
  await expect(page.getByTestId('analyze')).toHaveAttribute('data-state', 'idle');

  await page.locator('yig-grid tr[data-address="Deal Model!B4"]').click();
  await expect(page.locator('yig-grid tr[data-address="Deal Model!B4"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('yig-grid tr.is-selected')).toHaveCount(1);
  await expect(page.locator('yig-model-inspector')).toContainText('Gross profit ÷ Net ARR');
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0);
});

test('mobile layout keeps the proof workflow usable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');

  await page.keyboard.press('Tab');
  await expect(page.locator('.skip-link')).toBeFocused();
  await page.getByRole('button', { name: /complete evidence/i }).click();
  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
  await expect(page.locator('#packet-export')).toBeVisible();
  const viewport = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(viewport.scrollWidth).toBeLessThanOrEqual(viewport.clientWidth);
});

test('missing evidence produces an honest partial packet without an action control', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Missing cost evidence' }).click();
  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('HOLD');
  await expect(page.locator('#gross-margin')).toHaveText('Unavailable');
  await expect(page.locator('#headroom')).toHaveText('Floor check unavailable');
  await expect(page.locator('#packet-status')).toHaveText('5 CELLS · PARTIAL');
  await expect(page.locator('#memo-status')).toHaveText('NOT PREPARED');
  await expect(page.locator('body')).toHaveAttribute('data-outcome', 'hold');
  await expect(page.locator('#packet-heading')).toHaveText('Hold packet prepared.');
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0);
});

test('real Codex mode exposes a verified MCP run instead of a local fallback', async ({ page }) => {
  test.skip(process.env.YIGDESK_REAL_CODEX !== '1', 'requires an authenticated Codex runtime');
  test.setTimeout(150_000);
  await page.goto('/');

  await expect(page.getByTestId('analyze')).toHaveAccessibleName(/analyze with codex/i);
  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO', { timeout: 135_000 });
  await expect(page.locator('#agent-proof')).toBeVisible();
  await expect(page.locator('#agent-mode')).toContainText('gpt-5.6-sol');
  await expect(page.locator('#agent-meta')).toContainText('3 tools');
  await expect(page.locator('#agent-meta')).toContainText('arun-');
  await expect(page.locator('#agent-verified')).toHaveText('ENGINE MATCH VERIFIED');
});

test('a failed retry revokes stale proof immediately and a later success recovers', async ({ page, request }) => {
  const preview = await request.post('/api/analyze');
  expect(preview.ok()).toBe(true);
  const { packet } = await preview.json();

  await page.route('**/api/state', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.agent = { available: true, mode: 'codex', model: 'gpt-5.6-sol' };
    await route.fulfill({ response, json: body });
  });
  await page.route('**/api/reset', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.agent = { available: true, mode: 'codex', model: 'gpt-5.6-sol' };
    await route.fulfill({ response, json: body });
  });

  let attempt = 0;
  let releaseFailure!: () => void;
  let signalFailureRequest!: () => void;
  const failureGate = new Promise<void>((resolve) => { releaseFailure = resolve; });
  const failureRequested = new Promise<void>((resolve) => { signalFailureRequest = resolve; });
  const agent = {
    model: 'gpt-5.6-sol',
    tool_calls: ['get_deal_context', 'preview_consequence', 'inspect_evidence'],
    latency_ms: 1234,
    usage: { input_tokens: 100, output_tokens: 20 },
    verified: true,
  };

  await page.route('**/api/agent-runs', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }
    attempt += 1;
    if (attempt === 2) {
      signalFailureRequest();
      await failureGate;
      await route.fulfill({
        status: 200,
        json: {
          run_id: 'arun-failed-retry',
          status: 'failed',
          error: { message: 'The agent output did not match the engine packet.' },
        },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      json: {
        run_id: attempt === 1 ? 'arun-first-success' : 'arun-recovered-success',
        status: 'completed',
        packet,
        agent,
      },
    });
  });

  await page.goto('/');
  await page.getByTestId('analyze').click();
  await expect(page.locator('#agent-proof')).toBeVisible();
  await expect(page.locator('#agent-verified')).toHaveText('ENGINE MATCH VERIFIED');
  await expect(page.locator('#packet-export')).toBeVisible();
  await expect(page.locator('#copy-packet')).toBeEnabled();

  await page.getByTestId('analyze').click();
  await failureRequested;
  await expect(page.locator('#agent-proof')).toBeHidden();
  await expect(page.locator('#packet-export')).toBeHidden();
  await expect(page.locator('#copy-packet')).toBeDisabled();
  await expect(page.locator('#packet-id')).toHaveText('cpkt-pending');

  releaseFailure();
  await expect(page.getByTestId('verdict')).toHaveText('AGENT REJECTED');
  await expect(page.locator('#decision-empty')).toContainText('Analysis could not be verified');
  await expect(page.locator('#decision-empty')).toContainText('No proof packet is available');
  await expect(page.locator('#agent-proof')).toBeHidden();
  await expect(page.locator('#packet-export')).toBeHidden();

  await page.getByRole('button', { name: /complete evidence/i }).click();
  await expect(page.getByTestId('verdict')).toHaveText('NOT ANALYZED');
  await expect(page.locator('#decision-empty')).toContainText('Nothing inferred yet');
  await expect(page.locator('#packet-status')).toHaveText('PREVIEW ONLY');
  await expect(page.getByTestId('analyze')).toHaveAccessibleName(/analyze with codex/i);

  await page.getByTestId('analyze').click();
  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
  await expect(page.locator('#agent-proof')).toBeVisible();
  await expect(page.locator('#agent-meta')).toContainText('arun-recovered-success');
  await expect(page.locator('#packet-export')).toBeVisible();
  await expect(page.locator('#copy-packet')).toBeEnabled();
});
