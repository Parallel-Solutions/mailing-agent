export const LITERAL_PREFIX = '=';

export function isLiteralStoredValue(value: string): boolean {
  return String(value || '').startsWith(LITERAL_PREFIX);
}

export function toDisplayValue(stored: string): string {
  const raw = String(stored || '');
  if (isLiteralStoredValue(raw)) {
    return raw.slice(LITERAL_PREFIX.length);
  }
  return raw;
}

export function isColumnValue(value: string, columns: string[]): boolean {
  const normalized = String(value || '').trim().toLowerCase();
  if (!normalized) {
    return false;
  }
  const allowed = new Set(columns.map((column) => column.toLowerCase()));
  return allowed.has(normalized);
}

export function toStorageValue(input: string, columns: string[]): string {
  const trimmed = String(input || '').trim();
  if (!trimmed) {
    return '';
  }
  if (isLiteralStoredValue(trimmed)) {
    return trimmed;
  }
  if (isColumnValue(trimmed, columns)) {
    return trimmed.toLowerCase();
  }
  return `${LITERAL_PREFIX}${trimmed}`;
}

export function mappingToDisplayValues(
  mapping: Record<string, string>,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(mapping).map(([name, value]) => [name, toDisplayValue(value)]),
  );
}

export function mappingToStorageValues(
  mapping: Record<string, string>,
  columns: string[],
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(mapping).map(([name, value]) => [name, toStorageValue(value, columns)]),
  );
}
