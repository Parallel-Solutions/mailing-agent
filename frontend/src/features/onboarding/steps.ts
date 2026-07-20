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

export const ONBOARDING_VERSION = 4;

export const ONBOARDING_STEPS: OnboardingStepDefinition[] = [
  {
    id: 'welcome',
    route: '/',
    title: 'Настроим CampaignFlow',
    description: 'Мастер проведёт по реальным формам. Заполняйте поля прямо под подсветкой — введённые данные будут сохранены в системе.',
  },
  {
    id: 'profile-sender',
    route: '/profile',
    target: '[data-onboarding-id="profile-sender"]',
    title: 'Данные отправителя',
    description: 'Укажите имя, компанию и должность. Эти данные используются при подготовке писем и документов.',
  },
  {
    id: 'profile-email',
    route: '/profile',
    target: '[data-onboarding-id="profile-email"]',
    title: 'Почта для уведомлений',
    description: 'Сюда CampaignFlow отправляет служебные уведомления. Поле можно оставить пустым — на отправку рассылок это не влияет.',
  },
  {
    id: 'profile-signature',
    route: '/profile',
    target: '[data-onboarding-id="profile-signature"]',
    title: 'Подпись и часовой пояс',
    description: 'Добавьте подпись, проверьте часовой пояс и нажмите «Сохранить» в форме перед переходом дальше.',
  },
  {
    id: 'connection-open',
    route: '/connections',
    target: '[data-onboarding-id="add-connection"]',
    title: 'Добавьте отправителя',
    description: 'Нажмите подсвеченную кнопку. Мастер продолжится внутри формы подключения.',
    requiresAction: true,
  },
  {
    id: 'connection-method',
    route: '/connections',
    target: '[data-onboarding-id="connection-method"]',
    title: 'Выберите способ отправки',
    description: 'Почтовый ящик подходит для SMTP, а API-ключ — для RuSender или Mailopost.',
    requiresAction: true,
  },
  {
    id: 'connection-details',
    route: '/connections',
    target: '[data-onboarding-id="connection-details"]',
    title: 'Укажите почту',
    description: 'Введите email почтового ящика и нажмите «Определить и продолжить». CampaignFlow подскажет подходящий тип входа.',
    requiresAction: true,
    skipIfTargetMissing: true,
  },
  {
    id: 'connection-auth',
    route: '/connections',
    target: '[data-onboarding-id="connection-auth"]',
    title: 'Выберите тип входа',
    description: 'Проверьте предложенный вариант. Для Gmail, Яндекса и Mail.ru с двухфакторной защитой обычно нужен пароль приложения.',
    skipIfTargetMissing: true,
  },
  {
    id: 'connection-api-provider',
    route: '/connections',
    target: '[data-onboarding-id="connection-api-provider"]',
    title: 'Выберите провайдера',
    description: 'Выберите RuSender или MailoPost. После этого мастер отдельно подсветит API-ключ и подтверждённый email.',
    requiresAction: true,
    skipIfTargetMissing: true,
  },
  {
    id: 'connection-credentials',
    route: '/connections',
    target: '[data-onboarding-id="connection-credentials"]',
    title: 'Укажите данные доступа',
    description: 'Введите пароль или API-ключ. Эти данные хранятся в зашифрованном виде и не отображаются после сохранения.',
  },
  {
    id: 'connection-submit',
    route: '/connections',
    target: '[data-onboarding-id="connection-submit"]',
    title: 'Проверьте подключение',
    description: 'Нажмите рабочую кнопку подключения. Дальше мастер перейдёт только после успешного сохранения отправителя.',
    requiresAction: true,
  },
  {
    id: 'connection-limits',
    route: '/connections',
    target: '[data-onboarding-id="connection-limits"]',
    title: 'Лимиты отправки',
    description: 'Укажите безопасные часовые и дневные лимиты и сохраните их кнопкой формы. Нажмите «Далее», чтобы оставить значения 0 — без лимита.',
  },
  {
    id: 'template-open',
    route: '/templates',
    target: '[data-onboarding-id="add-template"]',
    title: 'Создайте письмо',
    description: 'Нажмите «Добавить письмо». Следующие подсказки помогут выбрать формат и способ создания.',
    requiresAction: true,
  },
  {
    id: 'template-format',
    route: '/templates',
    target: '.ant-modal-content:has([data-onboarding-id="template-format"])',
    title: 'Выберите формат письма',
    description: 'Простое письмо быстрее подготовить, HTML-письмо позволяет использовать визуальный дизайн. Выберите вариант и нажмите «Далее».',
  },
  {
    id: 'template-source',
    route: '/templates',
    target: '.ant-modal-content:has([data-onboarding-id="template-source"])',
    title: 'Выберите основу',
    description: 'Можно сразу взять готовый пример, создать пустой HTML-шаблон или нажать «Добавить» для генерации своего.',
    requiresAction: true,
  },
  {
    id: 'template-custom',
    route: '/templates',
    target: '.ant-modal-content:has([data-onboarding-id="template-custom"])',
    title: 'Опишите собственный шаблон',
    description: 'Выберите модель, опишите письмо или приложите файлы, затем нажмите рабочую кнопку «Создать».',
    requiresAction: true,
  },
  {
    id: 'audience-open',
    route: '/audiences',
    target: '[data-onboarding-id="create-audience"]',
    title: 'Создайте аудиторию',
    description: 'Нажмите кнопку — будет создана база получателей и сразу откроется её содержимое.',
    requiresAction: true,
  },
  {
    id: 'audience-import',
    route: '/audiences',
    target: '[data-onboarding-id="audience-import"]',
    title: 'Загрузите получателей',
    description: 'Импортируйте CSV или XLSX с компаниями, контактами и email. Если файла пока нет, этот шаг можно пройти позже.',
  },
  {
    id: 'campaign-basics',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-basics"]',
    title: 'Основная информация',
    description: 'Задайте понятное название, тему письма и сценарий отправки. Черновик сохраняется автоматически.',
  },
  {
    id: 'campaign-sender',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-sender"]',
    title: 'Выберите отправителя',
    description: 'Укажите подключение, которое настроили ранее. Способ отправки подставится автоматически.',
  },
  {
    id: 'campaign-recipients',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-recipients"]',
    title: 'Подключите аудиторию',
    description: 'Выберите созданную аудиторию или загрузите отдельный файл получателей.',
  },
  {
    id: 'campaign-schedule',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-schedule"]',
    title: 'Настройте расписание',
    description: 'Укажите размер пакета, время старта и интервал. Справа появится прогноз отправки.',
  },
  {
    id: 'campaign-launch',
    route: '/campaigns/new',
    target: '[data-onboarding-id="campaign-step-launch"]',
    title: 'Проверьте и запустите',
    description: 'Исправьте отмеченные ошибки, сохраните сопоставление переменных и отправьте тестовое письмо. Запускайте рассылку только после проверки.',
  },
    {
    id: 'statistics-overview',
    route: '/',
    target: '[data-onboarding-id="statistics-overview"]',
    title: 'Статистика',
    description: 'Здесь собраны показатели отправки, результаты кампаний, получатели, согласия, проблемы и отчёты. Используйте период и фильтры в верхней части страницы.',
  },
  {
    id: 'campaigns-overview',
    route: '/campaigns',
    target: '[data-onboarding-id="campaigns-overview"]',
    title: 'Управление рассылками',
    description: 'Следите за статусом и прогрессом рассылок. Отсюда кампанию можно открыть, отредактировать, дублировать, поставить на паузу или отменить.',
  },
  {
    id: 'chains-overview',
    route: '/chains',
    target: '[data-onboarding-id="chains-overview"]',
    title: 'Конструктор цепочек',
    description: 'Создавайте последовательности писем с задержками и условиями, публикуйте их и подключайте к новым рассылкам.',
  },
{
    id: 'finish',
    route: '/',
    title: 'Первичная настройка завершена',
    description: 'Профиль, отправитель, шаблон, аудитория и черновик рассылки готовы. Мастер можно повторно открыть кнопкой «?» в верхней панели.',
  },
];
