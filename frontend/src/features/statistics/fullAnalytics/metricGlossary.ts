export type MetricGlossaryEntry = {
  id: string;
  title: string;
  description: string;
  formula: string;
  source: string;
};

export const METRIC_GLOSSARY: Record<string, MetricGlossaryEntry> = {
  all_attempts: {
    id: 'all_attempts',
    title: 'Всего',
    description:
      'Все зарегистрированные попытки отправить выбранное письмо. Если одному получателю выполнялась повторная попытка, она учитывается отдельно.',
    formula: 'Количество всех попыток отправки, включая неудачные и повторные.',
    source: 'Журнал попыток доставки; для старых рассылок — журнал отправок.',
  },
  sent: {
    id: 'sent',
    title: 'Отправлено в почтовый провайдер',
    description:
      'Получатели, для которых почтовый провайдер принял письмо в обработку. Это ещё не подтверждает доставку в почтовый ящик.',
    formula: 'Учитывается один получатель, если провайдер принял хотя бы одну отправку.',
    source: 'Журнал фактических отправок.',
  },
  not_sent: {
    id: 'not_sent',
    title: 'Не дошло до отправки',
    description:
      'Попытки, по которым письмо не было принято почтовым провайдером: например, адрес заблокирован стоп-листом, некорректен или отправка завершилась до обращения к провайдеру.',
    formula: '«Всего» минус «Отправлено в почтовый провайдер», но не меньше нуля.',
    source: 'Журнал попыток доставки и журнал фактических отправок.',
  },
  attempts: {
    id: 'attempts',
    title: 'Попытки отправки',
    description:
      'Все технические попытки отправить письмо, включая неудачи и повторы одному получателю.',
    formula: 'Количество строк delivery_attempts.',
    source: 'PostgreSQL delivery_attempts.',
  },
  delivered: {
    id: 'delivered',
    title: 'Доставлено реальное письмо',
    description:
      'Получатели, для которых провайдер подтвердил доставку письма. Открытие или переход по ссылке также означает, что письмо было доставлено.',
    formula: 'Количество доставленных ÷ количество принятых провайдером × 100%.',
    source: 'События доставки, открытия и перехода от почтового провайдера.',
  },
  opened: {
    id: 'opened',
    title: 'Открыто',
    description:
      'Получатели, для которых провайдер зафиксировал открытие письма. Переход по ссылке также считается открытием.',
    formula: 'Количество открывших ÷ количество доставленных × 100%. Если данных о доставке нет — от числа принятых провайдером.',
    source: 'События открытия и перехода от почтового провайдера.',
  },
  clicked: {
    id: 'clicked',
    title: 'Кликнули по ссылке',
    description: 'Компании, по которым был клик по ссылке в письме (провайдерский click-tracking).',
    formula: 'manager_status = clicked.',
    source: 'Webhook провайдера; собственный click-proxy не используется.',
  },
  errors: {
    id: 'errors',
    title: 'Ошибки почтового провайдера',
    description:
      'Письма, которые провайдер принял, но не смог доставить: адрес не существует, ящик переполнен, сервер получателя отклонил письмо или произошла другая ошибка доставки.',
    formula: 'Учитываются постоянные, временные и прочие ошибки доставки. Отписки и жалобы на спам сюда не входят.',
    source: 'Ответы и события ошибок от почтового провайдера.',
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
    description:
      'Отправка принята, но финальный статус доставки от провайдера ещё не получен. Для SMTP без DSN успешная отправка считается доставленной; открытия/клики без tracking недоступны.',
    formula: 'manager_status ∈ {pending, no_data}.',
    source: 'Для SMTP: log status→delivered; для API-провайдеров — webhook/JSONL.',
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
    description:
      'Подтверждённые согласия: legacy consent flow и подписки через кнопки email-цепочки.',
    formula: 'consent_status = confirmed (consents.json) + chain subscribe events.',
    source: '{job}/state/consents.json и campaign_chain_consent_events.',
  },
  materials_sent: {
    id: 'materials_sent',
    title: 'Материалы отправлены',
    description: 'После подтверждения согласия материалы (КП) успешно отправлены получателю.',
    formula: 'materials_status = sent или заполнено materials_sent_at.',
    source: 'consents.json (legacy consent flow).',
  },
  unsubscribed: {
    id: 'unsubscribed',
    title: 'Отписались у почтового провайдера',
    description:
      'Получатели, для которых почтовый провайдер сообщил об отписке. Например, RuSender присылает событие external_mail.unsubscribe. После получения события email добавляется в глобальный стоп-лист, поэтому следующие письма через нашу систему ему не отправляются.',
    formula: 'Учитываются получатели с итоговым статусом отписки у провайдера.',
    source: 'Событие отписки от почтового провайдера.',
  },
  spam: {
    id: 'spam',
    title: 'Добавили в спам',
    description:
      'Получатели, которые пожаловались на письмо или пометили его как спам. Это отдельное действие, не отписка. Email добавляется в глобальный стоп-лист, и следующие письма через нашу систему ему не отправляются.',
    formula: 'Учитываются получатели с итоговым статусом жалобы на спам.',
    source: 'Событие complaint от почтового провайдера.',
  },
  tracked_link: {
    id: 'tracked_link',
    title: 'Переходы по ссылке',
    description:
      'Уникальные получатели, которые перешли именно по этой ссылке в выбранном письме.',
    formula: 'Количество перешедших ÷ количество принятых провайдером писем × 100%. Повторные переходы одного получателя не увеличивают показатель.',
    source: 'Персональные отслеживаемые ссылки нашей системы.',
  },
  tracked_document: {
    id: 'tracked_document',
    title: 'Открытия документа',
    description:
      'Уникальные получатели, которые открыли этот документ по персональной ссылке из выбранного письма.',
    formula: 'Количество открывших ÷ количество принятых провайдером писем × 100%. Повторные открытия одного получателя не увеличивают показатель.',
    source: 'Персональные ссылки открытия документов нашей системы.',
  },
  chain_unsubscribe: {
    id: 'chain_unsubscribe',
    title: 'Отписались по ссылке в письме',
    description:
      'Уникальные получатели, которые нажали нашу кнопку отписки в выбранном письме. Отписка фиксируется сразу, а email добавляется в глобальный стоп-лист. Это отдельный показатель от отписки, о которой сообщил почтовый провайдер.',
    formula: 'Количество нажавших ÷ количество принятых провайдером писем × 100%. Повторный переход одного получателя не увеличивает показатель.',
    source: 'Персональная кнопка отписки нашей системы.',
  },
  chain_subscribe: {
    id: 'chain_subscribe',
    title: 'Подписались по ссылке в письме',
    description:
      'Уникальные получатели, которые нажали кнопку подписки в выбранном письме. Система сохраняет согласие на рассылку на один год. Если email ранее был в стоп-листе, одно нажатие не снимает блокировку автоматически.',
    formula: 'Количество нажавших ÷ количество принятых провайдером писем × 100%. Повторный переход одного получателя не увеличивает показатель.',
    source: 'Персональная кнопка подписки нашей системы.',
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
    formula: 'processed_count / total_count; успешность = success_count / total_count.',
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
