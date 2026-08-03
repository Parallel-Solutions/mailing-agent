import { expect, test, type Page } from '@playwright/test';
import { attachGuard, openAppAuthed } from '../fixtures/ui';

type Step = {
  title: string;
  target?: string;
};

type Chapter = {
  title: string;
  entryPath?: string;
  steps: Step[];
};

const CHAPTERS: Chapter[] = [
  {
    title: 'Общее обучение',
    steps: [
      { title: 'Добро пожаловать в CampaignFlow' },
      { title: 'Подключение отправителя', target: '[data-onboarding-id="add-connection"]' },
      { title: 'Шаблоны писем и документов', target: '[data-onboarding-id="add-template"]' },
      { title: 'Цепочка писем', target: '[data-onboarding-id="create-chain"]' },
      { title: 'Рассылка, цепочка и компания', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-basics-label"]' },
      { title: 'Отправитель', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-sender-label"]' },
      { title: 'Получатели', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-recipients-label"]' },
      { title: 'Расписание и скорость', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-schedule-label"]' },
      { title: 'Проверка и запуск', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-launch-label"]' },
      { title: 'Управление рассылками', target: '[data-onboarding-id="campaigns-overview"] .ant-table-thead th:first-child' },
      { title: 'Результаты рассылок', target: '[data-onboarding-id="statistics-summary"]' },
      { title: 'Общее обучение завершено' },
    ],
  },
  {
    title: 'Компании',
    entryPath: '/companies',
    steps: [
      { title: 'Компании', target: '[data-onboarding-id="companies-overview"]' },
      { title: 'Карточка компании', target: '[data-onboarding-id="company-details"] .ant-form-item:first-child .ant-form-item-label > label' },
      { title: 'Виды работ', target: '[data-onboarding-id="company-work-types"]' },
    ],
  },
  {
    title: 'Подключения',
    entryPath: '/connections',
    steps: [
      { title: 'Подключение отправителя', target: '[data-onboarding-id="add-connection"]' },
      { title: 'Способ отправки', target: '[data-onboarding-id="connection-method"] .ant-form-item-label > label' },
      { title: 'Адрес и параметры почтового ящика', target: '[data-onboarding-id="connection-details"] .ant-steps-item:first-child .ant-steps-item-title' },
      { title: 'Тип входа', target: '[data-onboarding-id="connection-auth"] .ant-form-item-label > label' },
      { title: 'Провайдер API', target: '[data-onboarding-id="connection-api-provider"] .ant-form-item-label > label' },
      { title: 'Данные доступа', target: '[data-onboarding-id="connection-credentials"] .ant-form-item:first-child .ant-form-item-label > label' },
      { title: 'Проверка подключения', target: '[data-onboarding-id="connection-submit"]' },
      { title: 'Лимиты отправки', target: '[data-onboarding-id="connection-rate-limits"]' },
      { title: 'Защита доставки', target: '[data-onboarding-id="connection-delivery-guard"]' },
    ],
  },
  {
    title: 'Шаблоны',
    entryPath: '/templates',
    steps: [
      { title: 'Шаблоны' },
      { title: 'Библиотека писем', target: '[data-onboarding-id="template-library-toolbar"]' },
      { title: 'Шаблоны писем и документов', target: '[data-onboarding-id="add-template"]' },
      { title: 'Формат письма', target: '[data-onboarding-id="template-format"] .ant-card:first-child h5' },
      { title: 'Основа шаблона', target: '[data-onboarding-id="template-source"] .starter-tile:first-child' },
      { title: 'Генерация письма', target: '[data-onboarding-id="template-custom"] > div:first-child .ant-typography' },
      { title: 'Редактирование и предпросмотр', target: '[data-onboarding-id="template-library-toolbar"]' },
      { title: 'Документы и вложения', target: '[data-onboarding-id="template-document-tab"]' },
      { title: 'Библиотека документов', target: '[data-onboarding-id="document-library-toolbar"]' },
      { title: 'Создание документа', target: '[data-onboarding-id="document-add"]' },
      { title: 'Поддерживаемые форматы', target: '[data-onboarding-id="document-formats"]' },
      {
        title: 'Основа документа',
        target: '[data-onboarding-id="document-source"] > .starter-tile:first-child',
      },
      { title: 'Загрузка исходника', target: '[data-onboarding-id="document-upload"]' },
      { title: 'Поля и персонализация', target: '[data-onboarding-id="document-fields"]' },
      { title: 'Предпросмотр и вёрстка', target: '[data-onboarding-id="document-preview"]' },
      { title: 'Документ в цепочке', target: '[data-onboarding-id="document-chain-use"]' },
      { title: 'Обучение по шаблонам завершено' },
    ],
  },
  {
    title: 'Цепочки',
    entryPath: '/chains',
    steps: [
      { title: 'Библиотека цепочек', target: '[data-onboarding-id="chains-list"] .ant-table-thead' },
      { title: 'Создание цепочки', target: '[data-onboarding-id="create-chain"]' },
      { title: 'Название и статус цепочки', target: '[data-onboarding-id="chain-name-status"]' },
      { title: 'Схема писем и переходов', target: '[data-onboarding-id="chain-builder"] .chain-node-block--selected' },
      { title: 'Добавление писем и переходов', target: '[data-onboarding-id="chain-add-nodes"]' },
      { title: 'Шаблон письма в узле', target: '[data-onboarding-id="chain-email-template"]' },
      { title: 'Документы у письма', target: '[data-onboarding-id="chain-documents"]' },
      { title: 'Переход к следующему шагу', target: '[data-onboarding-id="chain-transitions"]' },
      { title: 'Назначение ссылки', target: '[data-onboarding-id="chain-link-purpose"]' },
      { title: 'Сохранение черновика', target: '[data-onboarding-id="chain-save"]' },
      { title: 'Публикация цепочки', target: '[data-onboarding-id="chain-publish-button"]' },
    ],
  },
  {
    title: 'Создание рассылки',
    entryPath: '/campaigns/new',
    steps: [
      { title: 'Создание рассылки' },
      { title: 'Рассылка, цепочка и компания', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-basics-label"]' },
      { title: 'Название рассылки', target: '[data-onboarding-id="campaign-name"] .ant-form-item-label > label' },
      { title: 'Выбор цепочки', target: '[data-onboarding-id="campaign-chain"] .ant-form-item-label > label' },
      { title: 'Компания', target: '[data-onboarding-id="campaign-company"] .ant-form-item-label > label' },
      { title: 'Вид работ', target: '[data-onboarding-id="campaign-work-type"] .ant-form-item-label > label' },
      { title: 'Отправитель', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-sender-label"]' },
      { title: 'Подключение отправителя', target: '[data-onboarding-id="campaign-sender-connection"] .ant-form-item-label > label' },
      { title: 'Получатели', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-recipients-label"]' },
      { title: 'Сохранённая аудитория', target: '[data-onboarding-id="campaign-audience"] .ant-form-item-label > label' },
      { title: 'Загрузка или генерация получателей', target: '[data-onboarding-id="campaign-recipient-sources"]' },
      { title: 'Проверка списка', target: '[data-onboarding-id="campaign-recipient-check"]' },
      { title: 'Расписание и скорость', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-schedule-label"]' },
      { title: 'Размер пакета', target: '[data-onboarding-id="campaign-batch-size"] .ant-form-item-label > label' },
      { title: 'Дата и время старта', target: '[data-onboarding-id="campaign-start-at"] .ant-form-item-label > label' },
      { title: 'Интервал между пакетами', target: '[data-onboarding-id="campaign-interval"] .ant-form-item-label > label' },
      { title: 'Прогноз длительности', target: '[data-onboarding-id="campaign-schedule-preview"]' },
      { title: 'Проверка и запуск', target: '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active [data-onboarding-id="campaign-step-launch-label"]' },
      { title: 'Проверка готовности', target: '[data-onboarding-id="campaign-launch-checks"]' },
      { title: 'Тестовое письмо', target: '[data-onboarding-id="campaign-test-email"]' },
      { title: 'Запуск рассылки', target: '[data-onboarding-id="campaign-start"]' },
    ],
  },
  {
    title: 'Контроль и аналитика',
    entryPath: '/',
    steps: [
      { title: 'Контроль и аналитика' },
      { title: 'Управление рассылками', target: '[data-onboarding-id="campaigns-overview"] .ant-table-thead th:first-child' },
      { title: 'Управление и показатели — разные задачи', target: '[data-onboarding-id="statistics-overview"] .ant-tabs-tab-active' },
      { title: 'Период, рассылка и провайдер', target: '[data-onboarding-id="statistics-filters"]' },
      { title: 'Воронка и проблемные зоны', target: '[data-onboarding-id="statistics-summary"]' },
      { title: 'Сравнение рассылок', target: '[data-onboarding-id="statistics-overview"] .ant-tabs-tab-active' },
      { title: 'Статус рассылки', target: '[data-onboarding-id="campaign-status-column"]' },
      { title: 'Как читать проценты', target: '[data-onboarding-id="campaign-delivery-rate-column"]' },
      { title: 'Компании и статусы', target: '[data-onboarding-id="statistics-overview"] .ant-tabs-tab-active' },
      { title: 'Аналитика конкретной рассылки', target: '[data-onboarding-id="statistics-body-campaign-analytics"]' },
      { title: 'Полная аналитика', target: '[data-onboarding-id="statistics-body-campaign-full-analytics"]' },
      { title: 'Подписки и отписки', target: '[data-onboarding-id="statistics-consents-summary"]' },
      { title: 'Глава об аналитике завершена' },
    ],
  },
];

async function expectSpotlightAligned(page: Page, targetSelector: string) {
  const target = page.locator(`${targetSelector}:visible`).first();
  const spotlight = page.locator('.campaignflow-onboarding__spotlight');

  await expect(target).toBeVisible();
  await expect(spotlight).toBeVisible();
  await expect.poll(async () => {
    const targetBox = await target.boundingBox();
    const spotlightBox = await spotlight.boundingBox();
    if (!targetBox || !spotlightBox) return Number.POSITIVE_INFINITY;
    return Math.max(
      Math.abs(spotlightBox.x - (targetBox.x - 10)),
      Math.abs(spotlightBox.y - (targetBox.y - 10)),
      Math.abs(spotlightBox.width - (targetBox.width + 20)),
      Math.abs(spotlightBox.height - (targetBox.height + 20)),
    );
  }).toBeLessThanOrEqual(2);
}

async function expectCriticalLayersSeparated(page: Page, targetSelector: string) {
  const target = page.locator(`${targetSelector}:visible`).first();
  const panel = page.locator('.campaignflow-onboarding__panel');
  const navigation = page.locator('.campaignflow-onboarding__navigation');
  const [targetBox, panelBox, navigationBox] = await Promise.all([
    target.boundingBox(),
    panel.boundingBox(),
    navigation.boundingBox(),
  ]);
  expect(targetBox).not.toBeNull();
  expect(panelBox).not.toBeNull();
  expect(navigationBox).not.toBeNull();

  const overlapArea = (
    left: NonNullable<typeof targetBox>,
    right: NonNullable<typeof targetBox>,
  ) => (
    Math.max(
      0,
      Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x),
    )
    * Math.max(
      0,
      Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y),
    )
  );

  expect(overlapArea(panelBox!, targetBox!)).toBe(0);
  expect(overlapArea(panelBox!, navigationBox!)).toBe(0);
}

test('all passive onboarding chapters complete without business writes', async ({ page }) => {
  test.setTimeout(240_000);

  let onboardingState = {
    version: 8,
    status: 'completed',
    current_step: 0,
    completed_steps: [] as string[],
    step_count: 85,
    available: true,
    paused_at: null,
    dismissed_at: null,
    completed_at: null,
    updated_at: null,
  };

  await page.route('**/api/v1/onboarding**', async (route) => {
    const request = route.request();
    if (request.method() === 'POST') {
      onboardingState = {
        ...onboardingState,
        status: 'active',
        current_step: 0,
        completed_steps: [],
      };
    } else if (request.method() === 'PATCH') {
      const patch = request.postDataJSON() as Partial<typeof onboardingState>;
      onboardingState = { ...onboardingState, ...patch };
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', result: onboardingState }),
    });
  });

  const businessWrites: string[] = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (
      ['POST', 'PATCH', 'PUT', 'DELETE'].includes(request.method())
      && url.pathname.startsWith('/api/v1/')
      && !url.pathname.startsWith('/api/v1/onboarding')
    ) {
      businessWrites.push(`${request.method()} ${url.pathname}`);
    }
  });

  const guard = await openAppAuthed(page);

  for (const chapter of CHAPTERS) {
    await page.getByRole('button', { name: 'Запустить обучение' }).click();
    await page
      .locator('.ant-dropdown-menu:visible')
      .getByText(chapter.title, { exact: true })
      .click();

    const tour = page.locator('.campaignflow-onboarding');
    const panel = tour.locator('.campaignflow-onboarding__panel');
    const navigation = tour.locator('.campaignflow-onboarding__navigation');
    for (const [index, step] of chapter.steps.entries()) {
      await expect(
        panel.getByRole('heading', { name: step.title, exact: true }),
      ).toBeVisible();
      if (step.target) {
        await expect(tour.locator('.campaignflow-onboarding__connector-path')).toHaveCount(1);
        await expectSpotlightAligned(page, step.target);
      }
      if (chapter.entryPath) {
        expect(new URL(page.url()).pathname).toBe(chapter.entryPath);
      }

      const tourBox = await panel.boundingBox();
      expect(tourBox).not.toBeNull();
      expect(tourBox!.x).toBeGreaterThanOrEqual(0);
      expect(tourBox!.y).toBeGreaterThanOrEqual(0);
      // Browser layout can report quarter-pixel values at device scale 1.
      expect(tourBox!.x + tourBox!.width).toBeLessThanOrEqual(1281);
      expect(tourBox!.y + tourBox!.height).toBeLessThanOrEqual(721);

      const navigationBox = await navigation.boundingBox();
      expect(navigationBox).not.toBeNull();
      expect(Math.abs(navigationBox!.x + navigationBox!.width / 2 - 640)).toBeLessThan(2);
      expect(navigationBox!.y + navigationBox!.height).toBeLessThanOrEqual(721);
      await expect(
        navigation.locator('.campaignflow-onboarding__page'),
      ).toHaveCount(chapter.steps.length);

      if (step.target?.includes('campaign-step-launch-label')) {
        for (const viewport of [
          { width: 768, height: 800 },
          { width: 390, height: 844 },
          { width: 1280, height: 720 },
        ]) {
          await page.setViewportSize(viewport);
          await expectSpotlightAligned(page, step.target);
          await expectCriticalLayersSeparated(page, step.target);
        }
      }

      if (index < chapter.steps.length - 1) {
        await navigation.getByRole('button', { name: 'Далее', exact: true }).click();
        await expect(tour).toBeVisible();
      }
    }

    await navigation.getByRole('button', { name: 'Готово', exact: true }).click();
    await expect(tour).toBeHidden();
    await expect.poll(() => onboardingState.status).toBe('completed');
    if (chapter.entryPath) {
      expect(new URL(page.url()).pathname).toBe(chapter.entryPath);
    }
  }

  expect(businessWrites).toEqual([]);
  guard.assertClean('passive chaptered onboarding');
});

test('an active campaign step survives a cold route mount', async ({ page }) => {
  await page.route('**/api/v1/onboarding**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        result: {
          version: 8,
          status: 'active',
          current_step: 17,
          completed_steps: [],
          step_count: 85,
          available: true,
          paused_at: null,
          dismissed_at: null,
          completed_at: null,
          updated_at: null,
        },
      }),
    });
  });

  const guard = attachGuard(page);
  await page.goto('/campaigns/new?onboarding=1', { waitUntil: 'domcontentloaded' });

  const target =
    '[data-onboarding-id="campaign-wizard"] .ant-collapse-item-active '
    + '[data-onboarding-id="campaign-step-launch-label"]';
  await expect(
    page.locator('.campaignflow-onboarding__panel').getByRole('heading', {
      name: 'Проверка и запуск',
      exact: true,
    }),
  ).toBeVisible();
  await expectSpotlightAligned(page, target);
  await expectCriticalLayersSeparated(page, target);

  guard.assertClean('cold onboarding route mount');
});
