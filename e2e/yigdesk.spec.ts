import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

function runProcess(command: string, args: string[]): Promise<number> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: process.cwd(),
      env: process.env,
      shell: false,
      stdio: 'inherit',
      windowsHide: true,
    });
    child.once('error', reject);
    child.once('exit', (code, signal) => resolve(code ?? (signal ? 1 : 0)));
  });
}

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
  await expect(page.locator('#read-wall')).toContainText('Codex Work calls Yigdesk MCP directly');

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

test('uploaded synthetic FY2024 workbook drives a real parsed decision board', async ({ page }) => {
  await page.goto('/');
  const sample = path.resolve('runtime/e2e-upload-sample.xlsx');

  await page.locator('#workbook-upload').setInputFiles(sample);
  await expect(page.locator('#upload-file-label')).toHaveText('e2e-upload-sample.xlsx');
  await page.locator('#requested-discount').fill('2.00');
  await page.locator('#margin-floor').fill('30.00');
  await page.locator('#upload-action').click();

  await expect(page.locator('#upload-status')).toContainText('48 FY2024 source cells');
  await expect(page.locator('#source-pill')).toHaveText('UPLOADED · SYNTHETIC');
  await expect(page.locator('#attachment-name')).toHaveText('e2e-upload-sample.xlsx');
  await expect(page.locator('#fingerprint')).toContainText('source sha256');
  await expect(page.getByRole('button', { name: /complete evidence/i })).toHaveAttribute('aria-pressed', 'false');

  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
  await expect(page.locator('#net-arr')).toHaveText('$14,365k');
  await expect(page.locator('#gross-margin')).toHaveText('30.2%');
  await expect(page.locator('#a2a-state')).toHaveText('A2A READY');
  await expect(page.locator('#board-requested')).toHaveText('2.0%');
  await expect(page.locator('#board-boundary')).toHaveText('2.23%');
  await expect(page.locator('#board-rounding')).toHaveText('2.24%');
  await expect(page.locator('#board-rounding-note')).toContainText('30.0% displayed');
  await expect(page.locator('#board-rounding-note')).toContainText('FAIL');
  await expect(page.locator('#board-stress')).toHaveText('26.7%');
  await expect(page.locator('#copy-council')).toBeEnabled();
  await expect(page.locator('#byte-proof')).toHaveText('Workbook bytes unchanged');
});

