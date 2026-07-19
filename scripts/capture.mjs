// Capture docs screenshots of the deterministic decision board.
//
// Expects a Yigdesk app already running at YIGDESK_URL with the board configured
// (YIGDESK_SCENARIO set, YIGDESK_LEDGER pointing at the same ledger seeded below).
// The old upload/analyze/council capture targets were removed with that UI.
import { chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const baseURL = process.env.YIGDESK_URL || 'http://127.0.0.1:8787';
const python = process.env.YIGDESK_PYTHON || 'python';
const scenario = process.env.YIGDESK_SCENARIO || path.resolve('data/scenarios/council_discount');
const ledger = process.env.YIGDESK_LEDGER || path.resolve('runtime/board.jsonl');

function seed(mode) {
  const args = ['-m', 'scripts.seed_board', '--scenario', scenario, '--ledger', ledger];
  if (mode) args.push(`--${mode}`);
  execFileSync(python, args, { stdio: 'inherit' });
}

await mkdir('docs/images', { recursive: true });
const browser = await chromium.launch();

async function capture(viewport, out, mode) {
  seed(mode);
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  await page.goto(baseURL);
  await page.getByTestId('board-root').waitFor();
  // Wait for the projection to render (a decision view, or the empty-board state).
  await page.waitForSelector('[data-testid="decision-view"], [data-testid="board-empty"]');
  await page.waitForTimeout(300);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  await page.screenshot({ path: out, fullPage: true });
  await page.close();
}

await capture({ width: 1440, height: 1120 }, 'docs/images/yigdesk-board.png');
await capture({ width: 390, height: 844 }, 'docs/images/yigdesk-board-mobile.png');
await browser.close();
