import { chromium } from 'playwright';

const url = process.env.E2E_BASE_URL || 'http://web:9806';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
try {
  console.log('goto', `${url}/login`);
  const res = await page.goto(`${url}/login`, { waitUntil: 'domcontentloaded', timeout: 20_000 });
  console.log('status', res?.status(), 'final', page.url());
  console.log('title', await page.title());
} catch (err) {
  console.error('FAIL', err);
  process.exitCode = 1;
} finally {
  await browser.close();
}
