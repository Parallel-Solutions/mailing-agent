import { describe, expect, it } from 'vitest';
import { addChildEmailNode, addChildLinkNode, addChildNode, createEmptyChain, updateNode } from './chainUtils';

describe('chainUtils button labels', () => {
  it('addChildNode sets button_label to child block name', () => {
    const chain = createEmptyChain();
    const rootId = chain.root_node_id;
    const next = addChildNode(chain, rootId);
    const child = next.nodes.find((n) => n.id !== rootId);
    const edge = next.edges.find((e) => e.target_id === child?.id);
    expect(child?.name).toMatch(/^Письмо \d+$/);
    expect(child?.kind).toBe('email');
    expect(edge?.button_label).toBe(child?.name);
  });

  it('addChildLinkNode creates link block with defaults', () => {
    const chain = createEmptyChain();
    const next = addChildLinkNode(chain, chain.root_node_id, 'unsubscribe');
    const child = next.nodes.find((n) => n.id !== chain.root_node_id);
    expect(child?.kind).toBe('link');
    expect(child?.link_kind).toBe('unsubscribe');
    expect(child?.name).toBe('Отписаться');
  });

  it('addChildEmailNode creates email block', () => {
    const chain = createEmptyChain();
    const next = addChildEmailNode(chain, chain.root_node_id);
    const child = next.nodes.find((n) => n.id !== chain.root_node_id);
    expect(child?.kind).toBe('email');
    expect(child?.email_template_id).toBeNull();
  });

  it('updateNode syncs incoming edge label when block is renamed', () => {
    const chain = createEmptyChain();
    const withChild = addChildNode(chain, chain.root_node_id);
    const childId = withChild.nodes.find((n) => n.id !== chain.root_node_id)!.id;
    const renamed = updateNode(withChild, childId, { name: 'Получить КП' });
    const edge = renamed.edges.find((e) => e.target_id === childId);
    expect(renamed.nodes.find((n) => n.id === childId)?.name).toBe('Получить КП');
    expect(edge?.button_label).toBe('Получить КП');
  });
});