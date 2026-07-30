export type OnboardingStepDefinition = {
  id: string;
  route: string;
  target?: string;
  title: string;
  description: string;
  requiresAction?: boolean;
  nextLabel?: string;
  skipIfTargetMissing?: boolean;
};

export const ONBOARDING_VERSION = 5;

export const ONBOARDING_STEPS: OnboardingStepDefinition[] = [
  {
    id: 'welcome',
    route: '/',
    title: 'Добро пожаловать в ai offer',
    description: 'Пройдём основные настройки вместе. На каждом шаге мы подсветим нужное действие и сохраним результат.',
  },
  {
    id: 'connection-open',
    route: '/connections',
    target: '[data-onboarding-id="add-connection"]',
    title: 'Подключите отправителя',
    description: 'Нажмите выделенную кнопку, чтобы открыть форму подключения почты.',
    requiresAction: true,
  },
  {
    id: 'connection-method',
    route: '/connections',
    target: '[data-onboarding-id="connection-method"]',
    title: 'Выберите способ отправки',
    description: 'Почтовый ящик подходит для SMTP, API-ключ — для RuSender и MailoPost.',
    requiresAction: true,
  },
  {
    id: 'connection-details',
    route: '/connections',
    target: '[data-onboarding-id="connection-details"]',
    title: 'Укажите почтовый ящик',
    description: 'Введите email и нажмите «Определить и продолжить».',
    requiresAction: true,
    skipIfTargetMissing: true,
  },
  {
    id: 'connection-auth',
    route: '/connections',
    target: '[data-onboarding-id="connection-auth"]',
    title: 'Проверьте способ входа',
    description: 'Для ящиков с двухфакторной защитой обычно нужен пароль приложения.',
    skipIfTargetMissing: true,
  },
  {
    id: 'connection-api-provider',
    route: '/connections',
    target: '[data-onboarding-id="connection-api-provider"]',
    title: 'Выберите провайдера',
    description: 'Выберите RuSender или MailoPost — после этого появятся нужные поля.',
    requiresAction: true,
    skipIfTargetMissing: true,
  },
  {
    id: 'connection-credentials',
    route: '/connections',
    target: '[data-onboarding-id="connection-credentials"]',
    title: 'Введите данные доступа',
    description: 'Укажите пароль приложения или API-ключ. Значение будет храниться в зашифрованном виде.',
  },
  {
    id: 'connection-submit',
    route: '/connections',
    target: '[data-onboarding-id="connection-submit"]',
    title: 'Проверьте подключение',
    description: 'Нажмите кнопку подключения. Продолжим после успешной проверки.',
    requiresAction: true,
  },
  {
    id: 'connection-limits',
    route: '/connections',
    target: '[data-onboarding-id="connection-limits"]',
    title: 'Установите лимиты',
    description: 'Задайте безопасные ограничения отправки или оставьте нули, если лимиты не нужны.',
  },
  {
    id: 'template-open',
    route: '/templates',
    target: '[data-onboarding-id="add-template"]',
    title: 'Создайте первое письмо',
    description: 'Нажмите «Добавить письмо» — дальше выберем формат и основу.',
    requiresAction: true,
  },
  {
    id: 'template-format',
    route: '/templates',
    target: '.ant-modal-content:has([data-onboarding-id="template-format"])',
    title: 'Выберите формат',
    description: 'Простой текст быстрее подготовить, HTML даёт больше возможностей для дизайна.',
  },
  {
    id: 'template-source',
    route: '/templates',
    target: '.ant-modal-content:has([data-onboarding-id="template-source"])',
    title: 'Выберите основу',
    description: 'Возьмите готовый пример, пустой шаблон или создайте собственный.',
    requiresAction: true,
  },
  {
    id: 'template-custom',
    route: '/templates',
    target: '.ant-modal-content:has([data-onboarding-id="template-custom"])',
    title: 'Опишите письмо',
    description: 'Выберите модель, добавьте описание или файлы и нажмите «Создать».',
    requiresAction: true,
  },
  {
    id: 'audience-open',
    route: '/?tab=audiences',
    target: '[data-onboarding-id="create-audience"]',
    title: 'Создайте базу получателей',
    description: 'Нажмите выделенную кнопку, чтобы создать новую аудиторию.',
    requiresAction: true,
  },
  {
    id: 'audience-import',
    route: '/?tab=audiences',
    target: '[data-onboarding-id="audience-import"]',
    title: 'Загрузите получателей',
    description: 'Импортируйте CSV или XLSX с компаниями, контактами и email. Это можно сделать позже.',
  },
  {
    id: 'campaign-basics',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-basics"]',
    title: 'Заполните основные данные',
    description: 'Укажите название, тему и сценарий. Изменения сохраняются автоматически. Затем нажмите «К отправителю».',
    nextLabel: 'К отправителю',
  },
  {
    id: 'campaign-sender',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-sender"]',
    title: 'Проверьте отправителя',
    description: 'Отправитель по умолчанию уже может быть выбран. Проверьте адрес. Если всё верно, нажмите «К получателям»; для смены раскройте список.',
    nextLabel: 'К получателям',
  },
  {
    id: 'campaign-recipients',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-recipients"]',
    title: 'Добавьте получателей',
    description: 'Выберите созданную аудиторию или загрузите файл с адресами. После этого нажмите «К расписанию».',
    nextLabel: 'К расписанию',
  },
  {
    id: 'campaign-schedule',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-schedule"]',
    title: 'Настройте расписание',
    description: 'Укажите время старта, размер пакета и интервал между отправками. Затем нажмите «К запуску».',
    nextLabel: 'К запуску',
  },
  {
    id: 'campaign-launch',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-launch"]',
    title: 'Проверьте и запустите',
    description: 'Красные отметки сверху показывают незаполненные разделы. Нажмите название раздела, исправьте ошибки и отправьте тестовое письмо.',
    nextLabel: 'К статистике',
  },
  {
    id: 'statistics-overview',
    route: '/',
    target: '[data-onboarding-id="statistics-overview"]',
    title: 'Следите за результатами',
    description: 'Здесь видны доставка, открытия, переходы и проблемы с email.',
  },
  {
    id: 'campaigns-overview',
    route: '/?tab=campaign-list',
    target: '[data-onboarding-id="campaigns-overview"]',
    title: 'Управляйте рассылками',
    description: 'Открывайте рассылки, следите за прогрессом, ставьте их на паузу или отменяйте.',
  },
  {
    id: 'chains-overview',
    route: '/chains',
    target: '[data-onboarding-id="chains-overview"]',
    title: 'Создавайте цепочки писем',
    description: 'Объединяйте письма в последовательности с задержками и условиями.',
  },
  {
    id: 'finish',
    route: '/',
    title: 'Всё готово',
    description: 'Основные разделы настроены. Повторно запустить обучение можно по кнопке «?».',
  },
];
