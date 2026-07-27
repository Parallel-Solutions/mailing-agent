import { companyEmailsText, companyField } from './utils';
import { formatLocalDateTime } from '@/utils/dateTime';

export type DrillColumn = [string, (item: Record<string, unknown>) => unknown];

export type DrillConfig = {
  title: string;
  source: 'recipients' | 'consents' | 'email-problems' | 'campaigns' | 'reports';
  columns: DrillColumn[];
  params?: Record<string, string>;
  filter?: (item: Record<string, unknown>) => boolean;
};

const RECIPIENT_COLUMNS: DrillColumn[] = [
  ['Компания', (item) => item.organization],
  ['Регион', (item) => companyField(item, 'region')],
  ['ИНН', (item) => companyField(item, 'inn')],
  ['Контакты', (item) => companyEmailsText(item)],
  ['Статус', (item) => (item.manager_status as { label?: string } | undefined)?.label],
  ['Последнее событие', (item) => item.last_event_label],
  ['Дата события', (item) => formatLocalDateTime(String(item.last_event_at || ''))],
  ['Интерес', (item) => (item.interest as { label?: string } | undefined)?.label],
  ['Следующее действие', (item) => (item.next_action as { label?: string } | undefined)?.label],
];

const CONSENT_COLUMNS: DrillColumn[] = [
  ['Компания', (item) => item.organization],
  ['Контакт', (item) => item.contact],
  ['Email', (item) => item.email],
  ['Статус согласия', (item) => item.consent_status_label],
  ['Материалы', (item) => item.materials_label],
  ['Последнее действие', (item) => item.last_action_label],
  ['Дата', (item) => formatLocalDateTime(String(item.last_action_at || ''))],
  ['Интерес', (item) => (item.interest as { label?: string } | undefined)?.label],
  ['Следующее действие', (item) => (item.next_action as { label?: string } | undefined)?.label],
];

const CAMPAIGN_COLUMNS: DrillColumn[] = [
  ['Название', (item) => item.title],
  ['Период', (item) => item.period_label],
  ['Провайдер', (item) => item.provider_label],
  ['Отправлено', (item) => item.sent],
  ['Доставлено', (item) => `${item.delivered} / ${item.delivery_rate}%`],
  ['Открыто', (item) => `${item.opened} / ${item.open_rate}%`],
  ['Переходы', (item) => `${item.clicked} / ${item.ctr}%`],
  ['Согласия', (item) => item.consents],
  ['Статус', (item) => item.status_label],
];

const PROBLEM_COLUMNS: DrillColumn[] = [
  ['Компания', (item) => item.organization],
  ['Контакты', (item) => companyEmailsText(item)],
  ['Причина', (item) => item.bounce_reason_label],
  ['Провайдер', (item) => item.provider],
  ['Писем', (item) => item.attempts],
  ['Последнее событие', (item) => formatLocalDateTime(String(item.last_event_at || ''))],
  ['Рекомендация', (item) => (item.recommended_action as { label?: string } | undefined)?.label],
];

const REPORT_COLUMNS: DrillColumn[] = [
  ['Отчёт', (item) => item.report_type],
  ['Период', (item) => `${item.period_from || ''} — ${item.period_to || ''}`],
  ['Формат', (item) => item.format],
  ['Создан', (item) => formatLocalDateTime(String(item.created_at || ''))],
  ['Автор', (item) => item.author],
  ['Статус', (item) => item.status],
];

function statusKey(item: Record<string, unknown>) {
  return (item.manager_status as { key?: string } | undefined)?.key;
}

