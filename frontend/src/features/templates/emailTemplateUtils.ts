import type { EmailEditorState, Template } from '@/api/types';
import { EMAIL_THEME } from './emailTheme';

export function getEmailFormat(template: Template): 'simple' | 'visual' {
  const state = template.version?.editor_state as EmailEditorState | null | undefined;
  return state?.email_format === 'visual' ? 'visual' : 'simple';
}

export const PARAGRAPH_INDENT = '1.25em';

export function preserveParagraphIndents(html: string): string {
  if (!html.trim()) return html;
  if (typeof DOMParser === 'undefined') {
    return html.replace(/<p(\s[^>]*)?>([\s\u00a0]|&nbsp;)+/gi, (match, attrs = '') => {
      const styleMatch = String(attrs).match(/style\s*=\s*["']([^"']*)["']/i);
      const style = styleMatch?.[1] || '';
      if (/text-indent\s*:/i.test(style)) return match;
      const styleAttr = style ? ` style="${style};text-indent:${PARAGRAPH_INDENT}"` : ` style="text-indent:${PARAGRAPH_INDENT}"`;
      return `<p${styleAttr}>`;
    });
  }

  const doc = new DOMParser().parseFromString(`<div id="root">${html}</div>`, 'text/html');
  const root = doc.getElementById('root');
  if (!root) return html;

  root.querySelectorAll('p').forEach((paragraph) => {
    const style = paragraph.getAttribute('style') || '';
    if (/text-indent\s*:/i.test(style)) return;

    const inner = paragraph.innerHTML;
    const leadingMatch = inner.match(/^((?:\s|&nbsp;|\u00a0)+)/i);
    if (!leadingMatch) return;

    const spaces = leadingMatch[1].replace(/&nbsp;|\u00a0/gi, ' ').length;
    if (spaces < 1) return;

    paragraph.innerHTML = inner.slice(leadingMatch[1].length);
    const indentEm = spaces >= 4 ? PARAGRAPH_INDENT : `${Math.max(1, spaces * 0.3)}em`;
    const nextStyle = style ? `${style};text-indent:${indentEm}` : `text-indent:${indentEm}`;
    paragraph.setAttribute('style', nextStyle);
  });

  return root.innerHTML;
}

export function paragraphHasIndent(style: string | null | undefined): boolean {
  return /text-indent\s*:/i.test(String(style || ''));
}

export function toggleParagraphIndentStyle(style: string | null | undefined): string {
  const current = String(style || '').trim();
  if (paragraphHasIndent(current)) {
    return current.replace(/text-indent\s*:\s*[^;]+;?/gi, '').replace(/;\s*;/g, ';').replace(/^;|;$/g, '').trim();
  }
  return current ? `${current};text-indent:${PARAGRAPH_INDENT}` : `text-indent:${PARAGRAPH_INDENT}`;
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

const REVIEW_ERROR_MARK_STYLE =
  'background:#fff1f0;color:#cf1322;border:1px solid #ffa39e;border-radius:3px;padding:0 2px;';
const REVIEW_WARNING_MARK_STYLE =
  'background:#fffbe6;color:#ad6800;border:1px solid #ffe58f;border-radius:3px;padding:0 2px;';

export type ReviewHighlightIssue = {
  token?: string;
  fragment?: string;
  severity?: 'error' | 'warning' | 'info';
};

export function highlightReviewIssues(html: string, issues: ReviewHighlightIssue[]): string {
  if (!html || issues.length === 0) {
    return html;
  }
  const fragments = [...new Set(
    issues
      .map((issue) => issue.fragment || issue.token)
      .filter((value): value is string => Boolean(value)),
  )].sort((left, right) => right.length - left.length);
  let result = html;
  for (const fragment of fragments) {
    const issue = issues.find((item) => item.fragment === fragment || item.token === fragment);
    const style = issue?.severity === 'warning' || issue?.severity === 'info'
      ? REVIEW_WARNING_MARK_STYLE
      : REVIEW_ERROR_MARK_STYLE;
    result = result.split(fragment).join(`<mark style="${style}">${fragment}</mark>`);
  }
  return result;
}

export type PlaceholderHighlightIssue = {
  token: string;
};

export function highlightPlaceholderIssues(html: string, issues: PlaceholderHighlightIssue[]): string {
  return highlightReviewIssues(
    html,
    issues.map((issue) => ({ token: issue.token, fragment: issue.token, severity: 'error' })),
  );
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
  const actionButtons = CHAIN_BUTTONS_PREVIEW_LABELS.map(
    (label) =>
      `<span style="display:inline-block;margin:0 4px;padding:8px 16px;background:#236348;color:#fff;border-radius:4px">${label}</span>`,
  ).join('');
  const unsubscribe = '<span style="display:inline-block;color:#868e96;text-decoration:underline">Отписаться</span>';
  return (
    `<div style="${wrapperStyle}"><p style="margin:0">${actionButtons}</p></div>`
    + `<div style="text-align:right;padding:12px 0 0"><p style="margin:0">${unsubscribe}</p></div>`
  );
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

export function sanitizeHtmlFilename(name: string): string {
  const slug = String(name || '')
    .trim()
    .replace(/\.html?$/i, '')
    .replace(/[<>:"/\\|?*\u0000-\u001f]+/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 120);
  return `${slug || 'template'}.html`;
}

export function ensureHtmlDocument(html: string): string {
  const trimmed = String(html || '').trim();
  if (!trimmed) {
    return '<!doctype html>\n<html><head><meta charset="utf-8"></head><body></body></html>';
  }
  if (/<!doctype\s+html|<html[\s>]/i.test(trimmed)) {
    return trimmed;
  }
  return (
    '<!doctype html>\n<html><head><meta charset="utf-8"></head><body>\n'
    + trimmed
    + '\n</body></html>'
  );
}

export function downloadEmailHtml(name: string, html: string): void {
  const content = ensureHtmlDocument(html);
  const blob = new Blob([content], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = sanitizeHtmlFilename(name);
  a.click();
  URL.revokeObjectURL(url);
}
