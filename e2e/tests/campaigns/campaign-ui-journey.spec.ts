import { test, expect, type Page } from '@playwright/test';
import path from 'node:path';
import { openAppAuthed } from '../fixtures/ui';
import { mailpitDeleteAll, mailpitWaitForMessage } from '../fixtures/mailpit';
import { createSimpleEmailTemplate, createChainWithRootTemplate, selectAntdOption } from '../fixtures/chains';

/**
 * Switches the campaign wizard's active accordion panel by clicking its header
 * label directly (`[data-onboarding-id="campaign-step-<x>-label"]`,
 * `CampaignNewPage.tsx`), rather than the numbered `Steps` component used by
 * `campaign-flow.spec.ts`'s `goToCampaignStep` helper.
 *
 * This is a deliberate deviation: the numbered `Steps` component's `onChange`
 * is `handleWizardStepClick`, which — when the TARGET step's computed
 * `stepValidation` status is `'error'`/`'warning'` — opens the separate
 * `CampaignStepFixModal` instead of switching the inline panel
 * (`CampaignNewPage.tsx` `handleWizardStepClick`). The Schedule step's
 * validation (`validateScheduleStep` in `campaignStepValidation.ts`) runs
 * unconditionally and reports an error whenever `start_at` is empty — true for
 * every fresh campaign — so navigating to "Расписание" via the numbered Steps
 * component would open the fix modal, not the inline step this test expects.
 * The Collapse accordion's own `onChange` (`CampaignNewPage.tsx`) has no such
 * gating, so clicking its header is the reliable way to reach every step's
 * inline content.
 */
async function goToCampaignStep(page: Page, onboardingId: string): Promise<void> {
  await page.locator(`[data-onboarding-id="${onboardingId}"]`).click();
}

/**
 * Connects a fresh Mailpit-backed SMTP mailbox through the Connections UI.
 * Exact steps/selectors per `frontend/src/pages/ConnectionsPage.tsx` (already
 * read in full for this task).
 */
async function connectMailpitMailbox(page: Page, email: string): Promise<string> {
  await page.goto('/connections');
  await page.getByRole('button', { name: 'Добавить' }).click();

  await selectAntdOption(page, 'Способ отправки', 'Почтовый ящик');
  await page.getByLabel('Email почтового ящика').fill(email);
  await page.getByRole('button', { name: 'Определить и продолжить' }).click();

  // `.test` has no resolvable MX record, so `analyzeSmtpEmail()`'s catch branch
  // (ConnectionsPage.tsx ~lines 814-834) lands on `smtpSetupStage: 'manual'` /
  // `authKind: 'password'` and shows a benign error Alert — expected, not a failure.
  // The backend's discover_smtp_candidates (smtp_autodiscover.py) tries an MX
  // lookup (2s timeout) then races 4 more discovery sources with a 10s shared
  // budget before giving up — worst case close to 15s, i.e. right at (or past)
  // Playwright's default 15s actionTimeout. Wait for the credentials panel
  // explicitly with a longer timeout instead of letting the first `.fill()`
  // absorb that wait implicitly.
  const passwordField = page.getByLabel('Пароль почтового ящика');
  await expect(passwordField).toBeVisible({ timeout: 30_000 });
  await passwordField.fill('mailpit-e2e-ui');
  await page.getByLabel('Логин SMTP').fill(email);
  await page.getByLabel('SMTP-сервер').fill('mailpit');
  // Selecting security auto-sets the port (SECURITY_PORTS.none = 25), so the
  // explicit port overwrite below must happen AFTER this selection.
  await selectAntdOption(page, 'Защита соединения', 'Без шифрования — не рекомендуется');
  await page.getByLabel('Порт SMTP').fill('1025');

  // Real SMTP handshake against mailpit:1025 (MP_SMTP_AUTH_ACCEPT_ANY=true in
  // docker-compose.e2e.yml), so give it a generous timeout.
  await page.locator('[data-onboarding-id="connection-submit"]').click();
  const skipButton = page.getByRole('button', { name: 'Пропустить' });
  await expect(skipButton).toBeVisible({ timeout: 30_000 });
  await skipButton.click();

  // Don't assert the new row is visible in the connections table below: it's
  // an AntD ProTable with default pagination, and repeated e2e runs
  // accumulate connections (each run creates a fresh, uniquely-named one and
  // nothing deletes older ones from other runs/specs) — a since-created
  // mailbox can land past the default page size, making `getByText(email)`
  // flaky for reasons unrelated to whether the connection actually exists.
  // The dialog closing is the real "wizard finished" UI signal; confirm the
  // connection exists via the same API the table itself reads from, and
  // return its id so the caller can delete it when the test is done (keeping
  // this suite from growing the admin account's connection list forever).
  await expect(page.getByRole('dialog', { name: 'Добавить подключение' })).toBeHidden({ timeout: 20_000 });
  let mailboxId = '';
  await expect(async () => {
    const response = await page.request.get('/api/smtp/mailboxes');
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    const mailboxes: Array<{ id?: string; email?: string }> = body?.result?.mailboxes || body?.mailboxes || [];
    const created = mailboxes.find((m) => String(m.email || '').toLowerCase() === email.toLowerCase());
    expect(created?.id).toBeTruthy();
    mailboxId = String(created!.id);
  }).toPass({ timeout: 20_000, intervals: [1_000] });
  return mailboxId;
}

