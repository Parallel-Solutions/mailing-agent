const ACTIVE_VALIDATION_STATUSES = new Set(['queued', 'running']);

export function emailValidationRefetchInterval(status: unknown): number | false {
  const normalized = String(status || '').trim().toLowerCase();
  return ACTIVE_VALIDATION_STATUSES.has(normalized) ? 3000 : false;
}
