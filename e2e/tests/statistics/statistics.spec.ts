import { test, expect } from '@playwright/test';
import { openAppAuthed } from '../fixtures/ui';

const STATS_TABS = [
  { key: 'dashboard', label: 'Обзор' },
  { key: 'campaign-list', label: 'Рассылки' },
  { key: 'campaigns', label: 'Показатели рассылок' },
  { key: 'recipients', label: 'Компании' },
  { key: 'campaign-analytics', label: 'Аналитика рассылки' },
  { key: 'campaign-full-analytics', label: 'Полная аналитика' },
  { key: 'marketing-consents', label: 'Подписки и отписки' },
] as const;

const REMOVED_STATS_TABS = ['База получателей', 'Согласия', 'Проблемы с email', 'Отчёты'] as const;

test.describe('Statistics page @smoke', () => {
  test('menu opens statistics and all tabs load without fatal errors', async ({ page }) => {
    const guard = await openAppAuthed(page);

    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('statistics-page')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Статистика').first()).toBeVisible();

    const stats = page.getByTestId('statistics-page');
    for (const label of REMOVED_STATS_TABS) {
      await expect(stats.getByRole('tab', { name: label })).toHaveCount(0);
    }
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
    await expect(stats.getByText('Принято провайдером').first()).toBeVisible();
    await expect(stats.getByText('Нет данных за выбранный период')).toHaveCount(0);

    guard.assertClean('statistics tabs');
  });

  test('campaign analytics opens current campaign attempts in a modal', async ({ page }) => {
    const guard = await openAppAuthed(page);

    await page.goto('/?tab=campaign-analytics', { waitUntil: 'domcontentloaded' });
    const campaignFilter = page.getByTestId('statistics-campaign-filter');
    await expect(campaignFilter).toBeVisible();
    await expect(page.getByText('Выберите рассылку для детальной аналитики')).toBeVisible();

    await campaignFilter.click();
    const firstCampaign = page
      .locator('.ant-select-dropdown:visible .ant-select-item-option')
      .first();
    await expect(firstCampaign).toBeVisible();
    await firstCampaign.click();
    // The filter now supports selecting several campaigns (chips), so unlike
    // a single-select it doesn't auto-close on pick — close it explicitly
    // before interacting with elements underneath.
    await page.keyboard.press('Escape');

    const totalCard = page.locator('[data-testid$="-total-card"]').first();
    await expect(totalCard).toBeVisible();
    for (const label of [
      'Всего',
      'Не дошло до отправки',
      'Отправлено в почтовый провайдер',
      'Ошибки почтового провайдера',
      'Доставлено реальное письмо',
      'Открыто',
      'Отписались у почтового провайдера',
      'Добавили в спам',
    ]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await totalCard.click();

    await expect(page.getByRole('dialog', { name: 'Все попытки и отправки' })).toBeVisible();
    await expect(page).toHaveURL(/modal=drill/);
    guard.assertClean('campaign attempts drilldown');
  });

  test('top campaign filter keeps its options and can be cleared', async ({ page }) => {
    const guard = await openAppAuthed(page);

    await page.goto('/?tab=campaign-analytics', { waitUntil: 'domcontentloaded' });
    const campaignFilter = page.getByTestId('statistics-campaign-filter');
    await campaignFilter.click();
    const campaignOptions = page.locator('.ant-select-dropdown:visible .ant-select-item-option');
    const firstCampaign = campaignOptions.first();
    await expect(firstCampaign).toBeVisible();
    const optionCount = await campaignOptions.count();
    const campaignName = await firstCampaign
      .locator('.ant-select-item-option-content')
      .innerText();
    await firstCampaign.click();
    // Multi-select stays open after a pick — close it before reopening below.
    await page.keyboard.press('Escape');

    await expect(page).toHaveURL(/campaign=/);
    await campaignFilter.click();
    await expect(campaignOptions).toHaveCount(optionCount);
    const selectedCampaign = page.locator('.ant-select-dropdown:visible .ant-select-item-option-selected');
    await expect(selectedCampaign.locator('.ant-select-item-option-content')).toHaveText(campaignName);
    await page.keyboard.press('Escape');

    await campaignFilter.hover();
    await campaignFilter.locator('.ant-select-clear').click();
    await expect(page).not.toHaveURL(/campaign=/);
    await expect(page.getByText('Выберите рассылку для детальной аналитики')).toBeVisible();
    guard.assertClean('campaign filter reset');
  });
});
