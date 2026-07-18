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
        command: `python -m yigdesk.app`,
        env: { ...process.env, PORT: port, YIGDESK_RUNTIME: 'runtime/e2e' },
        url: `http://127.0.0.1:${port}/api/health`,
        reuseExistingServer: false,
        timeout: 30_000
      },
  use: {
    baseURL,
    trace: 'retain-on-failure'
  }
});
