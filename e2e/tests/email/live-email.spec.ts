import { test } from '@playwright/test';

/**
 * Live provider SMTP checks (Gmail/Mail.ru/etc).
 * Skipped unless RUN_LIVE_EMAIL_TESTS=1 and secrets are provided via env / .env.e2e.local.
 * Never hardcode real credentials.
 */
const enabled = process.env.RUN_LIVE_EMAIL_TESTS === '1';

test.describe('Live SMTP providers @live-email', () => {
  test.skip(!enabled, 'Set RUN_LIVE_EMAIL_TESTS=1 and provide live SMTP secrets to run');

  test('placeholder live provider probe @live-email', async () => {
    const host = process.env.LIVE_SMTP_HOST;
    const user = process.env.LIVE_SMTP_USER;
    const pass = process.env.LIVE_SMTP_PASSWORD;
    if (!host || !user || !pass) {
      test.skip(true, 'LIVE_SMTP_HOST / LIVE_SMTP_USER / LIVE_SMTP_PASSWORD required');
    }
    // Intentionally not implemented until secrets are available in CI/local vault.
    throw new Error('Live email harness is reserved; configure secrets before enabling.');
  });
});
