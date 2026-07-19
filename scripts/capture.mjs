import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const browser = await chromium.launch();
const baseURL = process.env.YIGDESK_URL || 'http://127.0.0.1:8787';
await mkdir('docs/images', { recursive: true });

async function capture(viewport, path, scenario = 'ready', verdict = 'READY FOR CFO') {
  const page = await browser.newPage({ viewport, deviceScaleFactor: 1 });
  await page.request.post(`${baseURL}/api/reset`, { data: { scenario_id: scenario } });
  await page.goto(baseURL);
  await page.getByTestId('analyze').click();
  await page.waitForFunction((expected) => document.querySelector('[data-testid="verdict"]')?.textContent === expected, verdict);
  await page.waitForTimeout(500);
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    window.scrollTo({ top: 0, behavior: 'instant' });
  });
  await page.waitForTimeout(100);
  await page.screenshot({ path, fullPage: true });
  await page.close();
}

async function captureUpload() {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1350 }, deviceScaleFactor: 1 });
  await page.goto(baseURL);
  await page.locator('#workbook-upload').setInputFiles('runtime/northwind-fy2024-synthetic.xlsx');
  await page.locator('#upload-action').click();
  await page.locator('#upload-status[data-state="ready"]').waitFor();
  await page.getByTestId('analyze').click();
  await page.locator('#a2a-state').filter({ hasText: 'A2A READY' }).waitFor();
  await page.waitForTimeout(300);
  await page.screenshot({ path: 'docs/images/yigdesk-upload-a2a.png', fullPage: true });
  await page.close();
}

await captureUpload();
await capture({ width: 1440, height: 1120 }, 'docs/images/yigdesk-ready.png');
await capture({ width: 390, height: 844 }, 'docs/images/yigdesk-mobile.png');
await capture({ width: 1440, height: 1120 }, 'docs/images/yigdesk-hold.png', 'hold', 'HOLD');
await browser.close();