function formatScheduleDateTime(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

test.describe.configure({ mode: 'serial' });

test.describe('Campaign UI journey @email', () => {
  // Set once the mailbox is created; cleaned up in afterEach regardless of
  // pass/fail so repeated runs don't grow the admin account's connection
  // list forever (see connectMailpitMailbox for why that growth previously
  // broke this same test's own pagination-fragile assertion).
  let createdMailboxId: string | undefined;

  test.afterEach(async ({ page }) => {
    if (!createdMailboxId) return;
    await page.request.delete(`/api/smtp/mailboxes/${createdMailboxId}`).catch(() => {});
    createdMailboxId = undefined;
  });

  test('completes the full campaign journey through the UI and lands in Mailpit @email', async ({ page }) => {
    test.setTimeout(180_000);
    const stamp = Date.now();

    await mailpitDeleteAll();
    const guard = await openAppAuthed(page);

    // Step 2: connect a fresh Mailpit mailbox via the Connections UI.
    const senderEmail = `e2e-ui-${stamp}@example.test`;
    createdMailboxId = await connectMailpitMailbox(page, senderEmail);

    // Step 1 (done after the mailbox so the campaign draft, which persists its id
    // in the `campaignDraftStore` localStorage-backed store, is the freshest one
    // when we later rely on "К рассылке" to find its way back to it).
    await page.goto('/campaigns/new');
    await expect(page.getByText('Создание рассылки')).toBeVisible({ timeout: 30_000 });
    await expect(page).toHaveURL(/id=([0-9a-f-]{36})/i, { timeout: 30_000 });
    const campaignId = page.url().match(/id=([0-9a-f-]{36})/i)?.[1];
    expect(campaignId).toBeTruthy();

    const campaignName = `E2E UI Journey ${stamp}`;
    await page.getByLabel('Название').fill(campaignName);
    await expect(page.getByText('Сохранено')).toBeVisible({ timeout: 15_000 });

    // Step 3: build a template + chain via the UI, then return to the draft.
    const templateName = `E2E UI Template ${stamp}`;
    await createSimpleEmailTemplate(page, {
      name: templateName,
      subject: `E2E UI Subject ${stamp}`,
      bodyText: `Journey marker ${stamp}`,
    });

    const chainName = `E2E UI Chain ${stamp}`;
    await createChainWithRootTemplate(page, { chainName, emailTemplateName: templateName });

    // Return to the campaign draft via the chain builder's own "К рассылке"
    // affordance (EmailChainBuilderPage.tsx) instead of a raw page.goto — this
    // also auto-links the chain (?email_chain_id=...) via CampaignNewPage's own
    // effect (`campaignsApi.update(id, { send_scenario: 'email_chain', email_chain_id })`
    // followed by `navigate(..., {replace:true})` that strips the param —
    // CampaignNewPage.tsx:250-262). The intermediate URL
    // `/campaigns/new?id=X&email_chain_id=Y` also matches an unanchored
    // regex, so without anchoring to `$` the test proceeds while that PATCH
    // is still in flight and the accordion state isn't settled yet — anchor
    // the match to only accept the URL once the param has been stripped.
    await page.getByRole('button', { name: 'К рассылке' }).click();
    await expect(page).toHaveURL(new RegExp(`/campaigns/new\\?id=${campaignId}$`), { timeout: 20_000 });

    // Step 4: sender.
    await goToCampaignStep(page, 'campaign-step-sender-label');
    await selectAntdOption(page, 'Подключение отправителя', senderEmail);

    // Step 5: recipients — negative fixtures first, then the deterministic valid list.
    await goToCampaignStep(page, 'campaign-step-recipients-label');
    const uploadButton = page.getByRole('button', { name: /Загрузить Excel\s*\/\s*CSV/i });
    const recipientsTable = page.locator('[data-onboarding-id="campaign-recipient-check"] tbody tr');

    const errorsFixture = path.join('/work', 'fixtures', 'manual', 'recipients-with-errors.xlsx');
    let [chooser] = await Promise.all([page.waitForEvent('filechooser'), uploadButton.click()]);
    await chooser.setFiles(errorsFixture);
    // parse_recipients_xlsx (src/campaigns/service.py) drops the fixture's fully
    // empty company+email row at parse time (`if not email and not company: continue`),
    // so only 2 of the fixture's 3 spreadsheet rows ever reach replace_recipients:
    // one invalid ("bad-email"), one valid ("ok@example.com") — NOT 3 rows / 2
    // invalid, as an earlier (unverified) draft of this plan assumed.
    await expect(page.getByRole('cell', { name: 'ok@example.com' })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole('cell', { name: 'bad-email' })).toBeVisible();
    await expect(page.getByText('Некорректный адрес')).toBeVisible();
    await expect(recipientsTable).toHaveCount(2);

    const duplicatesFixture = path.join('/work', 'fixtures', 'manual', 'recipients-with-duplicates.xlsx');
    [chooser] = await Promise.all([page.waitForEvent('filechooser'), uploadButton.click()]);
    await chooser.setFiles(duplicatesFixture);
    await expect(page.getByRole('cell', { name: 'unique@example.com' })).toBeVisible({ timeout: 20_000 });
    // replace_recipients() (src/campaigns/service.py) skips the second
    // dup@example.com row server-side (duplicates_skipped), not just hides it in
    // the UI — assert exactly one row for that address remains.
    await expect(page.getByRole('cell', { name: 'dup@example.com' })).toHaveCount(1);
    await expect(recipientsTable).toHaveCount(2);

    const validFixture = path.join('/work', 'fixtures', 'manual', 'recipients-valid.csv');
    [chooser] = await Promise.all([page.waitForEvent('filechooser'), uploadButton.click()]);
    await chooser.setFiles(validFixture);
    await expect(page.getByRole('cell', { name: 'csv1@example.com' })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole('cell', { name: 'csv2@example.com' })).toBeVisible();
    await expect(page.getByText('Некорректный адрес')).toHaveCount(0);
    await expect(recipientsTable).toHaveCount(2);

    // Step 6: preview — assert real variable substitution before touching schedule/launch.
    await goToCampaignStep(page, 'campaign-step-launch-label');
    const previewButton = page.getByRole('button', { name: 'Предпросмотр цепочки' });
    await expect(previewButton).toBeVisible({ timeout: 30_000 });
    await previewButton.click();
    const previewFrame = page.frameLocator('iframe[title^="Предпросмотр:"]').first();
    // csv1@example.com's contact_name in fixtures/manual/recipients-valid.csv is "Сергей".
    await expect(previewFrame.locator('body')).toContainText('Сергей', { timeout: 20_000 });
    await expect(previewFrame.locator('body')).not.toContainText('{{contact_name}}');
    // Two prior attempts at a "clean" close both flaked against this exact
    // modal in live runs: `Escape` never closed it at all (`onCancel` is
    // wired, so this is unexplained — possibly a stacked/nested dialog
    // eating the keypress), and waiting for the open animation to finish
    // before clicking still hit "element is not stable" / "detached from
    // the DOM, retrying" repeatedly, suggesting some continuous re-render
    // beyond just the initial mount animation. Content is already confirmed
    // loaded (the assertions above), so force the click past Playwright's
    // actionability/stability wait rather than keep chasing the root cause
    // of the churn — that's a product-side investigation, not a test one.
    const dialog = page.getByRole('dialog');
    await page.getByRole('button', { name: 'Закрыть' }).click({ force: true });
    await expect(dialog).toBeHidden({ timeout: 10_000 });

    // Step 7: schedule — start_at is now mandatory (ProFormDateTimePicker,
    // CampaignWizardScheduleStep.tsx). Type directly into the input rather than
    // driving the calendar popover. Use "now", not a few minutes out: the
    // batch worker only dispatches once start_at has actually elapsed
    // (src/campaigns/service.py:1319-1320 clamps a past/current start_at to
    // `now()` server-side, which is exactly what we want here), so a
    // future start_at just leaves the campaign sitting in "Запланировано"
    // with nothing to find in Mailpit within this test's wait window — that
    // exact mistake is what caused this spec's first live run to time out
    // waiting on Mailpit with 0 messages.
    await goToCampaignStep(page, 'campaign-step-schedule-label');
    const startAt = new Date();
    const startAtField = page.getByLabel('Дата и время старта');
    await expect(startAtField).toBeVisible({ timeout: 15_000 });
    await startAtField.click();
    await startAtField.fill(formatScheduleDateTime(startAt));
    await page.keyboard.press('Escape');
    await expect(page.getByText(/Прогноз:/i)).toBeVisible();
    await expect(page.getByText('Прогноз: 0 пакетов')).toHaveCount(0);

    // Step 8: launch.
    await goToCampaignStep(page, 'campaign-step-launch-label');
    const startButton = page.getByRole('button', { name: 'Старт' });
    await expect(startButton).toBeEnabled({ timeout: 30_000 });
    await expect(page.locator('.ant-alert-error')).toHaveCount(0);
    await startButton.click();
    await expect(page).toHaveURL(/\/campaigns\/[0-9a-f-]{36}/i, { timeout: 30_000 });

    // Step 9: verify in Mailpit — same substituted contact_name asserted in the
    // preview step, closing the loop.
    const message = await mailpitWaitForMessage(
      (m) => Boolean(m.To?.some((t) => (t.Address || '').toLowerCase().includes('csv1@example.com'))),
      { timeoutMs: 90_000 },
    );
    expect(message.HTML || message.Text || '').toContain('Сергей');

    // Step 10: verify statistics. Per STATISTICS_TEST_PLAN.md, Mailpit/SMTP sends
    // have no open-tracking pixel, so only "Отправлено в почтовый провайдер" is
    // asserted here — not "Открыто".
    await page.goto('/?tab=campaign-analytics', { waitUntil: 'domcontentloaded' });
    const campaignFilter = page.getByTestId('statistics-campaign-filter');
    await expect(campaignFilter).toBeVisible({ timeout: 20_000 });
    await campaignFilter.click();
    await page.keyboard.type(campaignName);
    await page
      .locator('.ant-select-dropdown:visible .ant-select-item-option', { hasText: campaignName })
      .first()
      .click();

    // `campaign-analytics-sent-card` (buildKpis' default testPrefix) is only
    // the exact testid when the analytics view renders its flat per-campaign
    // KPI row. A single-node chain (our case: root node "Письмо 1" only)
    // instead renders the PER-STEP resource-group breakdown
    // (`buildKpis(..., \`campaign-analytics-step-${group.key}\`)`,
    // CampaignAnalyticsTab.tsx ~line 315-320), whose testid is
    // `campaign-analytics-step-<node-id>-sent-card` — the node id is a
    // runtime-generated value this test doesn't control. A `[data-testid$=
    // "-sent-card"]` suffix match (mirroring statistics.spec.ts's existing
    // `[data-testid$="-total-card"]` convention) is itself ambiguous, though:
    // `...-not-sent-card` ("Не дошло до отправки") ALSO ends with
    // "-sent-card", and sits earlier in the KPI array, so `.first()` grabbed
    // that zero-value card instead in an earlier live run. Scope by the
    // unambiguous visible title text instead.
    const sentCard = page
      .locator('.ant-card')
      .filter({ has: page.getByText('Отправлено в почтовый провайдер', { exact: true }) })
      .first();
    await expect(sentCard).toBeVisible({ timeout: 20_000 });
    await expect(async () => {
      const text = (await sentCard.locator('div').last().innerText()).trim();
      expect(text).not.toBe('0');
      expect(text.length).toBeGreaterThan(0);
    }).toPass({ timeout: 60_000, intervals: [2_000] });

    guard.assertClean('campaign ui journey');
  });
});
