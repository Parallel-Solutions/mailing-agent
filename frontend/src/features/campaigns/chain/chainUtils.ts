import type { ChainLinkKind, EmailChain, EmailChainEdge, EmailChainNode } from '@/api/types';

export type LayoutNode = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type LayoutEdge = {
  id: string;
  sourceId: string;
  targetId: string;
  path: string;
};

export type ChainLayout = {
  nodes: Record<string, LayoutNode>;
  edges: LayoutEdge[];
  width: number;
  height: number;
};

const NODE_WIDTH = 120;
const NODE_HEIGHT = 100;
const H_GAP = 80;
const V_GAP = 40;
const PADDING = 40;

/**
 * `crypto.randomUUID()` only exists in secure contexts (HTTPS, or HTTP on
 * localhost) — it is `undefined` on a plain-HTTP origin such as an internal
 * deployment without TLS termination, which crashed the whole chain builder
 * (`crypto.randomUUID is not a function`) before this fix. These ids are
 * local, ephemeral node/edge identifiers (never persisted as real UUIDs —
 * every call site below immediately truncates to 8 chars), so a
 * `Math.random`-based fallback is safe and preserves the existing id shape.
 */
function randomIdSuffix(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID().slice(0, 8);
  }
  return Math.random().toString(16).slice(2, 10).padEnd(8, '0');
}

export function nodeKind(node: EmailChainNode): 'email' | 'link' {
  return node.kind === 'link' ? 'link' : 'email';
}

export function isLinkNode(node: EmailChainNode): boolean {
  return nodeKind(node) === 'link';
}

export function isEmailNode(node: EmailChainNode): boolean {
  return nodeKind(node) === 'email';
}

function buildChildrenMap(edges: EmailChainEdge[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const edge of edges) {
    const list = map.get(edge.source_id) ?? [];
    list.push(edge.target_id);
    map.set(edge.source_id, list);
  }
  return map;
}

function assignYCoordinates(
  rootId: string,
  childrenMap: Map<string, string[]>,
): Map<string, { x: number; y: number }> {
  const leafCounts = new Map<string, number>();

  function countLeaves(nodeId: string): number {
    const cached = leafCounts.get(nodeId);
    if (cached !== undefined) return cached;
    const children = childrenMap.get(nodeId) ?? [];
    if (children.length === 0) {
      leafCounts.set(nodeId, 1);
      return 1;
    }
    const total = children.reduce((sum, childId) => sum + countLeaves(childId), 0);
    leafCounts.set(nodeId, total);
    return total;
  }

  countLeaves(rootId);

  const positions = new Map<string, { x: number; y: number }>();

  function place(nodeId: string, depth: number, yStart: number): number {
    const children = childrenMap.get(nodeId) ?? [];
    if (children.length === 0) {
      positions.set(nodeId, { x: depth, y: yStart });
      return yStart + 1;
    }
    let cursor = yStart;
    const childCenters: number[] = [];
    for (const childId of children) {
      const before = cursor;
      cursor = place(childId, depth + 1, cursor);
      childCenters.push((before + cursor - 1) / 2);
    }
    const centerY = childCenters.reduce((a, b) => a + b, 0) / childCenters.length;
    positions.set(nodeId, { x: depth, y: centerY });
    return cursor;
  }

  place(rootId, 0, 0);
  return positions;
}

function edgePath(from: LayoutNode, to: LayoutNode): string {
  const x1 = from.x + from.width;
  const y1 = from.y + from.height / 2;
  const x2 = to.x;
  const y2 = to.y + to.height / 2;
  const midX = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;
}

export function computeChainLayout(chain: EmailChain): ChainLayout {
  const childrenMap = buildChildrenMap(chain.edges);
  const rawPositions = assignYCoordinates(chain.root_node_id, childrenMap);

  const layoutNodes: Record<string, LayoutNode> = {};
  let maxX = 0;
  let maxY = 0;

  for (const node of chain.nodes) {
    const pos = rawPositions.get(node.id) ?? { x: 0, y: 0 };
    const x = PADDING + pos.x * (NODE_WIDTH + H_GAP);
    const y = PADDING + pos.y * (NODE_HEIGHT + V_GAP);
    layoutNodes[node.id] = {
      id: node.id,
      x,
      y,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    };
    maxX = Math.max(maxX, x + NODE_WIDTH);
    maxY = Math.max(maxY, y + NODE_HEIGHT);
  }

  const layoutEdges: LayoutEdge[] = chain.edges.map((edge) => {
    const from = layoutNodes[edge.source_id];
    const to = layoutNodes[edge.target_id];
    return {
      id: edge.id,
      sourceId: edge.source_id,
      targetId: edge.target_id,
      path: from && to ? edgePath(from, to) : '',
    };
  });

  return {
    nodes: layoutNodes,
    edges: layoutEdges,
    width: maxX + PADDING,
    height: maxY + PADDING,
  };
}

