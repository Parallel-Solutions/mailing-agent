import { LinkOutlined, MailOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import type { ChainLinkKind, EmailChain } from '@/api/types';
import type { ChainLayout } from './chainUtils';
import { isLinkNode, nodeKind } from './chainUtils';

type Props = {
  chain: EmailChain;
  layout: ChainLayout;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  onAddChildEmail: (nodeId: string) => void;
  onAddChildLink: (nodeId: string, linkKind: ChainLinkKind) => void;
};

const LINK_KIND_LABELS: Record<ChainLinkKind, string> = {
  custom: 'Пользовательская',
  unsubscribe: 'Отписаться',
  subscribe: 'Подписаться',
};

function buildAddMenu(
  nodeId: string,
  onAddChildEmail: (nodeId: string) => void,
  onAddChildLink: (nodeId: string, linkKind: ChainLinkKind) => void,
): MenuProps {
  return {
    items: [
      {
        key: 'email',
        label: 'Письмо',
        onClick: () => onAddChildEmail(nodeId),
      },
      {
        key: 'link',
        label: 'Ссылка',
        children: (['custom', 'unsubscribe', 'subscribe'] as ChainLinkKind[]).map((kind) => ({
          key: `link-${kind}`,
          label: LINK_KIND_LABELS[kind],
          onClick: () => onAddChildLink(nodeId, kind),
        })),
      },
    ],
  };
}

export function ChainNodeBlock({
  chain,
  layout,
  selectedNodeId,
  onSelectNode,
  onAddChildEmail,
  onAddChildLink,
}: Props) {
  return (
    <>
      {chain.nodes.map((node) => {
        const pos = layout.nodes[node.id];
        if (!pos) return null;
        const selected = selectedNodeId === node.id;
        const link = isLinkNode(node);
        const linkKind = node.link_kind;
        return (
          <div
            key={node.id}
            className={`chain-node-block${selected ? ' chain-node-block--selected' : ''}${link ? ' chain-node-block--link' : ''}`}
            style={{ left: pos.x, top: pos.y, width: pos.width, height: pos.height }}
            onClick={() => onSelectNode(node.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelectNode(node.id);
              }
            }}
            role="button"
            tabIndex={0}
          >
            {link ? (
              <LinkOutlined className="chain-node-block__icon chain-node-block__icon--link" />
            ) : (
              <MailOutlined className="chain-node-block__icon" />
            )}
            <div className="chain-node-block__title">{node.name}</div>
            {link && linkKind && linkKind !== 'custom' && (
              <div className="chain-node-block__badge">{LINK_KIND_LABELS[linkKind]}</div>
            )}
            {!link && (
              <Dropdown
                menu={buildAddMenu(node.id, onAddChildEmail, onAddChildLink)}
                trigger={['click']}
              >
                <Button
                  type="primary"
                  size="small"
                  shape="circle"
                  icon={<PlusOutlined />}
                  className="chain-node-block__add"
                  onClick={(event) => event.stopPropagation()}
                />
              </Dropdown>
            )}
          </div>
        );
      })}
    </>
  );
}

export { nodeKind };