test('Codex Work council progress is rendered from the revision audit', async ({ page }) => {
  const pendingRoles = {
    finance_analyst: { state: 'running', completed: 2, expected: 4, tools: ['get_deal_context', 'find_feasible_boundary'] },
    sales_advocate: { state: 'pending', completed: 0, expected: 3, tools: [] },
    risk_challenger: { state: 'pending', completed: 0, expected: 5, tools: [] },
    decision_optimizer: { state: 'pending', completed: 0, expected: 3, tools: [] },
  };
  const completeRoles = Object.fromEntries(
    Object.entries(pendingRoles).map(([actor, role]) => [
      actor,
      { ...role, state: 'complete', completed: role.expected },
    ]),
  );
  let polls = 0;
  await page.route('**/api/council-status', async (route) => {
    polls += 1;
    if (polls === 1) {
      await route.fulfill({
        json: {
          mode: 'codex-work',
          status: 'running',
          verified: false,
          observed_call_count: 2,
          accepted_progress_count: 2,
          expected_call_count: 15,
          roles: pendingRoles,
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        mode: 'codex-work',
        status: 'verified',
        verified: true,
        observed_call_count: 15,
        accepted_progress_count: 15,
        expected_call_count: 15,
        roles: completeRoles,
      },
    });
  });

  await page.goto('/');

  await expect(page.locator('#a2a-state')).toHaveText('COUNCIL 2/15');
  await expect(page.locator('[data-council-actor="finance_analyst"]')).toContainText('2/4');
  await expect(page.locator('[data-council-actor="decision_optimizer"]')).toContainText('0/3');
  await expect(page.locator('#a2a-state')).toHaveText('A2A VERIFIED');
  await expect(page.locator('[data-council-actor="decision_optimizer"]')).toHaveAttribute('data-state', 'complete');
});

test('real Codex Work council completes from browser upload inside 120 seconds', async ({ page }) => {
  test.skip(process.env.YIGDESK_REAL_COUNCIL !== '1', 'requires an authenticated Codex runtime');
  test.setTimeout(125_000);
  const sample = path.resolve('runtime/e2e-upload-sample.xlsx');
  const runtime = path.resolve(process.env.YIGDESK_RUNTIME || 'runtime/e2e');
  const reportPath = path.join(runtime, 'real-council-e2e-report.json');
  const python = process.env.YIGDESK_PYTHON || 'python';
  const baseURL = process.env.YIGDESK_BASE_URL || 'http://127.0.0.1:8791';

  await page.goto('/');
  await page.locator('#workbook-upload').setInputFiles(sample);
  await page.locator('#requested-discount').fill('2.00');
  await page.locator('#margin-floor').fill('30.00');
  await page.locator('#upload-action').click();
  await expect(page.locator('#upload-status')).toContainText('48 FY2024 source cells');
  await expect(page.locator('#a2a-state')).toHaveText('CODEX WORK READY');

  const completed = runProcess(python, [
    '-m',
    'scripts.run_real_council_e2e',
    '--runtime',
    runtime,
    '--base-url',
    baseURL,
    '--timeout-seconds',
    '120',
    '--report',
    reportPath,
    '--acknowledge-data-sharing',
  ]);
  await expect(page.locator('#a2a-state')).toHaveText(/COUNCIL \d+\/15|A2A VERIFIED/, {
    timeout: 120_000,
  });
  expect(await completed).toBe(0);

  const report = JSON.parse(await readFile(reportPath, 'utf-8'));
  expect(report.status).toBe('passed');
  expect(report.verified).toBe(true);
  expect(report.accepted_call_count).toBe(15);
  expect(report.elapsed_ms).toBeLessThan(120_000);
  expect(report.first_mcp_call_ms).toBeLessThan(120_000);
  expect(report.fast_mode).toBe(false);
  expect(report.reasoning_effort).toBe('none');
  expect(report.model).toBe('gpt-5.6-terra');
  expect(report.candidate.candidate_status).toBe('READY_FOR_EXTERNAL_AUDIT');
  expect(report.candidate.revision_id).toBe(report.revision.revision_id);
  expect(report.candidate.source_fingerprint).toBe(report.revision.source_fingerprint);
  expect(report.candidate.commercial_optimality_proven).toBe(false);
  expect(report.candidate.recommended_discount_pct).toBeNull();
  expect(report.candidate.exact_max_discount_pct).toBe('2.239020');
  expect(report.candidate.largest_safe_step_pct).toBe('2.23');
  expect(report.candidate.first_unsafe_pct).toBe('2.24');
  await expect(page.locator('#a2a-state')).toHaveText('A2A VERIFIED');
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
  test.setTimeout(125_000);
  await page.goto('/');

  await expect(page.getByTestId('analyze')).toHaveAccessibleName(/analyze with nested codex/i);
  await page.getByTestId('analyze').click();

  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO', { timeout: 120_000 });
  await expect(page.locator('#agent-proof')).toBeVisible();
  await expect(page.locator('#agent-mode')).toContainText('gpt-5.6-sol');
  await expect(page.locator('#agent-meta')).toContainText('3 tools');
  await expect(page.locator('#agent-meta')).toContainText('arun-');
  await expect(page.locator('#agent-verified')).toHaveText('ENGINE MATCH VERIFIED');
});

test('browser sends the run token and renders truthful Codex phases', async ({ page, request }) => {
  const preview = await request.post('/api/analyze');
  expect(preview.ok()).toBe(true);
  const { packet } = await preview.json();
  const requestToken = 'browser-only-request-token';

  await page.route('**/api/state', async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.agent = {
      available: true,
      mode: 'codex-mcp',
      model: 'gpt-5.6-sol',
      reasoning_effort: 'low',
      request_token: requestToken,
    };
    await route.fulfill({ response, json: body });
  });

  await page.route('**/api/agent-runs', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().headers()['x-yigdesk-agent-token']).toBe(requestToken);
    await route.fulfill({
      status: 202,
      json: { run_id: 'arun-phases', status: 'queued', stage: 'starting_codex' },
    });
  });

  let poll = 0;
  await page.route('**/api/agent-runs/arun-phases', async (route) => {
    poll += 1;
    if (poll === 1) {
      await route.fulfill({
        json: { run_id: 'arun-phases', status: 'running', stage: 'calling_yigdesk_tools' },
      });
      return;
    }
    if (poll === 2) {
      await route.fulfill({
        json: { run_id: 'arun-phases', status: 'running', stage: 'verifying_result' },
      });
      return;
    }
    await route.fulfill({
      json: {
        run_id: 'arun-phases',
        status: 'completed',
        stage: 'review_ready',
        packet,
        agent: {
          model: 'gpt-5.6-sol',
          tool_calls: ['get_deal_context', 'preview_consequence', 'inspect_evidence'],
          latency_ms: 25,
          usage: { input_tokens: 10, output_tokens: 5 },
          verified: true,
        },
      },
    });
  });

  await page.goto('/');
  await page.getByTestId('analyze').click();
  await expect(page.getByTestId('analyze')).toContainText('Codex · starting…');
  await expect(page.getByTestId('analyze')).toContainText('Codex · calling Yigdesk tools…');
  await expect(page.getByTestId('analyze')).toContainText('Codex · verifying result…');
  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
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
  await expect(page.getByTestId('analyze')).toHaveAccessibleName(/analyze with nested codex/i);

  await page.getByTestId('analyze').click();
  await expect(page.getByTestId('verdict')).toHaveText('READY FOR CFO');
  await expect(page.locator('#agent-proof')).toBeVisible();
  await expect(page.locator('#agent-meta')).toContainText('arun-recovered-success');
  await expect(page.locator('#packet-export')).toBeVisible();
  await expect(page.locator('#copy-packet')).toBeEnabled();
});
