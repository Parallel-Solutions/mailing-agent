import { test, expect } from '@playwright/test';
import { apiLogin, apiPost, ensureMailpitMailbox } from '../fixtures/appApi';
import { mailpitDeleteAll, mailpitGetAttachment, mailpitWaitForMessage } from '../fixtures/mailpit';

test.describe('Attachments via Mailpit @email @attachments', () => {
  test('send SMTP test with sample PDF attachment via backend @email', async () => {
    test.setTimeout(120_000);

    const session = await apiLogin();
    await mailpitDeleteAll();
    const mailbox = await ensureMailpitMailbox(session);

    const recipient = 'e2e-attach@example.test';
    await apiPost(session, '/api/smtp/test', {
      provider: 'custom',
      email: mailbox.email,
      password: process.env.E2E_SMTP_SENDER_PASSWORD || 'mailpit-e2e',
      sender_name: 'E2E Attach',
      host: process.env.E2E_SMTP_HOST || 'mailpit',
      port: Number(process.env.E2E_SMTP_PORT || 1025),
      use_ssl: false,
      use_starttls: false,
      mailbox_id: mailbox.id,
      send_test_email_to: recipient,
      include_sample_attachment: true,
    });

    const message = await mailpitWaitForMessage(undefined, {
      timeoutMs: 45_000,
      subjectIncludes: 'Проверка SMTP',
      toIncludes: recipient,
    });

    const attachments = Array.isArray(message.Attachments) ? message.Attachments : [];
    expect(attachments.length, JSON.stringify(message).slice(0, 500)).toBeGreaterThan(0);

    const pdf = attachments.find((a) => String(a.FileName || '').toLowerCase().endsWith('.pdf'));
    expect(pdf, `expected pdf attachment, got ${JSON.stringify(attachments)}`).toBeTruthy();
    expect(String(pdf!.FileName)).toContain('e2e-sample.pdf');
    expect(String(pdf!.ContentType || '').toLowerCase()).toMatch(/pdf|octet-stream/);
    expect(Number(pdf!.Size || 0)).toBeGreaterThan(0);
    if (pdf!.PartID) {
      const part = await mailpitGetAttachment(message.ID, String(pdf!.PartID));
      expect(part.bytes.byteLength).toBeGreaterThan(0);
      const head = Buffer.from(part.bytes.slice(0, 5)).toString('utf8');
      expect(head).toBe('%PDF-');
    }
  });
});
