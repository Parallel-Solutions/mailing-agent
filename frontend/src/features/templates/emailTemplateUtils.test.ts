import { describe, expect, it } from 'vitest';
import {
  getEmailFormat,
  hasChainButtonPlaceholder,
  htmlToPlainText,
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
    expect(preview).not.toContain('data-ma-chain-buttons');
  });
});
