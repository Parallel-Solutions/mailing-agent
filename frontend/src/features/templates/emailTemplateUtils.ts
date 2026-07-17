import type { EmailEditorState, Template } from '@/api/types';
import { EMAIL_THEME } from './emailTheme';

export function getEmailFormat(template: Template): 'simple' | 'visual' {
  const state = template.version?.editor_state as EmailEditorState | null | undefined;
  return state?.email_format === 'visual' ? 'visual' : 'simple';
}

export function htmlToPlainText(html: string): string {
  if (!html.trim()) return '';
  if (typeof DOMParser !== 'undefined') {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return (doc.body.textContent || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
  return html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

export function substitutePreviewValues(html: string, values: Record<string, string>): string {
  let result = html;
  Object.entries(values).forEach(([key, value]) => {
    result = result.replaceAll(`{{${key}}}`, value);
  });
  return result;
}

export const CHAIN_BUTTONS_MARKER = 'data-ma-chain-buttons="1"';

const CHAIN_BUTTONS_PLACEHOLDER_RE =
  /<(?:div|td)\b[^>]*\bdata-ma-chain-buttons\s*=\s*["']1["'][^>]*>[\s\S]*?<\/(?:div|td)>/gi;

const CHAIN_BUTTONS_PREVIEW_LABELS = ['Вариант 1', 'Вариант 2'] as const;

function extractChainButtonsWrapperStyle(attrs: string): string {
  const styleMatch = attrs.match(/style\s*=\s*["']([^"']*)["']/i);
  if (!styleMatch) return 'text-align:center;padding:8px 0';
  const preserved = styleMatch[1]
    .split(';')
    .map((part) => part.trim())
    .filter((part) => part.startsWith('text-align:') || part.startsWith('padding'));
  return preserved.length ? preserved.join(';') : 'text-align:center;padding:8px 0';
}

function buildChainButtonsPreviewBlock(wrapperStyle: string): string {
  const buttons = CHAIN_BUTTONS_PREVIEW_LABELS.map(
    (label) =>
      `<p style="margin:0 0 8px"><span style="display:inline-block;padding:8px 16px;background:#d9d9d9;color:#595959;border-radius:4px">${label}</span></p>`,
  ).join('');
  return `<div style="${wrapperStyle}">${buttons}</div>`;
}

export function hasChainButtonPlaceholder(html: string): boolean {
  return CHAIN_BUTTONS_PLACEHOLDER_RE.test(html || '');
}

export function substituteChainButtonsPreview(html: string): string {
  return (html || '').replace(CHAIN_BUTTONS_PLACEHOLDER_RE, (match) => {
    const attrsMatch = match.match(/^<(?:div|td)\b([^>]*)>/i);
    const wrapperStyle = extractChainButtonsWrapperStyle(attrsMatch?.[1] || '');
    return buildChainButtonsPreviewBlock(wrapperStyle);
  });
}

export function buildEmailPreviewDocument(html: string): string {
  const t = EMAIL_THEME;
  return (
    '<!doctype html><html><head><meta charset="utf-8">'
    + '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
    + `<body style="margin:0;padding:32px 16px;background:${t.bg}">`
    + `<div style="max-width:${t.maxWidth};margin:0 auto;background:${t.bgCard};`
    + 'border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.08);overflow:hidden">'
    + html
    + '</div></body></html>'
  );
}
