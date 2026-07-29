import { test, expect } from '@playwright/test';
import { API_URL, PASSWORD, USERNAME } from '../fixtures/appApi';
import { attachGuard, loginViaUi } from '../fixtures/ui';

test.describe('Authentication', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('login, protected route, logout, session restore', async ({ page, context }) => {
    const guard = attachGuard(page, {
      allowHttp4xxUrls: ['/api/auth/login', '/api/auth/me', '/favicon.ico'],
      allowFailedUrls: ['/api/auth/logout'],
      allowConsole: [],
    });

    await page.goto('/');
    await page.waitForURL(/\/login/);
    await expect(page.getByText('ai-offer')).toBeVisible();

    await loginViaUi(page, USERNAME, PASSWORD);
    await expect(page.getByTestId('statistics-page')).toBeVisible();

    const cookies = await context.cookies();
    expect(cookies.some((c) => c.name === 'mailing_agent_session')).toBeTruthy();

    await page.reload();
    await expect(page.getByTestId('statistics-page')).toBeVisible();

    // Avatar title lives in the sider (ProLayout side mode), not always in the top header.
    await page.getByText(USERNAME, { exact: true }).first().click();
    await page.getByRole('menuitem', { name: 'Выйти' }).click();
    await page.waitForURL(/\/login/, { timeout: 15_000 });

    const afterLogout = await page.request.get(`${API_URL}/api/auth/me`);
    expect([401, 403]).toContain(afterLogout.status());

    await loginViaUi(page, USERNAME, PASSWORD);
    await expect(page.getByTestId('statistics-page')).toBeVisible();

    guard.assertClean('auth flow');
  });

  test('invalid password stays on login', async ({ page }) => {
    await page.goto('/login');
    await page.getByPlaceholder('Логин').fill(USERNAME);
    await page.getByPlaceholder('Пароль').fill('wrong-password-xxx');
    await page.getByRole('button', { name: /вход|войти|login/i }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByText(/неверн|ошибка|парол/i).first()).toBeVisible({ timeout: 10_000 });
  });
});
