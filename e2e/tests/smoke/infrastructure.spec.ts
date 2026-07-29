import { test, expect } from '@playwright/test';
import { API_URL } from '../fixtures/appApi';
import { MAILPIT_URL } from '../fixtures/mailpit';
import { attachGuard } from '../fixtures/ui';

test.describe('Infrastructure smoke @smoke', () => {
  test('frontend, health, mailpit and clean console @smoke', async ({ page, request }) => {
    const health = await request.get(`${API_URL}/health`);
    expect(health.status()).toBe(200);
    const healthBody = await health.json();
    expect(healthBody.status).toBe('ok');
    expect(healthBody.database).toBe('up');

    const ready = await request.get(`${API_URL}/ready`);
    expect(ready.status()).toBe(200);
    const readyBody = await ready.json();
    expect(readyBody.status).toBe('ok');
    expect(readyBody.database).toBe('up');
    expect(readyBody.redis).toBe('up');
    expect(readyBody.object_store).toBe('up');
    expect(readyBody.gotenberg).toBe('up');

    const mailpit = await request.get(`${MAILPIT_URL}/api/v1/info`);
    expect(mailpit.status()).toBe(200);

    const guard = attachGuard(page, {
      allowHttp4xxUrls: ['/favicon.ico'],
      allowFailedUrls: ['fonts.gstatic.com', 'fonts.googleapis.com'],
    });

    await page.goto('/login');
    await expect(page.getByText('ai-offer')).toBeVisible();
    await expect(page.getByPlaceholder('Логин')).toBeVisible();

    await page.goto('/');
    await expect(page.getByTestId('statistics-page')).toBeVisible();
    await expect(page.locator('body')).not.toBeEmpty();

    const me = await request.get(`${API_URL}/api/auth/me`);
    expect(me.status()).toBeLessThan(500);
    expect(me.ok()).toBeTruthy();

    guard.assertClean('infrastructure smoke');
  });
});
