import type { Editor } from '@tiptap/react';

import { PARAGRAPH_INDENT, preserveParagraphIndents } from './emailTemplateUtils';

export function paragraphIndentActive(attrs: Record<string, unknown>): boolean {
  return Boolean(attrs.textIndent) || /text-indent\s*:/i.test(String(attrs.style || ''));
}

export function toggleParagraphIndent(attrs: Record<string, unknown>): string | null {
  return paragraphIndentActive(attrs) ? null : PARAGRAPH_INDENT;
}

export function resolveBulkParagraphIndents(
  indents: boolean[],
  options?: { skipFirst?: boolean },
): Array<string | null> {
  const skipFirst = options?.skipFirst ?? true;
  const targets = skipFirst ? indents.slice(1) : indents;
  const allHaveIndent = targets.length > 0 && targets.every(Boolean);

  return indents.map((_, index) => {
    if (skipFirst && index === 0) {
      return null;
    }
    return allHaveIndent ? null : PARAGRAPH_INDENT;
  });
}

export function bulkParagraphIndentActive(indents: boolean[], options?: { skipFirst?: boolean }): boolean {
  const skipFirst = options?.skipFirst ?? true;
  const targets = skipFirst ? indents.slice(1) : indents;
  return targets.length > 0 && targets.every(Boolean);
}

function findParagraphDepth(state: Editor['state']): { depth: number; pos: number } | null {
  const { $from } = state.selection;
  for (let depth = $from.depth; depth > 0; depth -= 1) {
    if ($from.node(depth).type.name === 'paragraph') {
      return { depth, pos: $from.before(depth) };
    }
  }
  return null;
}

export function toggleCurrentParagraphIndent(editor: Editor): boolean {
  return editor
    .chain()
    .focus()
    .command(({ tr, state, dispatch }) => {
      const paragraph = findParagraphDepth(state);
      if (!paragraph) return false;

      const node = state.doc.nodeAt(paragraph.pos);
      if (!node || node.type.name !== 'paragraph') return false;

      const nextIndent = toggleParagraphIndent(node.attrs);
      if (dispatch) {
        tr.setNodeMarkup(paragraph.pos, undefined, { ...node.attrs, textIndent: nextIndent });
        dispatch(tr);
      }
      return true;
    })
    .run();
}

export function applyParagraphIndentAtStart(editor: Editor): boolean {
  return editor
    .chain()
    .focus()
    .command(({ tr, state, dispatch }) => {
      const paragraph = findParagraphDepth(state);
      if (!paragraph) return false;

      const node = state.doc.nodeAt(paragraph.pos);
      if (!node || node.type.name !== 'paragraph') return false;
      if (paragraphIndentActive(node.attrs)) return false;

      if (dispatch) {
        tr.setNodeMarkup(paragraph.pos, undefined, { ...node.attrs, textIndent: PARAGRAPH_INDENT });
        dispatch(tr);
      }
      return true;
    })
    .run();
}

export function applyParagraphIndentToAll(editor: Editor, options?: { skipFirst?: boolean }): boolean {
  const skipFirst = options?.skipFirst ?? true;

  return editor
    .chain()
    .focus()
    .command(({ tr, state, dispatch }) => {
      const paragraphs: Array<{ pos: number; attrs: Record<string, unknown> }> = [];
      state.doc.forEach((node, pos) => {
        if (node.type.name === 'paragraph') {
          paragraphs.push({ pos, attrs: node.attrs });
        }
      });
      if (!paragraphs.length) return false;

      const indents = paragraphs.map(({ attrs }) => paragraphIndentActive(attrs));
      const nextIndents = resolveBulkParagraphIndents(indents, { skipFirst });

      if (dispatch) {
        let transaction = tr;
        paragraphs.forEach(({ pos, attrs }, index) => {
          transaction = transaction.setNodeMarkup(pos, undefined, {
            ...attrs,
            textIndent: nextIndents[index],
          });
        });
        dispatch(transaction);
      }
      return true;
    })
    .run();
}

export function normalizeEditorParagraphIndents(editor: Editor): void {
  const { state } = editor;
  const updates: Array<{ pos: number; deleteFrom: number; deleteTo: number; attrs: Record<string, unknown> }> = [];

  state.doc.descendants((node, pos) => {
    if (node.type.name !== 'paragraph') return;
    const leadingMatch = node.textContent.match(/^[\s\u00a0]+/);
    if (!leadingMatch) return;

    const attrs = { ...node.attrs };
    if (!attrs.textIndent) {
      attrs.textIndent = PARAGRAPH_INDENT;
    }
    updates.push({
      pos,
      deleteFrom: pos + 1,
      deleteTo: pos + 1 + leadingMatch[0].length,
      attrs,
    });
  });

  if (!updates.length) return;

  let tr = state.tr;
  updates
    .sort((left, right) => right.pos - left.pos)
    .forEach(({ pos, deleteFrom, deleteTo, attrs }) => {
      tr = tr.delete(deleteFrom, deleteTo);
      tr = tr.setNodeMarkup(pos, undefined, attrs);
    });
  editor.view.dispatch(tr);
}

const indentNormalizationTimers = new WeakMap<Editor, ReturnType<typeof setTimeout>>();

export function scheduleParagraphIndentNormalization(editor: Editor, delayMs = 120): void {
  const existing = indentNormalizationTimers.get(editor);
  if (existing) clearTimeout(existing);
  indentNormalizationTimers.set(
    editor,
    setTimeout(() => {
      indentNormalizationTimers.delete(editor);
      if (editor.isDestroyed) return;
      normalizeEditorParagraphIndents(editor);
    }, delayMs),
  );
}

export function cancelParagraphIndentNormalization(editor: Editor): void {
  const existing = indentNormalizationTimers.get(editor);
  if (existing) {
    clearTimeout(existing);
    indentNormalizationTimers.delete(editor);
  }
}

export function handleParagraphIndentKeydown(editor: Editor, event: KeyboardEvent): boolean {
  if (event.key === 'Tab') {
    if (event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return false;
    const { $from } = editor.state.selection;
    if ($from.parent.type.name !== 'paragraph') return false;
    event.preventDefault();
    return toggleCurrentParagraphIndent(editor);
  }

  if (event.key === ' ') {
    const { $from } = editor.state.selection;
    if ($from.parent.type.name !== 'paragraph') return false;
    if ($from.parentOffset !== 0) return false;
    if (paragraphIndentActive($from.parent.attrs)) return false;
    event.preventDefault();
    return applyParagraphIndentAtStart(editor);
  }

  return false;
}

export function buildEmailEditorHtml(editor: Editor): string {
  normalizeEditorParagraphIndents(editor);
  return preserveParagraphIndents(editor.getHTML());
}

export function collectParagraphIndentStates(editor: Editor): boolean[] {
  const indents: boolean[] = [];
  editor.state.doc.forEach((node) => {
    if (node.type.name === 'paragraph') {
      indents.push(paragraphIndentActive(node.attrs));
    }
  });
  return indents;
}
