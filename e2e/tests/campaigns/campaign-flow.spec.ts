import { test, expect } from '@playwright/test';
import path from 'node:path';
import { apiLogin, ensureMailpitMailbox } from '../fixtures/appApi';
import { openAppAuthed } from '../fixtures/ui';
import { mailpitDeleteAll, mailpitWaitForMessage } from '../fixtures/mailpit';

async function goToCampaignStep(page: import('@playwright/test').Page, stepTitle: string) {
  await page.getByRole('button', { name: new RegExp(`\\d+\\s+${stepTitle}`) }).click();
}

test.describe('Campaign creation and schedule', () => {
  test('create draft, import recipients, schedule preview, persist after reload', async ({ page }) => {
    const guard = await openAppAuthed(page);
    await page.goto('/campaigns/new');
    await expect(page.getByText('Создание рассылки')).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/id=[0-9a-f-]{36}/i, { timeout: 30_000 });

    const name = `E2E Campaign ${Date.now()}`;
    await page.getByLabel('Название').fill(name);
    await expect(page.getByText('Сохранено')).toBeVisible({ timeout: 15_000 });

    await goToCampaignStep(page, 'Получатели');
    const fixture = path.join('/work', 'fixtures', 'manual', 'recipients-valid.csv');
    const upload = page.getByRole('button', { name: /Загрузить Excel\s*\/\s*CSV/i });
    await expect(upload).toBeVisible();
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), upload.click()]);
    await chooser.setFiles(fixture);
    await expect(page.getByRole('cell', { name: 'csv1@example.com' })).toBeVisible({
      timeout: 20_000,
    });

    await goToCampaignStep(page, 'Расписание');
    await page.getByLabel('Размер пакета').fill('2');
    await page.getByLabel(/Интервал между пакетами/i).fill('30');
    await expect(page.getByText(/Прогноз:/i)).toBeVisible();

    await page.reload();
    await expect(page.getByLabel('Название')).toHaveValue(name, { timeout: 20_000 });

    guard.assertClean('campaign draft');
  });

  test('launch campaign sends mail via Mailpit @email', async ({ page }) => {
    test.setTimeout(120_000);
    await mailpitDeleteAll();
    const guard = await openAppAuthed(page);

    await page.goto('/campaigns/new');
    await expect(page.getByText('Создание рассылки')).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/id=([0-9a-f-]{36})/i, { timeout: 30_000 });
    const campaignId = page.url().match(/id=([0-9a-f-]{36})/i)?.[1];
    expect(campaignId).toBeTruthy();

    const session = await apiLogin();
    const mailpitBox = await ensureMailpitMailbox(session);
    expect(mailpitBox?.id).toBeTruthy();

    const name = `Mailpit ${Date.now()}`;
    const patch = await page.request.patch(`/api/v1/campaigns/${campaignId}`, {
      data: {
        name,
        mail_subject: 'Mailpit subject',
        send_scenario: 'materials_now',
        connection_ids: [mailpitBox.id],
        smtp_mailbox_id: mailpitBox.id,
        draft_payload: { email_body: '<p>Hello from E2E Mailpit</p>' },
      },
    });
    expect(patch.ok()).toBeTruthy();

    const recipients = await page.request.put(`/api/v1/campaigns/${campaignId}/recipients`, {
      data: {
        recipients: [
          {
            company: 'ООО E2E',
            contact_name: 'Tester',
            email: 'mailpit-target@example.test',
            region: 'Москва',
          },
        ],
      },
    });
    expect(recipients.ok()).toBeTruthy();

    await page.reload();
    await goToCampaignStep(page, 'Запуск');
    await expect(page.locator('.campaign-launch-readiness-overlay')).toHaveCount(0, { timeout: 60_000 });
    await expect(page.getByText('Готово к запуску')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole('button', { name: 'Запустить сейчас' })).toBeEnabled();
    await page.getByRole('button', { name: 'Запустить сейчас' }).click();
    await page.waitForURL(new RegExp(`/campaigns/${campaignId}`), { timeout: 30_000 });

    const msg = await mailpitWaitForMessage(
      (m) =>
        Boolean(m.Subject?.includes('Mailpit subject')) &&
        Boolean(m.To?.some((t) => (t.Address || '').includes('mailpit-target@example.test'))),
      { timeoutMs: 90_000 },
    );
    expect(msg.Subject).toMatch(/Mailpit subject/i);

    guard.assertClean('mailpit send');
  });
});
