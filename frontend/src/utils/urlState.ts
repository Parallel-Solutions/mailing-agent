/**
 * URL query-param conventions (per page):
 *
 * Statistics: tab, from, to, campaign, provider(s), org, problems_only, quick_filter, q,
 *   rp/cp/pp, modal, modal_id, export_type, drill_kind, action_type
 * Campaign wizard: id, step, modal, fix_step, preview_node
 * Campaign detail: tab
 * Templates: tab, wizard, preview, wizard_step
 * Profile: tab
 * Audiences: audience
 * Connections: add, smtp_stage
 * Companies: edit, work_types
 * Chain builder: node, fullscreen
 * Template editor: preview
 */

export type UrlParamPatch = Record<string, string | null | undefined>;

export function buildSearchParams(
  current: URLSearchParams,
  patch: UrlParamPatch,
  remove: string[] = [],
): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const key of remove) {
    next.delete(key);
  }
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === undefined || value === '') {
      next.delete(key);
    } else {
      next.set(key, value);
    }
  }
  return next;
}

export function readEnumParam<T extends string>(
  params: URLSearchParams,
  key: string,
  allowed: readonly T[],
  defaultValue: T,
): T {
  const raw = params.get(key);
  if (raw && (allowed as readonly string[]).includes(raw)) {
    return raw as T;
  }
  return defaultValue;
}

export function readIntParam(
  params: URLSearchParams,
  key: string,
  defaultValue: number,
  min = 0,
  max = Number.MAX_SAFE_INTEGER,
): number {
  const raw = params.get(key);
  if (raw === null) return defaultValue;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return defaultValue;
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

export function readBoolParam(params: URLSearchParams, key: string): boolean {
  const raw = params.get(key);
  return raw === '1' || raw === 'true';
}

export function sameSearchParams(a: URLSearchParams, b: URLSearchParams): boolean {
  return a.toString() === b.toString();
}

export function searchParamsToString(params: URLSearchParams): string {
  const value = params.toString();
  return value ? `?${value}` : '';
}

/** Modal-related keys cleared when closing any modal overlay. */
export const MODAL_PARAM_KEYS = [
  'modal',
  'modal_id',
  'export_type',
  'drill_kind',
  'action_type',
  'fix_step',
  'preview_node',
  'wizard',
  'wizard_step',
  'preview',
  'audience',
  'add',
  'smtp_stage',
  'edit',
  'work_types',
] as const;

export function clearModalParams(current: URLSearchParams, extraRemove: string[] = []): URLSearchParams {
  return buildSearchParams(current, {}, [...MODAL_PARAM_KEYS, ...extraRemove]);
}
