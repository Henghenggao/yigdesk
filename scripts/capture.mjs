import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1120 }, deviceScaleFactor: 1 });
const baseURL = process.env.YIGDESK_URL || 'http://127.0.0.1:8787';
await page.request.post(`${baseURL}/api/reset`, { data: { scenario_id: 'ready' } });
await page.goto(baseURL);
await page.getByTestId('analyze').click();
await page.waitForFunction(() => document.querySelector('[data-testid="verdict"]')?.textContent === 'READY FOR CFO');
await page.waitForTimeout(650);
await mkdir('docs/images', { recursive: true });
await page.screenshot({ path: 'docs/images/yigdesk-ready.png', fullPage: true });
await browser.close();
