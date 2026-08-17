import type { Page } from '@playwright/test';
import { ConsoleGuard, type ConsoleGuardOptions } from './consoleGuard';

export async function disableAnimations(page: Page): Promise<void> {
  await page.addStyleTag({
    content: `
      *, *::before, *::after {
        animation: none !important;
        transition: none !important;
        caret-color: transparent !important;
      }
    `,
  });
}

export async function waitForFonts(page: Page): Promise<void> {
  await page.evaluate(async () => {
    if (document.fonts?.ready) {
      await document.fonts.ready;
    }
  });
}

export async function openAppAuthed(
  page: Page,
  options: ConsoleGuardOptions = {},
): Promise<ConsoleGuard> {
  const guard = new ConsoleGuard(page, {
    allowHttp4xxUrls: [
      '/api/auth/me',
      '/api/v1/templates/',
      '/api/v1/companies/',
      ...(options.allowHttp4xxUrls || []),
    ],
    allowFailedUrls: options.allowFailedUrls || [],
    allowConsole: [
      // Optional lazy-loaded preview thumbnails and company logos may 404 in E2E seed data.
      'Failed to load resource: the server responded with a status of 404 (Not Found)',
      ...(options.allowConsole || []),
    ],
  });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.getByTestId('statistics-page').waitFor({ state: 'visible', timeout: 30_000 });
  return guard;
}

export async function loginViaUi(page: Page, username: string, password: string): Promise<void> {
  await page.goto('/login');
  await page.getByPlaceholder('Логин').fill(username);
  await page.getByPlaceholder('Пароль').fill(password);
  await page.getByRole('button', { name: /вход|войти|login/i }).click();
  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 20_000 });
}

export function attachGuard(page: Page, options?: ConsoleGuardOptions): ConsoleGuard {
  return new ConsoleGuard(page, options);
}
