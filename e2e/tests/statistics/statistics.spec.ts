import { test, expect } from '@playwright/test';
import { openAppAuthed } from '../fixtures/ui';

const TABS = [
  'Обзор',
  'Рассылки',
  'Компании',
  'Аналитика рассылки',
  'Согласия',
  'Проблемы с email',
  'Отчёты',
] as const;

test.describe('Statistics page @smoke', () => {
  test('menu opens statistics and all tabs load without fatal errors', async ({ page }) => {
    const guard = await openAppAuthed(page);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('statistics-page')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Статистика отправок').first()).toBeVisible();

    const stats = page.getByTestId('statistics-page');
    for (const tab of TABS) {
      // Scope to the page: role=name substring can also match "Аналитика рассылки".
      const tabLocator = stats.getByRole('tab', { name: new RegExp(`^${tab}$`) });
      await tabLocator.click();
      await expect(tabLocator).toHaveAttribute('aria-selected', 'true');
      await expect(page.locator('body')).not.toHaveText(
        /Something went wrong|Traceback|Internal Server Error/i,
      );
      await page.waitForLoadState('networkidle').catch(() => undefined);
    }
    await stats.getByRole('tab', { name: /^Обзор$/ }).click();
    await expect(stats.getByText('Компаний в рассылке')).toBeVisible();
    await expect(stats.getByText('Нет данных за выбранный период')).toHaveCount(0);

    guard.assertClean('statistics tabs');
  });
});
