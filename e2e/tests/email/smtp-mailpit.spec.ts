import { test, expect } from '@playwright/test';
import { apiLogin, apiPost, ensureMailpitMailbox } from '../fixtures/appApi';
import { mailpitDeleteAll, mailpitWaitForMessage } from '../fixtures/mailpit';
import { attachGuard, openAppAuthed } from '../fixtures/ui';

test.describe('SMTP through Mailpit @email', () => {
  test('send real test email via backend SMTP API and verify in Mailpit @email @smoke', async ({
    page,
  }) => {
    const guard = await openAppAuthed(page);
    const session = await apiLogin();
    await mailpitDeleteAll();

    const mailbox = await ensureMailpitMailbox(session);
    const recipient = 'e2e-recipient@example.test';

    // Real backend send path (not Mailpit message create)
    await apiPost(session, '/api/smtp/test', {
      provider: 'custom',
      email: mailbox.email,
      password: process.env.E2E_SMTP_SENDER_PASSWORD || 'mailpit-e2e',
      sender_name: 'E2E Mailpit',
      host: process.env.E2E_SMTP_HOST || 'mailpit',
      port: Number(process.env.E2E_SMTP_PORT || 1025),
      use_ssl: false,
      use_starttls: false,
      mailbox_id: mailbox.id,
      send_test_email_to: recipient,
    });

    const message = await mailpitWaitForMessage(undefined, {
      timeoutMs: 30_000,
      subjectIncludes: 'Проверка SMTP',
      toIncludes: recipient,
      fromIncludes: mailbox.email,
    });

    expect(message.Subject || '').toContain('Проверка SMTP');
    expect((message.From?.Address || '').toLowerCase()).toContain(mailbox.email.toLowerCase());
    const toAddrs = (message.To || []).map((t) => (t.Address || '').toLowerCase());
    expect(toAddrs.some((a) => a.includes(recipient.toLowerCase()))).toBeTruthy();
    const body = `${message.Text || ''}\n${message.HTML || ''}`;
    expect(body.toLowerCase()).toMatch(/smtp|подключен|тест/i);

    // UI still healthy after send
    await page.goto('/');
    await expect(page.getByText('Дашборд').first()).toBeVisible();
    guard.assertClean('smtp mailpit');
  });
});

