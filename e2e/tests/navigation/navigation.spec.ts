import { test, expect } from '@playwright/test';
import { attachGuard, openAppAuthed } from '../fixtures/ui';

const MENU = [
  { path: '/', name: 'Статистика' },
  { path: '/campaigns/new', name: 'Создать рассылку' },
  { path: '/campaigns', name: 'Рассылки' },
  { path: '/templates', name: 'Шаблоны и документы' },
  { path: '/connections', name: 'Подключения' },
  { path: '/profile', name: 'Профиль' },
] as const;

test.describe('Navigation @smoke', () => {
  test('statistics is home and menu routes open @smoke', async ({ page }) => {
    const guard = await openAppAuthed(page);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId('statistics-page')).toBeVisible();
    await expect(page.getByText('Статистика').first()).toBeVisible();

    await page.goto('/statistics', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId('statistics-page')).toBeVisible();

    await page.goto('/audiences', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId('statistics-page')).toBeVisible();

    for (const item of MENU) {
      await page.goto(item.path, { waitUntil: 'domcontentloaded' });
      await expect(page.getByText(item.name).first()).toBeVisible({ timeout: 20_000 });
      await expect(page.locator('body')).not.toHaveText(/Something went wrong|Traceback|Internal Server Error/i);
      await page.waitForLoadState('networkidle').catch(() => undefined);
    }

    await page.goto('/this-route-does-not-exist-xyz');
    await expect(page.getByRole('heading', { name: /не найдена|404/i }).or(page.getByText(/не найдена|404/i)).first()).toBeVisible();

    guard.assertClean('navigation');
  });


  test('login route responds @smoke', async ({ page }) => {
    const guard = attachGuard(page, {
      allowFailedUrls: ['fonts.gstatic.com', 'fonts.googleapis.com'],
    });
    await page.goto('/login');
    await expect(page.getByText('ai-offer')).toBeVisible();
    await expect(page.getByPlaceholder('Логин')).toBeVisible();
    guard.assertClean('login route');
  });
});
