export type MetricGlossaryEntry = {
  id: string;
  title: string;
  description: string;
  formula: string;
  source: string;
};

export const METRIC_GLOSSARY: Record<string, MetricGlossaryEntry> = {
  sent: {
    id: 'sent',
    title: 'Компаний в рассылке',
    description: 'Число компаний (получателей), по которым зафиксирована хотя бы одна отправка письма.',
    formula: 'Количество уникальных row_id в журнале отправок sent_mail_log.',
    source: 'PostgreSQL job_events → stream sent_mail_log.',
  },
  delivered: {
    id: 'delivered',
    title: 'Доставлено',
    description: 'Компании, у которых провайдер подтвердил доставку или более высокий статус (открытие, клик).',
    formula: 'manager_status ∈ {delivered, opened, clicked}.',
    source: 'Объединение sent_mail_log и webhook-событий провайдера (JSONL).',
  },
  opened: {
    id: 'opened',
    title: 'Открыто',
    description: 'Компании, у которых зафиксировано открытие письма или переход по ссылке.',
    formula: 'manager_status ∈ {opened, clicked}.',
    source: 'Webhook провайдера (RuSender, MailoPost, UniSender Go).',
  },
  clicked: {
    id: 'clicked',
    title: 'Переходы',
    description: 'Компании, по которым был клик по ссылке в письме (провайдерский click-tracking).',
    formula: 'manager_status = clicked.',
    source: 'Webhook провайдера; собственный click-proxy не используется.',
  },
  errors: {
    id: 'errors',
    title: 'Ошибки доставки',
    description: 'Недоставленные письма: hard/soft bounce, ошибка доставки, жалоба на спам.',
    formula: 'manager_status ∈ {email_broken, soft_bounce, delivery_error, spam}.',
    source: 'Webhook провайдера и ошибки в sent_mail_log.',
  },
  layout_errors: {
    id: 'layout_errors',
    title: 'КП не влезло',
    description: 'Получатели, для которых коммерческое предложение не прошло проверку вёрстки (шрифт/макет).',
    formula: 'layout_error_code = kp_font_compact.',
    source: 'CampaignRecipient.extra и sent_mail_log при ошибке генерации КП.',
  },
  pending: {
    id: 'pending',
    title: 'Ожидают статуса',
    description: 'Отправка принята, но финальный статус доставки от провайдера ещё не получен.',
    formula: 'manager_status ∈ {pending, no_data}.',
    source: 'Для SMTP часто остаётся 100% до появления bounce-handler.',
  },
  pending_rate: {
    id: 'pending_rate',
    title: 'Доля ожидающих',
    description: 'Процент компаний без подтверждённого статуса доставки.',
    formula: 'pending / sent × 100%.',
    source: 'Агрегация manager_stats._aggregate_counts.',
  },
  consents: {
    id: 'consents',
    title: 'Согласия',
    description: 'Подтверждённые согласия на получение коммерческих материалов (сценарий consent).',
    formula: 'consent_status = confirmed в consents.json.',
    source: 'Файл {job}/state/consents.json.',
  },
  materials_sent: {
    id: 'materials_sent',
    title: 'Материалы отправлены',
    description: 'После подтверждения согласия материалы (КП) успешно отправлены получателю.',
    formula: 'materials_status = sent или заполнено materials_sent_at.',
    source: 'consents.json.',
  },
  unsubscribed: {
    id: 'unsubscribed',
    title: 'Отписки',
    description: 'Получатели, отписавшиеся от рассылки через провайдера или цепочку.',
    formula: 'manager_status = unsubscribed.',
    source: 'Webhook + campaign_chain_consent_events + suppression_entries.',
  },
  spam: {
    id: 'spam',
    title: 'Жалобы на спам',
    description: 'Жалобы (complaint) от почтовых провайдеров или получателей.',
    formula: 'manager_status = spam.',
    source: 'Webhook провайдера → suppression_entries.',
  },
  delivery_rate: {
    id: 'delivery_rate',
    title: 'Доставляемость',
    description: 'Доля доставленных от числа отправленных компаний.',
    formula: 'delivered / sent × 100%.',
    source: 'manager_stats build_campaign_analytics.',
  },
  open_rate: {
    id: 'open_rate',
    title: 'Open rate',
    description: 'Доля открытий от доставленных (или от отправленных, если доставленных нет).',
    formula: 'opened / (delivered или sent) × 100%.',
    source: 'manager_stats.',
  },
  ctr: {
    id: 'ctr',
    title: 'CTR',
    description: 'Click-through rate — доля кликов от числа отправленных компаний.',
    formula: 'clicked / sent × 100%.',
    source: 'manager_stats.',
  },
  error_rate: {
    id: 'error_rate',
    title: 'Доля ошибок',
    description: 'Процент компаний с проблемами доставки.',
    formula: 'errors / sent × 100%.',
    source: 'manager_stats.',
  },
  provider: {
    id: 'provider',
    title: 'Провайдер отправки',
    description: 'Транспорт, через который было отправлено письмо (RuSender, MailoPost, UniSender, SMTP).',
    formula: 'Поле transport/provider из sent_mail_log.',
    source: 'sent_mail_log при отправке.',
  },
  message_id: {
    id: 'message_id',
    title: 'ID сообщения провайдера',
    description: 'Идентификатор задачи/письма у провайдера для сопоставления с webhook-событиями.',
    formula: 'provider_message_id или provider_job_id.',
    source: 'sent_mail_log + webhook JSONL.',
  },
  bounce_reason: {
    id: 'bounce_reason',
    title: 'Причина недоставки',
    description: 'Нормализованная причина bounce или ошибки SMTP/провайдера.',
    formula: 'Классификация provider_status + delivery_response.',
    source: 'Вычисляется при чтении sender_report (не хранится отдельно).',
  },
  domain_stats: {
    id: 'domain_stats',
    title: 'Статистика по доменам',
    description: 'Доставляемость и вовлечённость в разрезе почтовых доменов (Mail.ru, Gmail и т.д.).',
    formula: 'Группировка по email_domain_provider.',
    source: 'manager_stats.build_domain_delivery_stats.',
  },
  chain_clicks: {
    id: 'chain_clicks',
    title: 'Клики по веткам цепочки',
    description: 'Переходы по кнопкам в email-цепочке (собственные branch-ссылки).',
    formula: 'COUNT(clicked_at) по edge_id в campaign_chain_tokens.',
    source: 'PostgreSQL campaign_chain_tokens.',
  },
  delivery_attempt: {
    id: 'delivery_attempt',
    title: 'Попытка доставки',
    description: 'Отдельная техническая попытка отправить письмо одному получателю (включая retry).',
    formula: 'Одна строка delivery_attempts на attempt_number.',
    source: 'PostgreSQL delivery_attempts (CampaignFlow worker).',
  },
  sent_mail_log: {
    id: 'sent_mail_log',
    title: 'Журнал отправок',
    description: 'Неизменяемый лог каждой успешной или неуспешной отправки с метаданными.',
    formula: 'Одна запись на факт send-вызова.',
    source: 'PostgreSQL job_events → sent_mail_log.',
  },
  email_render: {
    id: 'email_render',
    title: 'Рендер письма',
    description: 'HTML собран из шаблона и данных получателя так же, как при отправке. Может отличаться от фактического HTML провайдера (tracking pixel, обёртка ссылок).',
    formula: 'template_render_service + substitution context.',
    source: 'Шаблон кампании + CampaignRecipient; exact sent HTML не сохраняется.',
  },
  documents: {
    id: 'documents',
    title: 'Документы рассылки',
    description: 'Сгенерированные КП, договоры и другие файлы в каталоге output job.',
    formula: 'Файлы в {job}/output/**.',
    source: 'Генератор документов + preview API /api/preview/archive.',
  },
  operational_progress: {
    id: 'operational_progress',
    title: 'Прогресс отправки',
    description: 'Операционные счётчики CampaignFlow: сколько получателей обработано worker.',
    formula: 'sent_count / total_count, error_count.',
    source: 'PostgreSQL campaigns + campaign_recipients.',
  },
  live_send: {
    id: 'live_send',
    title: 'Идёт отправка',
    description: 'Текущее состояние очереди батчей, если кампания в статусе running/scheduled/paused.',
    formula: 'remaining, queued_batches, next_batch_at.',
    source: 'PostgreSQL campaign_batches.',
  },
  refresh_in_progress: {
    id: 'refresh_in_progress',
    title: 'Обновление у провайдера',
    description: 'Фоновый опрос API провайдера для добора событий доставки.',
    formula: 'Флаг _trigger_provider_refresh.',
    source: 'provider_status_sync / sender_report.',
  },
  awaiting_provider_events: {
    id: 'awaiting_provider_events',
    title: 'Ждём webhook',
    description: 'Есть отправки, но все компании ещё без статуса — возможно, webhook не настроен.',
    formula: 'sent > 0 и pending >= sent.',
    source: 'build_campaign_analytics.',
  },
};

export function getMetricGlossary(id: string): MetricGlossaryEntry | undefined {
  return METRIC_GLOSSARY[id];
}