export const DRILLDOWN_CONFIG: Record<string, DrillConfig> = {
  sent: { title: 'Компании в рассылке', source: 'recipients', columns: RECIPIENT_COLUMNS, params: {} },
  delivered: {
    title: 'Доставлено',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: { quick_filter: 'delivered' },
  },
  opened: {
    title: 'Открыто',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: { quick_filter: 'opened' },
  },
  clicked: {
    title: 'Переходы',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: { quick_filter: 'clicked' },
  },
  problems: {
    title: 'Ошибки',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: { quick_filter: 'problems' },
  },
  pending: {
    title: 'Ожидают статуса',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: { quick_filter: 'pending' },
  },
  consents: { title: 'Согласия', source: 'consents', columns: CONSENT_COLUMNS, params: {} },
  materials: {
    title: 'Материалы отправлены',
    source: 'consents',
    columns: CONSENT_COLUMNS,
    params: {},
    filter: (item) => item.materials_label === 'Материалы отправлены',
  },
  errors: {
    title: 'Недоставлено',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: {},
    filter: (i) => ['email_broken', 'soft_bounce', 'delivery_error', 'spam'].includes(statusKey(i) || ''),
  },
  kp_layout: {
    title: 'КП не влезло на 1 стр.',
    source: 'recipients',
    columns: [
      ...RECIPIENT_COLUMNS.slice(0, 5),
      ['Ошибка', (item) => item.error || item.comment],
    ],
    params: {},
    filter: (i) => i.layout_error_code === 'kp_font_compact',
  },
  unsub_spam: {
    title: 'Отписки и спам',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: {},
    filter: (i) => ['unsubscribed', 'spam'].includes(statusKey(i) || ''),
  },
  recipients_active: {
    title: 'Активные получатели',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: {},
    filter: (i) => ['opened', 'clicked'].includes(statusKey(i) || ''),
  },
  recipients_call: {
    title: 'Нужно перезвонить',
    source: 'recipients',
    columns: RECIPIENT_COLUMNS,
    params: {},
    filter: (i) => (i.next_action as { key?: string } | undefined)?.key === 'call',
  },
  campaigns_all: { title: 'Все рассылки', source: 'campaigns', columns: CAMPAIGN_COLUMNS, params: {} },
  campaigns_active: {
    title: 'Активные рассылки',
    source: 'campaigns',
    columns: CAMPAIGN_COLUMNS,
    params: {},
    filter: (i) => i.status === 'active',
  },
  campaigns_completed: {
    title: 'Завершённые рассылки',
    source: 'campaigns',
    columns: CAMPAIGN_COLUMNS,
    params: {},
    filter: (i) => i.status === 'completed',
  },
  campaigns_draft: {
    title: 'Черновики',
    source: 'campaigns',
    columns: CAMPAIGN_COLUMNS,
    params: {},
    filter: (i) => i.status === 'draft',
  },
  campaigns_scheduled: {
    title: 'Запланированные рассылки',
    source: 'campaigns',
    columns: CAMPAIGN_COLUMNS,
    params: {},
    filter: (i) => i.status === 'scheduled',
  },
  campaigns_delivery: {
    title: 'Доставляемость по рассылкам',
    source: 'campaigns',
    columns: CAMPAIGN_COLUMNS,
    params: {},
  },
  campaigns_open: {
    title: 'Открываемость по рассылкам',
    source: 'campaigns',
    columns: CAMPAIGN_COLUMNS,
    params: {},
  },
  consents_confirmed: {
    title: 'Дали согласие',
    source: 'consents',
    columns: CONSENT_COLUMNS,
    params: { consent_status: 'confirmed' },
  },
  consents_opened: {
    title: 'Открыли после согласия',
    source: 'consents',
    columns: CONSENT_COLUMNS,
    params: { consent_status: 'confirmed' },
    filter: (i) => !!i.materials_sent_at,
  },
  consents_call: {
    title: 'Нужно перезвонить',
    source: 'consents',
    columns: CONSENT_COLUMNS,
    params: {},
    filter: (i) => (i.interest as { key?: string } | undefined)?.key === 'high',
  },
  problems_all: {
    title: 'Проблемные адреса',
    source: 'email-problems',
    columns: PROBLEM_COLUMNS,
    params: {},
  },
  problems_hard: {
    title: 'Постоянные ошибки',
    source: 'email-problems',
    columns: PROBLEM_COLUMNS,
    params: {},
    filter: (i) => statusKey(i) === 'email_broken',
  },
  problems_soft: {
    title: 'Временные ошибки',
    source: 'email-problems',
    columns: PROBLEM_COLUMNS,
    params: {},
    filter: (i) => statusKey(i) === 'soft_bounce',
  },
  reports_all: { title: 'Все отчёты', source: 'reports', columns: REPORT_COLUMNS, params: {} },
  reports_xlsx: {
    title: 'Excel выгрузки',
    source: 'reports',
    columns: REPORT_COLUMNS,
    params: {},
    filter: (i) => i.format === 'xlsx',
  },
  reports_csv: {
    title: 'CSV выгрузки',
    source: 'reports',
    columns: REPORT_COLUMNS,
    params: {},
    filter: (i) => i.format === 'csv',
  },
  reports_ndjson: {
    title: 'NDJSON журналы',
    source: 'reports',
    columns: REPORT_COLUMNS,
    params: {},
    filter: (i) => i.format === 'ndjson',
  },
};
