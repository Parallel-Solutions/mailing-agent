export const AUTO_REFRESH_MS = 30 * 1000;
export const DASHBOARD_CACHE_PREFIX = 'stats-dashboard-v3:';
export const PER_PAGE = 10;

export const STATS_TABS = [
  { key: 'dashboard', label: 'Обзор' },
  { key: 'campaign-list', label: 'Рассылки' },
  { key: 'audiences', label: 'Аудитории' },
  { key: 'campaigns', label: 'Показатели рассылок' },
  { key: 'recipients', label: 'Компании' },
  { key: 'campaign-analytics', label: 'Аналитика рассылки' },
  { key: 'campaign-full-analytics', label: 'Полная аналитика' },
  { key: 'marketing-consents', label: 'Подписки и отписки' },
] as const;

export const MANAGEMENT_TAB_KEYS = ['campaign-list', 'audiences'] as const;

export type StatsTabKey = (typeof STATS_TABS)[number]['key'];

export const PAGE_TITLES: Record<StatsTabKey, string> = {
  dashboard: 'Статистика рассылки',
  'campaign-list': 'Рассылки',
  audiences: 'Аудитории',
  campaigns: 'Показатели рассылок',
  recipients: 'Компании и статусы',
  'campaign-analytics': 'Детальная аналитика рассылки',
  'campaign-full-analytics': 'Полная аналитика рассылки',
  'marketing-consents': 'Подписки и отписки',
};

export const RECIPIENT_CHIPS: Array<[string, string]> = [
  ['', 'Все'],
  ['delivered', 'Доставлено'],
  ['opened', 'Открыто'],
  ['clicked', 'Переходы'],
  ['problems', 'Проблемы'],
  ['pending', 'Ожидают'],
  ['action', 'Нужно действие'],
];

export const ACTION_TYPES: Array<[string, string]> = [
  ['call', 'Перезвонить'],
  ['resend', 'Повторить отправку'],
  ['find_another_email', 'Найти другой email'],
  ['create_task', 'Создать задачу'],
];

export const PROVIDER_OPTIONS = [
  { value: '', label: 'Все провайдеры' },
  { value: 'rusender', label: 'RuSender' },
  { value: 'mailopost', label: 'MailoPost' },
  { value: 'unisender', label: 'UniSender' },
  { value: 'smtp', label: 'SMTP' },
];

export const EXPORT_TYPES = [
  { value: 'delivery_summary', label: 'Сводка по доставке' },
  { value: 'sent_mail_log', label: 'Журнал отправок' },
  { value: 'consents', label: 'Согласия' },
  { value: 'email_problems', label: 'Проблемы с email' },
  { value: 'auto_call_contacts', label: 'Контакты для обзвона' },
];

export function isStatsTabKey(value: string | null | undefined): value is StatsTabKey {
  return !!value && STATS_TABS.some((tab) => tab.key === value);
}
