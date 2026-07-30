export function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return value.toLocaleString('ru-RU');
  return String(value);
}

/** Format KPI counts: missing → em dash; real zero stays "0". */
export function fmtMetric(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'number') return value.toLocaleString('ru-RU');
  const text = String(value).trim();
  if (!text) return '—';
  return text;
}

export function companyField(item: Record<string, unknown>, field: string): string {
  const company = item.company as { fields?: Record<string, { display?: string }> } | undefined;
  return company?.fields?.[field]?.display ?? '—';
}

export function companyEmailsText(item: Record<string, unknown>): string {
  const emails = (item.emails as Array<{ email?: string }> | undefined) || [];
  const list = emails.map((entry) => entry.email).filter(Boolean) as string[];
  if (list.length) return list.join(', ');
  return String(item.email || '—');
}

export function statusLabel(status: unknown): string {
  if (!status || typeof status !== 'object') return '—';
  const label = (status as { label?: string }).label;
  return label || '—';
}

export function downloadCsv(filename: string, headers: string[], rows: string[][]) {
  const escape = (cell: string) => `"${String(cell ?? '').replace(/"/g, '""')}"`;
  const lines = [headers.map(escape).join(';'), ...rows.map((row) => row.map(escape).join(';'))];
  const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}
