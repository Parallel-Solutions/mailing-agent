import { describe, expect, it } from 'vitest';
import {
  ensureHtmlDocument,
  getEmailFormat,
  hasChainButtonPlaceholder,
  highlightReviewIssues,
  highlightPlaceholderIssues,
  htmlToPlainText,
  preserveParagraphIndents,
  sanitizeHtmlFilename,
  substituteChainButtonsPreview,
  substitutePreviewValues,
} from './emailTemplateUtils';
import type { Template } from '@/api/types';

describe('emailTemplateUtils', () => {
  it('detects visual email format from editor_state', () => {
    const visual: Template = {
      id: '1',
      name: 'Visual',
      template_type: 'email',
      status: 'ready',
      version: {
        id: 'v1',
        subject: '',
        body_html: '<p>Hi</p>',
        body_text: '',
        variables: [],
        editor_state: { email_format: 'visual' },
      },
    };
    const simple: Template = {
      ...visual,
      version: { ...visual.version!, editor_state: { email_format: 'simple' } },
    };
    expect(getEmailFormat(visual)).toBe('visual');
    expect(getEmailFormat(simple)).toBe('simple');
    expect(getEmailFormat({ ...visual, version: undefined })).toBe('simple');
  });

  it('converts html to plain text', () => {
    expect(htmlToPlainText('<p>Hello <b>world</b></p>')).toBe('Hello world');
  });

  it('substitutes preview variables', () => {
    expect(substitutePreviewValues('Hi {{contact_name}}', { contact_name: 'Anna' })).toBe('Hi Anna');
  });

  it('detects and previews chain button placeholders', () => {
    const html = '<p>Hi</p><div data-ma-chain-buttons="1" style="text-align:left;padding:8px 0">stub</div>';
    expect(hasChainButtonPlaceholder(html)).toBe(true);
    const preview = substituteChainButtonsPreview(html);
    expect(preview).toContain('Вариант 1');
    expect(preview).toContain('text-align:left');
    expect(preview).toContain('<p style="margin:0">');
    expect(preview).not.toContain('margin:0 0 8px');
    expect(preview).not.toContain('data-ma-chain-buttons');
  });

  it('sanitizes html download filenames', () => {
    expect(sanitizeHtmlFilename('Письмо оферта')).toBe('Письмо-оферта.html');
    expect(sanitizeHtmlFilename('a/b:c?.html')).toBe('abc.html');
    expect(sanitizeHtmlFilename('   ')).toBe('template.html');
  });

  it('highlights review issues with severity styles', () => {
    const html = '<p>на {{ стп }} для</p>';
    const highlighted = highlightReviewIssues(html, [
      { fragment: '{{ стп }}', severity: 'error' },
    ]);
    expect(highlighted).toContain('<mark style=');
    expect(highlighted).toContain('{{ стп }}');
  });

  it('highlights unresolved placeholder tokens in preview html', () => {
    const html = '<p>Работы {{{Вид_работ}}} для {{company}}</p>';
    const highlighted = highlightPlaceholderIssues(html, [
      { token: '{{{Вид_работ}}}' },
      { token: '{{company}}' },
    ]);
    expect(highlighted).toContain('<mark style=');
    expect(highlighted).toContain('{{{Вид_работ}}}');
    expect(highlighted).not.toContain('<p>Работы {{{Вид_работ}}}');
  });

  it('preserves leading spaces as paragraph text-indent', () => {
    expect(preserveParagraphIndents('<p>&nbsp;&nbsp;&nbsp;&nbsp;Абзац</p>')).toBe(
      '<p style="text-indent:1.25em">Абзац</p>',
    );
    expect(preserveParagraphIndents('<p style="text-indent:2em">Уже с отступом</p>')).toBe(
      '<p style="text-indent:2em">Уже с отступом</p>',
    );
  });

  it('wraps html fragments into a document', () => {
    const wrapped = ensureHtmlDocument('<p>Hi {{contact_name}}</p>');
    expect(wrapped).toContain('<!doctype html>');
    expect(wrapped).toContain('<p>Hi {{contact_name}}</p>');
    expect(ensureHtmlDocument('<!DOCTYPE html><html><body>x</body></html>')).toBe(
      '<!DOCTYPE html><html><body>x</body></html>',
    );
  });
});