export function createEmptyChain(): EmailChain {
  const rootId = `node-${randomIdSuffix()}`;
  return {
    version: 2,
    root_node_id: rootId,
    nodes: [
      {
        id: rootId,
        name: 'Письмо 1',
        kind: 'email',
        email_template_id: null,
        document_template_ids: [],
        consent_on_click: false,
      },
    ],
    edges: [],
  };
}

function nextBlockIndex(chain: EmailChain): number {
  return chain.nodes.length + 1;
}

function appendChild(
  chain: EmailChain,
  parentId: string,
  child: EmailChainNode,
): EmailChain {
  const edgeId = `edge-${randomIdSuffix()}`;
  return {
    ...chain,
    nodes: [...chain.nodes, child],
    edges: [
      ...chain.edges,
      {
        id: edgeId,
        source_id: parentId,
        target_id: child.id,
        button_label: child.name,
      },
    ],
  };
}

export function addChildEmailNode(chain: EmailChain, parentId: string): EmailChain {
  const childId = `node-${randomIdSuffix()}`;
  const childIndex = nextBlockIndex(chain);
  const childName = `Письмо ${childIndex}`;
  return appendChild(chain, parentId, {
    id: childId,
    name: childName,
    kind: 'email',
    email_template_id: null,
    document_template_ids: [],
    consent_on_click: false,
  });
}

const LINK_DEFAULTS: Record<ChainLinkKind, { name: string; link_url?: string }> = {
  custom: { name: 'Ссылка' },
  unsubscribe: { name: 'Отписаться' },
  subscribe: { name: 'Подписаться' },
};

export function addChildLinkNode(
  chain: EmailChain,
  parentId: string,
  linkKind: ChainLinkKind,
): EmailChain {
  const childId = `node-${randomIdSuffix()}`;
  const defaults = LINK_DEFAULTS[linkKind];
  const childIndex = nextBlockIndex(chain);
  const childName =
    linkKind === 'custom' ? `${defaults.name} ${childIndex}` : defaults.name;
  return appendChild(chain, parentId, {
    id: childId,
    name: childName,
    kind: 'link',
    link_kind: linkKind,
    link_url: linkKind === 'custom' ? '' : null,
  });
}

/** @deprecated use addChildEmailNode */
export function addChildNode(chain: EmailChain, parentId: string): EmailChain {
  return addChildEmailNode(chain, parentId);
}

export function removeNodeSubtree(chain: EmailChain, nodeId: string): EmailChain {
  if (nodeId === chain.root_node_id) {
    return chain;
  }
  const toRemove = new Set<string>();
  const queue = [nodeId];
  while (queue.length) {
    const current = queue.pop()!;
    toRemove.add(current);
    for (const edge of chain.edges) {
      if (edge.source_id === current && !toRemove.has(edge.target_id)) {
        queue.push(edge.target_id);
      }
    }
  }
  return {
    ...chain,
    nodes: chain.nodes.filter((n) => !toRemove.has(n.id)),
    edges: chain.edges.filter((e) => !toRemove.has(e.source_id) && !toRemove.has(e.target_id)),
  };
}

export function updateNode(chain: EmailChain, nodeId: string, patch: Partial<EmailChainNode>): EmailChain {
  const next = {
    ...chain,
    nodes: chain.nodes.map((n) => (n.id === nodeId ? { ...n, ...patch } : n)),
  };
  if (patch.name !== undefined && nodeId !== chain.root_node_id) {
    return updateIncomingEdgeLabel(next, nodeId, String(patch.name || 'Перейти'));
  }
  return next;
}

export function updateIncomingEdgeLabel(chain: EmailChain, nodeId: string, buttonLabel: string): EmailChain {
  return {
    ...chain,
    edges: chain.edges.map((e) => (e.target_id === nodeId ? { ...e, button_label: buttonLabel } : e)),
  };
}

export function getIncomingEdge(chain: EmailChain, nodeId: string): EmailChainEdge | undefined {
  return chain.edges.find((e) => e.target_id === nodeId);
}

export { NODE_WIDTH, NODE_HEIGHT };
