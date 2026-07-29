import { test, expect } from '@playwright/test';
import { openAppAuthed } from '../fixtures/ui';

const STATS_TABS = [
  { key: 'dashboard', label: 'Обзор' },
  { key: 'campaign-list', label: 'Рассылки' },
  { key: 'campaigns', label: 'Показатели рассылок' },
  { key: 'audiences', label: 'База получателей' },
  { key: 'recipients', label: 'Компании' },
  { key: 'campaign-analytics', label: 'Аналитика рассылки' },
  { key: 'campaign-full-analytics', label: 'Полная аналитика' },
  { key: 'consents', label: 'Согласия' },
  { key: 'marketing-consents', label: 'Подписки и отписки' },
  { key: 'problems', label: 'Проблемы с email' },
  { key: 'reports', label: 'Отчёты' },
] as const;

test.describe('Statistics page @smoke', () => {
  test('menu opens statistics and all tabs load without fatal errors', async ({ page }) => {
    const guard = await openAppAuthed(page);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('statistics-page')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Статистика').first()).toBeVisible();

    const stats = page.getByTestId('statistics-page');
    for (const tab of STATS_TABS) {
      await page.goto(`/?tab=${tab.key}`, { waitUntil: 'domcontentloaded' });
      await expect(stats.getByRole('tab', { name: new RegExp(`^${tab.label}$`), selected: true })).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.locator('body')).not.toHaveText(
        /Something went wrong|Traceback|Internal Server Error/i,
      );
      await page.waitForLoadState('networkidle').catch(() => undefined);
    }
    await page.goto('/?tab=dashboard', { waitUntil: 'domcontentloaded' });
    await expect(stats.getByText('Принято провайдером')).toBeVisible();
    await expect(stats.getByText('Нет данных за выбранный период')).toHaveCount(0);

    guard.assertClean('statistics tabs');
  });
});
