/**
 * Reusable chain / template builder helpers for UI-driven campaign journeys.
 * Mirrors the style of appApi.ts / mailpit.ts: plain async functions, no classes.
 */
import { expect, type Page } from '@playwright/test';

/**
 * Opens an AntD `<Select>` located by its Form.Item label text, then clicks the
 * dropdown option whose visible text contains `optionText`.
 *
 * This goes via the Form.Item's DOM structure (`.ant-form-item-label label` sibling
 * to `.ant-select-selector` within the same `.ant-form-item`) rather than
 * `page.getByLabel(...)`, because at least one field this module drives
 * (`ChainNodeSettingsPanel`'s "Шаблон письма" / "Документы", backed by
 * `frontend/src/features/templates/TemplatePickerField.tsx`) is a hand-rolled
 * wrapper around `<Select>` that does not forward AntD Form.Item's generated
 * `id` prop onto the inner control — its prop list is
 * `{ templates, placeholder, disabled, mode, value, onChange }`, with no `id`,
 * so the `<label for="...">` AntD renders never matches any element's `id` and
 * `getByLabel` would fail to resolve it. The Form.Item-structure lookup works
 * for both that wrapper and plain ProFormSelect fields, so it is used
 * everywhere in this module for consistency.
 */
export async function selectAntdOption(
  page: Page,
  formItemLabel: string,
  optionText: string,
  options?: {
    // Types `optionText` into the field before matching, narrowing AntD's
    // option list to what's actually needed. Opt-in (default off, matching
    // the prior behavior exactly) because typing is a no-op for fields
    // without `showSearch` (their search input is `readOnly`) but would
    // silently filter results to zero on a searchable field whose
    // `optionFilterProp` doesn't match this text. Needed for fields backed
    // by lists that grow over repeated test runs (e.g. connections) — AntD
    // virtualizes long dropdowns, so a freshly created option can sit past
    // the render window and a plain DOM text filter never finds it.
    typeToSearch?: boolean;
  },
): Promise<void> {
  const formItem = page
    .locator('.ant-form-item')
    .filter({ has: page.locator('.ant-form-item-label label', { hasText: formItemLabel }) })
    .first();
  await formItem.locator('.ant-select-selector').first().click();
  if (options?.typeToSearch) {
    await page.keyboard.type(optionText);
  }
  await page
    .locator('.ant-select-dropdown:visible .ant-select-item-option', { hasText: optionText })
    .first()
    .click();
}

/**
 * Fills a plain text input located by its Form.Item label text. Used only where
 * `page.getByLabel(...)` would be ambiguous (e.g. the chain node's "Название"
 * field, which shares its exact label text with the chain-level name field
 * rendered on the same page — see `createChainWithRootTemplate`).
 */
async function fillFormItemInput(page: Page, formItemLabel: string, value: string): Promise<void> {
  const formItem = page
    .locator('.ant-form-item')
    .filter({ has: page.locator('.ant-form-item-label label', { hasText: formItemLabel }) })
    .first();
  await formItem.locator('input').first().fill(value);
}

export type SimpleEmailTemplateOptions = {
  name: string;
  subject: string;
  bodyText?: string;
};

/**
 * Creates a "Простое письмо" (TipTap-based) email template via /templates.
 *
 * The wizard's "custom" step (`AddTemplateWizard.tsx` step === 'custom') only
 * offers two ways to finish creating a "simple" template: an AI-generation
 * call (`templatesApi.generate`, which reaches `_call_llm` in
 * `src/campaigns/template_ai.py` and requires a real OpenAI key — not
 * available/deterministic in the e2e stack, see `.env.docker`'s placeholder
 * `OPENAI_API_KEY=replace-with-openai-key`), or picking one of the built-in
 * "starter" tiles on the gallery step. This helper uses a starter
 * (`src/campaigns/template_starters.py` EMAIL_STARTERS, hardcoded Python data,
 * not DB-seeded, so it is always present) via `useStarterMutation`, which is a
 * synchronous DB copy with no external calls — deterministic and fast, and
 * keeps the starter's built-in `{{contact_name}}` token in the body.
 */
