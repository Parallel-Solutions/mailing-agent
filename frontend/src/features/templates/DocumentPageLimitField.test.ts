import { describe, expect, it } from 'vitest';
import type { Template } from '@/api/types';
import {
  canConfigureDocumentPageLimit,
  documentPageMode,
} from './DocumentPageLimitField';

const template = (overrides: Partial<Template> = {}): Template => ({
  id: 'template-1',
  name: 'КП',
  status: 'ready',
  template_type: 'document',
  is_template: true,
  attachment_output_format: 'pdf',
  version: {
    id: 'version-1',
    subject: '',
    body_html: '',
    body_text: '',
    variables: [],
    filename: 'offer.docx',
  },
  ...overrides,
});

describe('document page limit', () => {
  it('is configurable for personalized DOCX-to-PDF templates', () => {
    expect(canConfigureDocumentPageLimit(template())).toBe(true);
    expect(canConfigureDocumentPageLimit(template({ is_template: false }))).toBe(false);
    expect(canConfigureDocumentPageLimit(template({ attachment_output_format: 'original' }))).toBe(false);
  });

  it('keeps one page as the backward-compatible default', () => {
    expect(documentPageMode(template())).toBe('one_page');
    expect(documentPageMode(template({ enforce_one_page: false }))).toBe('multi_page');
  });
});
