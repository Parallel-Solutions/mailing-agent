import { test, expect } from '@playwright/test';
import path from 'node:path';
import { apiLogin, ensureMailpitMailbox } from '../fixtures/appApi';
import { attachGuard, openAppAuthed } from '../fixtures/ui';
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
    // CampaignNewPage.tsx tracks the active wizard step in the `step` URL
    // param (`readIntParam(params, 'step', ...)`), and its Collapse has no
    // `forceRender` — an inactive panel's fields (including "Название" in
    // step 0) aren't mounted at all. `goToCampaignStep` above pushed
    // `?step=3` for "Расписание", so the reload above correctly reopens on
    // that step (proving the draft's schedule fields survived reload) but
    // leaves step 0 collapsed. Strip `step` to reopen it before checking
    // the name — this is a second real navigation, so it doubles as
    // confirmation the name also survives a reload.
    const basicsUrl = new URL(page.url());
    basicsUrl.searchParams.delete('step');
    await page.goto(basicsUrl.toString());
    await expect(page.getByLabel('Название')).toHaveValue(name, { timeout: 20_000 });

    guard.assertClean('campaign draft');
  });

  test('keeps the same draft when leaving the wizard and returning from navigation', async ({ page }) => {
    const guard = await openAppAuthed(page);
    await page.goto('/campaigns/new');
    await expect(page).toHaveURL(/id=([0-9a-f-]{36})/i, { timeout: 30_000 });
    const campaignId = page.url().match(/id=([0-9a-f-]{36})/i)?.[1];
    expect(campaignId).toBeTruthy();

    const createRequests: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'POST' && url.pathname === '/api/v1/campaigns') {
        createRequests.push(request.url());
      }
    });

    const name = `Navigation draft ${Date.now()}`;
    await page.locator('[data-onboarding-id="campaign-name"] input').fill(name);
    await page.locator('a[href="/templates"]').click();
    await expect(page).toHaveURL(/\/templates$/);

    const resumeLink = page.locator(`a[href="/campaigns/new?id=${campaignId}"]`).first();
    await expect(resumeLink).toBeVisible();
    await resumeLink.click();

    await expect(page).toHaveURL(new RegExp(`/campaigns/new\\?id=${campaignId}`));
    await expect(page.locator('[data-onboarding-id="campaign-name"] input')).toHaveValue(name);
    await expect.poll(async () => {
      const response = await page.request.get(`/api/v1/campaigns/${campaignId}`);
      return (await response.json()).result.name;
    }).toBe(name);
    expect(createRequests).toHaveLength(0);

    guard.assertClean('campaign navigation draft');
  });

  test('launch campaign sends mail via Mailpit @email', async ({ page }) => {
    test.setTimeout(120_000);
    await mailpitDeleteAll();
    // This test launches the campaign via a raw API call (not the UI), so the
    // page is never told it happened — revisiting its own /campaigns/new?id=
    // URL below to check the "already launched" warning briefly hydrates the
    // schedule form before that check redirects away, firing one benign
    // autosave PUT .../schedule the backend correctly 409s (the browser logs
    // any non-2xx response to console regardless of the app catching it).
    const guard = attachGuard(page, {
      allowHttp4xxUrls: ['/api/auth/me', '/api/v1/templates/', '/api/v1/companies/', '/schedule'],
      allowConsole: [
        'Failed to load resource: the server responded with a status of 404 (Not Found)',
        'Failed to load resource: the server responded with a status of 409 (Conflict)',
      ],
    });
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.getByTestId('statistics-page').waitFor({ state: 'visible', timeout: 30_000 });

    await page.goto('/campaigns/new');
    await expect(page.getByText('Создание рассылки')).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/id=([0-9a-f-]{36})/i, { timeout: 30_000 });
    const campaignId = page.url().match(/id=([0-9a-f-]{36})/i)?.[1];
    expect(campaignId).toBeTruthy();

    const session = await apiLogin();
    const mailpitBox = await ensureMailpitMailbox(session);
    expect(mailpitBox?.id).toBeTruthy();

    const companyResp = await page.request.post('/api/v1/companies', {
      data: { name: `E2E Co ${Date.now()}`, phone: '', contact_person_name: 'E2E' },
    });
    expect(companyResp.ok()).toBeTruthy();
    const companyId = (await companyResp.json()).result.id as string;
    const workTypeResp = await page.request.post(`/api/v1/companies/${companyId}/work-types`, {
      data: { name: 'E2E work' },
    });
    expect(workTypeResp.ok()).toBeTruthy();
    const workTypeId = (await workTypeResp.json()).result.id as string;

    const name = `Mailpit ${Date.now()}`;
    const patch = await page.request.patch(`/api/v1/campaigns/${campaignId}`, {
      data: {
        name,
        mail_subject: 'Mailpit subject',
        send_scenario: 'materials_now',
        connection_ids: [mailpitBox.id],
        smtp_mailbox_id: mailpitBox.id,
        company_id: companyId,
        company_work_type_id: workTypeId,
        draft_payload: {
          email_body: '<p>Hello from E2E Mailpit</p>',
          company_id: companyId,
          company_work_type_id: workTypeId,
          mapping_confirmed: true,
        },
      },
    });
    expect(patch.ok()).toBeTruthy();

    const recipients = await page.request.put(`/api/v1/campaigns/${campaignId}/recipients`, {
      data: {
        recipients: [
          {
            company: 'ООО E2E',
            contact_name: 'Tester',
            email: 'mailpit-target@example.com',
            region: 'Москва',
          },
        ],
      },
    });
    expect(recipients.ok()).toBeTruthy();

    const schedule = await page.request.put(`/api/v1/campaigns/${campaignId}/schedule`, {
      data: {
        send_immediately: true,
        start_at: null,
        batch_size: 25,
        interval_seconds: 0,
        weekdays: [],
        time_windows: [],
      },
    });
    expect(schedule.ok()).toBeTruthy();

    const validation = await page.request.get(`/api/v1/campaigns/${campaignId}/validate`);
    expect(validation.ok()).toBeTruthy();
    expect((await validation.json()).result.ok).toBe(true);

    const launch = await page.request.post(`/api/v1/campaigns/${campaignId}/launch`);
    expect(launch.ok(), await launch.text()).toBeTruthy();

    const msg = await mailpitWaitForMessage(
      (m) =>
        Boolean(m.Subject?.includes('Mailpit subject')) &&
        Boolean(m.To?.some((t) => (t.Address || '').includes('mailpit-target@example.com'))),
      { timeoutMs: 90_000 },
    );
    expect(msg.Subject).toMatch(/Mailpit subject/i);

    const createRequests: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (request.method() === 'POST' && url.pathname === '/api/v1/campaigns') {
        createRequests.push(request.url());
      }
    });

    // The sidebar's "Создать рассылку" link keeps pointing at whatever draft
    // is currently open (`?id=<current campaignId>`) as a "resume where you
    // left off" convenience — which, since the browser is still sitting on
    // that exact URL (everything above went through page.request, not the
    // UI), is *also* the page's current URL. A same-URL click is a no-op for
    // react-router (its location doesn't change, so CampaignNewPage's
    // mount effect never re-runs and never notices the campaign it launched
    // behind its back is no longer a draft) — force a real navigation
    // instead, mirroring what actually opening that link in a fresh tab
    // would do.
    await page.goto(`/campaigns/new?id=${campaignId}`);
    // That campaign is no longer a draft (just launched above), and
    // CampaignNewPage.tsx (~line 231) deliberately refuses to silently start
    // editing/re-launching an already-sent campaign under its id — it warns
    // and redirects to the read-only detail page instead of forking a new
    // draft behind the user's back.
    await expect(page.getByText('Эта рассылка уже запускалась')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/campaigns/${campaignId}$`), { timeout: 30_000 });
    expect(createRequests).toHaveLength(0);

    const relaunchedCampaign = await page.request.get(`/api/v1/campaigns/${campaignId}`);
    expect(relaunchedCampaign.ok(), await relaunchedCampaign.text()).toBeTruthy();
    expect((await relaunchedCampaign.json()).result.status).not.toBe('draft');

    guard.assertClean('mailpit send');
  });
});