export async function createSimpleEmailTemplate(
  page: Page,
  { name, subject, bodyText }: SimpleEmailTemplateOptions,
): Promise<{ templateId: string }> {
  await page.goto('/templates');
  await page.getByRole('tab', { name: 'Шаблон письма' }).click();
  await page.getByRole('button', { name: 'Добавить письмо' }).click();

  // Step 1/3 ("format"): pick the TipTap-based simple editor, not the visual/upload formats.
  await page.locator('[data-onboarding-id="template-format"]').getByText('Простое письмо').click();
  await page.getByRole('button', { name: 'Далее' }).click();

  // Step 2/3 ("gallery"): "Приветствие с предложением" is the first of the four built-in
  // "simple" starters (src/campaigns/template_starters.py EMAIL_STARTERS) and already
  // contains the {{contact_name}} token requested by the test plan.
  await page
    .locator('[data-onboarding-id="template-source"] .starter-tile')
    .filter({ hasText: 'Приветствие с предложением' })
    .click();

  // finishTemplateCreation() navigates to /templates/{id}/edit on success.
  await expect(page).toHaveURL(/\/templates\/[0-9a-f-]{36}\/edit/i, { timeout: 20_000 });
  const templateId = page.url().match(/\/templates\/([0-9a-f-]{36})\/edit/i)?.[1] ?? '';
  expect(templateId).toBeTruthy();

  await page.getByLabel('Название шаблона').fill(name);
  await page.getByLabel('Тема письма').fill(subject);

  // Keep the starter's default {{contact_name}} token in place — just append
  // test-identifying copy so the rendered/sent body is easy to assert on later.
  const body = page.locator('.template-email-canvas .ProseMirror');
  await body.click();
  await page.keyboard.press('Control+End');
  if (bodyText) {
    await page.keyboard.press('Enter');
    await page.keyboard.type(bodyText);
  }

  await page.getByRole('button', { name: 'Сохранить версию' }).click();
  await expect(page.getByText('Создана новая версия шаблона')).toBeVisible({ timeout: 15_000 });

  return { templateId };
}

/**
 * Explicitly clicks "Сохранить" and waits for it to finish, rather than
 * relying on `EmailChainBuilderPage.tsx`'s own debounced autosave
 * (`debounceRef` → `saveMutation.mutate()` after an edit). `publishMutation`
 * reads the server-persisted chain to run `validate_chain(strict=True)`
 * (`src/campaigns/chain_service.py`) — if "Опубликовать" is clicked before
 * the debounce fires, the server still has the pre-edit payload (e.g. a
 * root node with `email_template_id: null`), so publish fails silently into
 * an antd `message.error(...)` toast the caller isn't checking for. Explicit
 * save removes the race entirely regardless of the debounce's exact delay.
 */
async function saveChainAndWait(page: Page): Promise<void> {
  const saveButton = page.getByRole('button', { name: 'Сохранить' });
  await saveButton.click();
  // AntD's `<Button loading>` adds the `ant-btn-loading` class for the
  // mutation's duration; wait for it to clear rather than a fixed delay.
  await expect(page.locator('button.ant-btn-loading', { hasText: 'Сохранить' })).toHaveCount(0, {
    timeout: 15_000,
  });
}

export type ChainWithRootTemplateOptions = {
  chainName: string;
  emailTemplateName: string;
};

/**
 * Creates a chain via /chains, names it, points its (already-selected-by-default)
 * root node at an existing email template, and publishes.
 */
export async function createChainWithRootTemplate(
  page: Page,
  { chainName, emailTemplateName }: ChainWithRootTemplateOptions,
): Promise<{ chainId: string }> {
  await page.goto('/chains');
  // toolBarRender always renders one "Создать цепочку" button; ProTable's empty-state
  // locale adds a second, identical one when the list has no rows yet — `.first()`
  // handles either case without depending on whether other specs left chains behind.
  await page.getByRole('button', { name: 'Создать цепочку' }).first().click();

  // Chain ids are `uuid.uuid4().hex[:12]` (src/campaigns/chain_service.py::_new_id),
  // i.e. 12 lowercase hex chars with no dashes — not a full 36-char UUID like
  // campaign/template ids.
  await expect(page).toHaveURL(/\/chains\/[0-9a-f]{12}\b/i, { timeout: 20_000 });
  const chainId = page.url().match(/\/chains\/([0-9a-f]{12})\b/i)?.[1] ?? '';
  expect(chainId).toBeTruthy();

  // Chain-level name field: `<label class="email-chain-name-field"><span>Название</span>
  // <Input/></label>` (EmailChainBuilderPage.tsx). Scoped by class because the root
  // node's OWN "Название" field (ChainNodeSettingsPanel, a separate ProFormText) uses
  // the exact same label text and is visible on the same page at the same time.
  await page.locator('.email-chain-name-field input').fill(chainName);

  // The root node is selected by default (no ?node= param yet), and its settings
  // panel is open by default (EditorSideAccordion defaultActiveKey="settings").
  await selectAntdOption(page, 'Шаблон письма', emailTemplateName);

  await saveChainAndWait(page);
  await page.getByRole('button', { name: 'Опубликовать' }).click();
  await expect(page.getByText('Цепочка опубликована')).toBeVisible({ timeout: 15_000 });

  return { chainId };
}

