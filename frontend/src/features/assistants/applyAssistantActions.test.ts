import { describe, expect, it, vi } from 'vitest';
import { applyAssistantActions } from './applyAssistantActions';

describe('applyAssistantActions', () => {
  it('applies html and subject mutations', async () => {
    const setHtml = vi.fn();
    const setSubject = vi.fn();
    const markDirty = vi.fn();
    const applied = await applyAssistantActions(
      [
        { type: 'set_subject', subject: 'Новая тема' },
        { type: 'set_html', html: '<p>Hi {{contact_name}}</p>' },
      ],
      { setHtml, setSubject, markDirty },
    );
    expect(applied).toBe(2);
    expect(setSubject).toHaveBeenCalledWith('Новая тема');
    expect(setHtml).toHaveBeenCalledWith('<p>Hi {{contact_name}}</p>');
    expect(markDirty).toHaveBeenCalled();
  });

  it('applies chain_set and reload_template', async () => {
    const setChain = vi.fn();
    const reloadTemplate = vi.fn(async () => undefined);
    const chain = {
      version: 2,
      root_node_id: 'root',
      nodes: [{ id: 'root', name: 'Письмо 1', kind: 'email' as const }],
      edges: [],
    };
    const applied = await applyAssistantActions(
      [
        { type: 'chain_set', chain, selected_node_id: 'root' },
        { type: 'reload_template', reason: 'docx_replaced' },
      ],
      { setChain, reloadTemplate },
    );
    expect(applied).toBe(2);
    expect(setChain).toHaveBeenCalledWith(chain, 'root');
    expect(reloadTemplate).toHaveBeenCalled();
  });
});
