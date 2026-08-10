const STATUS_LABELS: Record<string, string> = {
  active: 'Активно',
  auth_failed: 'Ошибка авторизации',
  cancelled: 'Отменено',
  clicked: 'Перешли по ссылке',
  completed: 'Завершено',
  completed_with_errors: 'Завершено с ошибками',
  delivered: 'Доставлено',
  disabled: 'Отключено',
  disabled_by_guard: 'Приостановлено из-за ошибок',
  draft: 'Черновик',
  empty: 'Адрес не указан',
  failed: 'Ошибка',
  in_chain: 'В цепочке писем',
  invalid: 'Некорректный адрес',
  opened: 'Открыто',
  paused: 'Приостановлено',
  pending: 'Ожидает',
  queued: 'В очереди',
  retry: 'Повторная попытка',
  running: 'Выполняется',
  scheduled: 'Запланировано',
  sending: 'Отправляется',
  sent: 'Принято провайдером',
  skipped: 'Пропущено',
  throttled: 'Скорость ограничена из-за ошибок',
  unknown: 'Не подтверждён (допущен)',
  stale: 'Требуется повторная проверка',
  valid: 'Адрес корректен',
};

const SCENARIO_LABELS: Record<string, string> = {
  email_chain: 'Цепочка писем',
  consent_then_materials: 'Согласие, затем материалы',
  direct: 'Обычная отправка',
  materials: 'Материалы',
};

export function statusLabel(value?: string | null): string {
  const key = String(value || '').trim().toLowerCase();
  return STATUS_LABELS[key] || 'Неизвестный статус';
}

export function scenarioLabel(value?: string | null): string {
  const key = String(value || '').trim().toLowerCase();
  return SCENARIO_LABELS[key] || 'Не указан';
}

export function errorLabel(value?: string | null): string {
  const source = String(value || '').trim();
  if (!source) return '—';
  if (/[А-Яа-яЁё]/.test(source)) return source;

  const normalized = source.toLowerCase();
  if (normalized.includes('auth') || normalized.includes('credential')) {
    return 'Ошибка авторизации в почтовом сервисе';
  }
  if (normalized.includes('timeout') || normalized.includes('timed out')) {
    return 'Почтовый сервис не ответил вовремя';
  }
  if (normalized.includes('rate limit') || normalized.includes('too many')) {
    return 'Почтовый сервис временно ограничил частоту отправки';
  }
  if (normalized.includes('invalid') && normalized.includes('email')) {
    return 'Некорректный адрес электронной почты';
  }
  if (normalized.includes('connection') || normalized.includes('network')) {
    return 'Не удалось подключиться к почтовому сервису';
  }
  if (normalized.includes('suppressed') || normalized.includes('unsubscribe')) {
    return 'Получатель исключён из рассылки';
  }
  return 'Техническая ошибка отправки';
}

export function providerLabel(value?: string | null): string {
  const key = String(value || '').trim().toLowerCase();
  const labels: Record<string, string> = {
    smtp: 'Почтовый ящик',
    rusender: 'RuSender',
    mailopost: 'MailoPost',
    unisender: 'UniSender',
    unisender_go: 'UniSender Go',
  };
  return labels[key] || 'Почтовый сервис';
}