export type DocumentFollowupNodeOptions = {
  childName: string;
  emailTemplateName: string;
  documentTemplateNames: string[];
};

/**
 * On an already-open chain builder (root node selected), adds a child "Письмо"
 * node off the root, names it, gives it an email template, attaches one or
 * more document templates to it, and publishes.
 *
 * `emailTemplateName` is required, not optional: `validate_chain(..., strict=True)`
 * (`src/campaigns/chain_service.py:414-422`) rejects publishing ANY email-kind
 * node — including children, not just the root — that has no
 * `email_template_id`, with the strict error
 * `f"У блока «{name}» не выбран шаблон письма"`. `addChildEmailNode`
 * (`frontend/src/features/campaigns/chain/chainUtils.ts:192-203`) creates new
 * child nodes with `email_template_id: null`, so this must be set explicitly
 * before publishing or the "Опубликовать" click fails silently into an
 * antd `message.error(...)` toast instead of the success toast this helper
 * awaits. Reusing the same template as the root node is fine — nothing in the
 * chain model requires distinct templates per node.
 *
 * The "+" add-node button has no accessible name/role/label/data-onboarding-id
 * — confirmed in `frontend/src/features/campaigns/chain/ChainNodeBlock.tsx`:
 * it is an icon-only AntD `<Button icon={<PlusOutlined />} className="chain-node-block__add" />`
 * with no `aria-label`/`title`/text content, so `.chain-node-block__add` (CSS
 * class) is the only viable selector. This is the one pre-approved exception
 * to "no new selectors" in the governing plan — no new test-id was added
 * because the class selector already works.
 */
export async function addDocumentFollowupNode(
  page: Page,
  { childName, emailTemplateName, documentTemplateNames }: DocumentFollowupNodeOptions,
): Promise<void> {
  await page.locator('.chain-node-block .chain-node-block__add').first().click();
  await page.getByRole('menuitem', { name: 'Письмо' }).click();

  // Scoped via Form.Item structure, not getByLabel('Название') — the chain-level
  // name field (see createChainWithRootTemplate) shares the same label text and
  // is still present on the page.
  await fillFormItemInput(page, 'Название', childName);
  await selectAntdOption(page, 'Шаблон письма', emailTemplateName);
  for (const documentName of documentTemplateNames) {
    await selectAntdOption(page, 'Документы', documentName);
  }

  await saveChainAndWait(page);
  await page.getByRole('button', { name: 'Опубликовать' }).click();
  await expect(page.getByText('Цепочка опубликована')).toBeVisible({ timeout: 15_000 });
}

/**
 * Selects an already-created chain in the campaign wizard's Basics step
 * (`div[data-onboarding-id="campaign-chain"]`, field `email_chain_id`).
 *
 * Deviates from the governing plan's literal `{chainId}` signature: AntD's
 * `<Select>` dropdown only exposes each chain's visible `name` as option text
 * (`ProFormSelect options={chainOptions}` where `chainOptions` map
 * `{ label: chain.name, value: chain.id }` — see `CampaignNewPage.tsx`), and
 * there is no DOM hook exposing the underlying id on the rendered option, so
 * selection has to happen by name, not id.
 */
export async function linkChainToCampaign(page: Page, { chainName }: { chainName: string }): Promise<void> {
  await selectAntdOption(page, 'Цепочка писем', chainName);
}
