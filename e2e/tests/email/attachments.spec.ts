import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {
  apiLogin,
  apiPost,
  apiPostMultipart,
  apiGet,
  ensureMailpitMailbox,
} from '../fixtures/appApi';
import { mailpitDeleteAll, mailpitGetAttachment, mailpitWaitForMessage } from '../fixtures/mailpit';

const FIXTURES = process.env.E2E_FIXTURES_DIR || '/fixtures';
const fixture = (name: string) => path.join(FIXTURES, name);

async function pollDocuments(session: Awaited<ReturnType<typeof apiLogin>>, jobId: string) {
  const started = Date.now();
  let last: any = {};
  while (Date.now() - started < 600_000) {
    const payload = await apiGet(
      session,
      `/api/documents/status?job_id=${encodeURIComponent(jobId)}&document_mode=kp`,
    );
    last = payload?.result || payload;
    const status = String(last.status || '').toLowerCase();
    if (status === 'completed') return last;
    if (status === 'failed' || status === 'error') {
      throw new Error(`documents ${status}: ${JSON.stringify(last).slice(0, 600)}`);
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`documents timeout: ${JSON.stringify(last).slice(0, 600)}`);
}

test.describe('Attachments via Mailpit @email @attachments', () => {
  test('generate documents and send SMTP test with real attachment via backend @email', async () => {
    test.setTimeout(900_000);

    for (const name of ['recipients.xlsx', 'mail_template.txt', 'kp_1.docx']) {
      expect(fs.existsSync(fixture(name))).toBeTruthy();
    }

    const session = await apiLogin();
    await mailpitDeleteAll();
    const mailbox = await ensureMailpitMailbox(session);

    const jobCreated = await apiPost(session, '/api/jobs');
    const jobId = String(jobCreated?.result?.job_id || jobCreated?.job_id || '');
    expect(jobId).toBeTruthy();

    const dataForm = new FormData();
    dataForm.set('job_id', jobId);
    dataForm.set(
      'file',
      new Blob([fs.readFileSync(fixture('recipients.xlsx'))], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }),
      'recipients.xlsx',
    );
    await apiPostMultipart(session, '/api/upload/data', dataForm);

    for (const [kind, file, mime] of [
      ['mail', 'mail_template.txt', 'text/plain'],
      ['kp', 'kp_1.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    ] as const) {
      const form = new FormData();
      form.set('job_id', jobId);
      form.set('template_kind', kind);
      form.set('file', new Blob([fs.readFileSync(fixture(file))], { type: mime }), file);
      await apiPostMultipart(session, '/api/upload/template', form);
    }

    await apiPost(session, '/api/documents/template-preview', {
      job_id: jobId,
      document_mode: 'kp',
      work_type: 'mngp_settlements',
    });
    await apiPost(session, '/api/documents/start', {
      job_id: jobId,
      document_mode: 'kp',
      work_type: 'mngp_settlements',
      mode: 'fast',
      template_analysis_confirmed: true,
    });
    const docs = await pollDocuments(session, jobId);
    expect(String(docs.status)).toBe('completed');

    // Real backend SMTP send (same transport stack as production), with attachment.
    await mailpitDeleteAll();
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
