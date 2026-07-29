import { describe, expect, it } from 'vitest';

import {
  bulkParagraphIndentActive,
  paragraphIndentActive,
  resolveBulkParagraphIndents,
  toggleParagraphIndent,
} from './emailEditorIndent';
import { PARAGRAPH_INDENT } from './emailTemplateUtils';

describe('emailEditorIndent', () => {
  it('detects paragraph indent from textIndent or legacy style', () => {
    expect(paragraphIndentActive({ textIndent: '1.25em' })).toBe(true);
    expect(paragraphIndentActive({ style: 'text-indent:1.25em' })).toBe(true);
    expect(paragraphIndentActive({})).toBe(false);
  });

  it('toggles paragraph indent value', () => {
    expect(toggleParagraphIndent({})).toBe(PARAGRAPH_INDENT);
    expect(toggleParagraphIndent({ textIndent: PARAGRAPH_INDENT })).toBeNull();
  });

  it('applies bulk indent to all paragraphs except the first', () => {
    expect(resolveBulkParagraphIndents([false, false, false], { skipFirst: true })).toEqual([
      null,
      PARAGRAPH_INDENT,
      PARAGRAPH_INDENT,
    ]);
  });

  it('removes bulk indent from non-first paragraphs on repeat', () => {
    expect(resolveBulkParagraphIndents([false, true, true], { skipFirst: true })).toEqual([
      null,
      null,
      null,
    ]);
  });

  it('keeps greeting without indent when bulk mode is active', () => {
    expect(resolveBulkParagraphIndents([true, true, true], { skipFirst: true })).toEqual([
      null,
      null,
      null,
    ]);
  });

  it('reports bulk indent active only for non-first paragraphs', () => {
    expect(bulkParagraphIndentActive([false, true, true], { skipFirst: true })).toBe(true);
    expect(bulkParagraphIndentActive([false, true, false], { skipFirst: true })).toBe(false);
    expect(bulkParagraphIndentActive([true], { skipFirst: true })).toBe(false);
  });
});
