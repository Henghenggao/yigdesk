import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const ROOT = process.cwd();
const PYTHON = process.env.YIGDESK_PYTHON || 'python';
const SCENARIO = process.env.YIGDESK_SCENARIO || path.resolve(ROOT, 'data/scenarios/council_discount');
const LEDGER = process.env.YIGDESK_LEDGER || path.resolve(ROOT, 'runtime/e2e-board.jsonl');

function seedBoard(): void {
  execFileSync(PYTHON, ['-m', 'scripts.seed_board', '--scenario', SCENARIO, '--ledger', LEDGER], { cwd: ROOT, stdio: 'inherit' });
}

test.describe.configure({ mode: 'serial' });

test('Northwind council moves from a fail-closed gate to a committed decision', async ({ page }) => {
  seedBoard();
  await page.goto('/');

  const view = page.getByTestId('decision-view');
  await expect(view).toBeVisible();
  await expect(page.getByTestId('executive-conclusion')).toContainText('Ready');
  await expect(page.getByTestId('candidate-comparison')).toContainText('12%');
  await expect(page.getByTestId('candidate-comparison')).toContainText('15%');
  await expect(page.getByTestId('candidate-comparison')).toContainText('20%');
  await expect(page.getByTestId('candidate-comparison')).toContainText('Gross margin');
  await expect(page.getByTestId('candidate-comparison')).toContainText('Headroom');
  await expect(page.getByTestId('candidate-comparison')).toContainText('Submitted request');
  await expect(page.getByTestId('candidate-comparison')).toContainText('Sales alternative');
  await expect(page.getByTestId('candidate-comparison')).toContainText('Risk boundary');
  const boardBeforeApproval = await (await page.request.get('/api/board')).json();
  expect(boardBeforeApproval.decisions.d1.candidates.submitted_request.author).toBe('agent:finance_analyst');
  expect(boardBeforeApproval.decisions.d1.candidates.sales_submitted_assessment.author).toBe('agent:sales_advocate');
  expect(boardBeforeApproval.decisions.d1.candidates.sales_alternative.author).toBe('agent:sales_advocate');
  expect(boardBeforeApproval.decisions.d1.candidates.risk_boundary.author).toBe('agent:risk_challenger');
  expect(boardBeforeApproval.decisions.d1.candidates.submitted_request.agent_identity.run_id).toBe('synthetic-preview-run');
  expect(boardBeforeApproval.decisions.d1.candidates.sales_alternative.agent_identity.prompt_revision).toBe('sales_advocate-preview-v1');
  expect(boardBeforeApproval.decisions.d1.claims['optimizer-advisory'].agent_identity.agent_id).toBe('decision_optimizer');
  await expect(page.getByTestId('decision-proof')).toContainText('Different perspectives');
  await expect(page.getByTestId('proof-source')).toContainText('4/4 proposals');
  await expect(page.getByTestId('proof-source')).toContainText('3 distinct options');
  await expect(page.getByTestId('proof-gate')).toContainText('Blocked');
  await expect(page.getByTestId('proof-gate')).toContainText('required approval missing');
  await expect(page.getByTestId('risk-boundary')).toContainText('boundary candidate');
  await expect(page.getByTestId('risk-boundary')).toContainText('Deal Inputs!B4');

  const pending = await page.request.post('/api/board/op', { data: {
    decision_id: 'd1', kind: 'request_resolve', payload: {},
  }});
  expect((await pending.json()).result.pending).toBe('required approval missing');

  await expect(page.getByTestId('approve-candidate')).toHaveCount(1);
  await expect(page.getByTestId('approve-candidate')).toHaveText('Approve submitted request (12%)');
  await expect(page.getByTestId('resolve')).toHaveCount(0);
  await page.getByTestId('approve-candidate').click();
  await expect(page.getByTestId('action-status')).toContainText('recorded');
  await expect(page.getByTestId('executive-conclusion')).toContainText('approval recorded');
  await expect(page.getByTestId('proof-human')).toContainText('approval recorded');
  await expect(page.getByTestId('proof-gate')).toContainText('Ready');

  const continuation = JSON.parse(execFileSync(PYTHON, [
    '-m', 'yigdesk.continuation', '--scenario', SCENARIO, '--ledger', LEDGER,
    '--decision-id', 'd1', '--after-seq', '0', '--timeout', '1',
  ], { cwd: ROOT, encoding: 'utf8' }));
  expect(continuation.status).toBe('resolved');
  expect(continuation.record.chosen_candidate_id).toBe('submitted_request');

  await page.getByTestId('refresh').click();
  await expect(page.getByTestId('executive-conclusion')).toContainText('Resolved');
  await expect(page.getByTestId('decision-proof')).toContainText('No agent committed this outcome');
  await expect(page.getByTestId('proof-gate')).toContainText('Committed');
  await expect(page.getByTestId('proof-record')).toContainText('Ledger sequence');
  await expect(page.getByTestId('proof-record')).toContainText('Evaluator revision');
  await expect(page.getByTestId('proof-record')).toContainText('Input cutoff');
  const board = await page.request.get('/api/board');
  const decision = (await board.json()).decisions.d1;
  expect(decision.approvals).toHaveLength(1);
  expect(decision.resolution.chosen_candidate_id).toBe('submitted_request');
  expect(decision.resolution.agent_identities).toHaveLength(5);
  await expect(page.getByTestId('proof-record')).toContainText('5 declared');
});

test('unsafe action input is rejected by the narrow bridge', async ({ page }) => {
  seedBoard();
  await page.goto('/');
  const response = await page.request.post('/api/agent-actions', { data: {
    version: 'yigdesk-agent-action/v1', action_id: 'unsafe-1', correlation_id: 'unsafe-1',
    decision_id: 'd1', action_type: 'resolve', human: { role: 'cfo' }, script: '<script>alert(1)</script>',
  }});
  expect(response.status()).toBe(400);
  await expect(page.getByTestId('decision-view')).toBeVisible();
});

test('ChatGPT widget renders the trusted Northwind decision manifest', async ({ page }) => {
  seedBoard();
  const manifest = await (await page.request.get('/api/decision-view')).json();
  const html = readFileSync(path.resolve(ROOT, 'yigdesk/static/chatgpt-widget.html'), 'utf8');
  await page.setContent(html);
  await page.evaluate((view) => {
    window.postMessage({
      jsonrpc: '2.0',
      method: 'ui/notifications/tool-result',
      params: {
        structuredContent: {
          view,
          stateVersion: 7,
          event: { kind: 'read_board' },
        },
      },
    }, '*');
  }, manifest);

  await expect(page.locator('h1')).toHaveText('Approve the discount?');
  await expect(page.getByText('Submitted request', { exact: true })).toBeVisible();
  await expect(page.getByText('Sales alternative', { exact: true })).toBeVisible();
  await expect(page.getByText('Risk boundary', { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/4\/4 proposals.*3 distinct options/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve submitted request (12%)' })).toBeEnabled();
  await expect(page.getByText('Live proof · #7')).toBeVisible();
});
