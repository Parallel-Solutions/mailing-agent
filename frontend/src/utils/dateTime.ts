const LEGACY_MOSCOW_DATE_TIME =
  /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?$/;

export function parseApiDateTime(value?: string | null): Date | null {
  const source = String(value || '').trim();
  if (!source) return null;

  // Старые отчёты возвращают московское время без смещения.
  const legacyMatch = source.match(LEGACY_MOSCOW_DATE_TIME);
  const normalized = legacyMatch
    ? `${legacyMatch[1]}T${legacyMatch[2]}+03:00`
    : source;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatLocalDateTime(
  value?: string | null,
  options: Intl.DateTimeFormatOptions = {},
): string {
  if (!value) return '—';
  const date = parseApiDateTime(value);
  if (!date) return '—';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...options,
  }).format(date);
}

export function userTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}
