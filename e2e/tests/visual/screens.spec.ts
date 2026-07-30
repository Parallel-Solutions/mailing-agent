import { test, expect } from '@playwright/test';
import { disableAnimations, openAppAuthed, waitForFonts } from '../fixtures/ui';

async function stabilize(page: import('@playwright/test').Page) {
  await disableAnimations(page);
  await waitForFonts(page);
  // Remove live KPI / queue widgets from layout so baselines stay height-stable.
  await page.addStyleTag({
    content: `
      .ant-pro-card:has(.ant-statistic),
      .ant-table-wrapper,
      .ant-pagination,
      .ant-tabs-content-holder {
        display: none !important;
      }
    `,
  });
  await page.waitForTimeout(200);
}

async function expectStableScreenshot(
  page: import('@playwright/test').Page,
  name: string,
  extraMasks: import('@playwright/test').Locator[] = [],
) {
  await stabilize(page);
  await expect(page).toHaveScreenshot(name, {
    fullPage: true,
    maxDiffPixelRatio: 0.05,
    mask: [
      page.locator('.ant-table'),
      page.locator('.ant-statistic-content'),
      page.locator('.ant-progress'),
      page.locator('.ant-tag'),
      page.locator('.ant-pagination'),
      ...extraMasks,
    ],
  });
}

test.describe('Visual regression @visual', () => {
  test('login screen @visual', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/login');
    await expect(page.getByText('ai-offer')).toBeVisible();
    await expectStableScreenshot(page, 'login.png');
  });

  test('statistics home and main routes @visual', async ({ page }) => {
    await openAppAuthed(page);
    await expect(page.getByTestId('statistics-page')).toBeVisible();
    await expectStableScreenshot(page, 'statistics.png');

    for (const route of ['/?tab=campaign-list', '/templates', '/connections', '/profile']) {
      await page.goto(route);
      await page.waitForLoadState('networkidle').catch(() => undefined);
      const name = route.replace(/^\//, '').replace(/\//g, '-').replace(/\?/g, '').replace(/=/g, '-') || 'home';
      await expectStableScreenshot(page, `${name}.png`);
    }

    // Composer creates a unique draft each visit — capture shell only after id is ready.
    await page.goto('/campaigns/new');
    await expect(page.getByText('Создание рассылки')).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/id=/i, { timeout: 30_000 });
    await expectStableScreenshot(page, 'campaigns-new.png', [
      page.locator('.ant-pro-form'),
      page.locator('.ant-collapse'),
      page.locator('.ant-steps'),
    ]);
  });
});
