import { defineConfig } from '@playwright/test';

const port = process.env.YIGDESK_E2E_PORT || '8791';
const externalBaseURL = process.env.YIGDESK_BASE_URL;
const baseURL = externalBaseURL || `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  timeout: 20_000,
  workers: 1,
  webServer: externalBaseURL
    ? undefined
    : {
        // Build the on-demand scenario workbook first, then serve the board. Without
        // YIGDESK_SCENARIO/LEDGER the board routes answer 503; these defaults mirror
        // the spec's fallbacks so a direct `playwright test` seeds the same ledger.
        command: `python scripts/build_scenarios.py && python -m yigdesk.app`,
        env: {
          ...process.env,
          PORT: port,
          YIGDESK_RUNTIME: 'runtime/e2e',
          YIGDESK_SCENARIO: process.env.YIGDESK_SCENARIO || 'data/scenarios/council_discount',
          YIGDESK_LEDGER: process.env.YIGDESK_LEDGER || 'runtime/e2e-board.jsonl'
        },
        url: `http://127.0.0.1:${port}/api/health`,
        reuseExistingServer: false,
        timeout: 30_000
      },
  use: {
    baseURL,
    trace: 'retain-on-failure'
  }
});
