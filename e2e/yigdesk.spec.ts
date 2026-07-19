import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

// The board web surface renders a deterministic ledger projection and lets a
// human drive the two gate ops. The agent/MCP side normally seeds the ledger;
// here `scripts/seed_board.py` plays that role against the SAME shared ledger the
// app reads (YIGDESK_SCENARIO + YIGDESK_LEDGER, resolved fresh on every request),
// so re-seeding between specs deterministically swaps the board state.
//
// The harness (scripts/run_e2e.mjs) launches the app once with these env vars and
// forwards them to this process; the defaults keep a direct `playwright test`
// (webServer path) working too. Playwright runs single-worker (playwright.config
// workers:1) and this file is serial, so the shared ledger is never contended.

const ROOT = process.cwd();
const PYTHON = process.env.YIGDESK_PYTHON || 'python';
const SCENARIO = process.env.YIGDESK_SCENARIO || path.resolve(ROOT, 'data/scenarios/council_discount');
const LEDGER = process.env.YIGDESK_LEDGER || path.resolve(ROOT, 'runtime/e2e-board.jsonl');

type SeedMode = 'single' | 'multi' | 'empty';

function seedBoard(mode: SeedMode): void {
  const args = ['-m', 'scripts.seed_board', '--scenario', SCENARIO, '--ledger', LEDGER];
  if (mode === 'multi') args.push('--multi');
  if (mode === 'empty') args.push('--empty');
  execFileSync(PYTHON, args, { cwd: ROOT, stdio: 'inherit' });
}

test.describe.configure({ mode: 'serial' });

test('single decision auto-selects, prices its candidate, and drives the policy gate to a committed record', async ({ page }) => {
  seedBoard('single');
  await page.goto('/');

  // Exactly one decision auto-selects: the decision view renders directly with no chooser.
  await expect(page.getByTestId('board-root')).toBeVisible();
  await expect(page.getByTestId('decision-chooser')).toHaveCount(0);
  const view = page.getByTestId('decision-view');
  await expect(view).toBeVisible();
  await expect(view).toHaveAttribute('data-decision-id', 'd1');
  await expect(view).toContainText('Approve the discount?'); // question
  await expect(view).toContainText('council_discount');      // decision_type
  await expect(view).toContainText('open');                  // status

  // The priced candidate carries the deterministic verdict, figures, and evidence refs.
  const candidate = page.locator('[data-testid="candidate"][data-candidate-id="c1"]');
  await expect(candidate).toBeVisible();
  await expect(candidate).toHaveAttribute('data-verdict', 'ok');
  await expect(candidate).toContainText('Net ARR');
  await expect(candidate).toContainText('980.00');            // net_arr after discount=2
  await expect(candidate).toContainText('Deal Inputs!B4');    // grounded evidence ref

  // The grounded risk claim is surfaced.
  const claim = page.getByTestId('claim');
  await expect(claim).toBeVisible();
  await expect(claim).toContainText('risk');
  await expect(claim).toContainText('cogs may rise');

  // max:<metric> is decision-scoped: a policy winner + a single authorize control,
  // and NO candidate-scoped approves (that is the human_selected shape).
  await expect(page.getByTestId('gate-panel')).toBeVisible();
  await expect(page.getByTestId('policy-winner')).toContainText('c1');
  await expect(page.getByTestId('authorize-policy')).toBeVisible();
  await expect(page.getByTestId('approve-candidate')).toHaveCount(0);

  // Act as cfo and authorize the policy selection (decision-scoped approve).
  await page.getByTestId('role-select').selectOption('cfo');
  const approvalCommitted = page.waitForResponse(
    (r) => r.url().includes('/api/board/op') && r.request().method() === 'POST',
  );
  await page.getByTestId('authorize-policy').click();
  await approvalCommitted; // ensure the approval is on the ledger before resolving

  // Resolve → deterministic committed record (policy picks max headroom = c1).
  await page.getByTestId('resolve').click();
  const outcome = page.getByTestId('resolve-outcome');
  await expect(outcome).toBeVisible();
  await expect(outcome).toContainText('c1');     // chosen_candidate_id
  await expect(outcome).toContainText('policy'); // closed_by

  // After a committed resolution the gate controls carry disabled...
  await expect(page.getByTestId('resolve')).toBeDisabled();
  await expect(page.getByTestId('authorize-policy')).toBeDisabled();

  // ...and resolving again is idempotent: the SAME record still renders.
  await page.getByTestId('resolve').click({ force: true });
  await expect(outcome).toContainText('c1');
  await expect(outcome).toContainText('policy');
  await expect(page.getByTestId('resolve')).toBeDisabled();
});

test('multiple decisions require an explicit chooser selection before the gate appears', async ({ page }) => {
  seedBoard('multi');
  await page.goto('/');

  // A chooser is offered and nothing is auto-selected.
  await expect(page.getByTestId('decision-chooser')).toBeVisible();
  await expect(page.getByTestId('decision-option')).toHaveCount(2);

  // Until a decision is chosen there is no decision view and no gate actions.
  await expect(page.getByTestId('decision-view')).toHaveCount(0);
  await expect(page.getByTestId('gate-panel')).toHaveCount(0);
  await expect(page.getByTestId('resolve')).toHaveCount(0);
  await expect(page.getByTestId('authorize-policy')).toHaveCount(0);

  // Choosing a decision reveals its view and gate.
  await page.locator('[data-testid="decision-option"][data-decision-id="d2"]').click();
  const view = page.getByTestId('decision-view');
  await expect(view).toBeVisible();
  await expect(view).toHaveAttribute('data-decision-id', 'd2');
  await expect(view).toContainText('Approve the pilot expansion?');
  await expect(page.getByTestId('gate-panel')).toBeVisible();
});

test('an empty ledger renders the empty-board state', async ({ page }) => {
  seedBoard('empty');
  await page.goto('/');

  await expect(page.getByTestId('board-empty')).toBeVisible();
  await expect(page.getByTestId('decision-view')).toHaveCount(0);
  await expect(page.getByTestId('decision-chooser')).toHaveCount(0);
});
