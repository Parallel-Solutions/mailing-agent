import { describe, expect, it } from 'vitest';
import {
  addChildNode,
  computeChainLayout,
  createEmptyChain,
} from './chainUtils';

describe('computeChainLayout', () => {
  it('lays out branching chain left-to-right', () => {
    let chain = createEmptyChain();
    const root = chain.root_node_id;
    chain = addChildNode(chain, root);
    chain = addChildNode(chain, root);
    chain = addChildNode(chain, root);

    const layout = computeChainLayout(chain);
    expect(Object.keys(layout.nodes).length).toBe(4);
    expect(layout.width).toBeGreaterThanOrEqual(400);
    expect(layout.height).toBeGreaterThan(200);

    const rootPos = layout.nodes[root];
    const children = chain.edges.map((e) => layout.nodes[e.target_id]);
    for (const child of children) {
      expect(child.x).toBeGreaterThan(rootPos.x);
    }
  });
});
