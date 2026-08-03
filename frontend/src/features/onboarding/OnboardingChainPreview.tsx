import { Button, Card, Modal, Space, Tag, Typography } from 'antd';
import type { EmailChain } from '@/api/types';
import { ChainCanvas } from '@/features/campaigns/chain/ChainCanvas';
import { ChainNodeBlock } from '@/features/campaigns/chain/ChainNodeBlock';
import { computeChainLayout } from '@/features/campaigns/chain/chainUtils';
import '@/pages/EmailChainBuilderPage.css';

const DEMO_CHAIN: EmailChain = {
  version: 2,
  root_node_id: 'preview-email-1',
  nodes: [
    {
      id: 'preview-email-1',
      name: 'Первое письмо',
      kind: 'email',
      email_template_id: null,
      document_template_ids: [],
    },
    {
      id: 'preview-link',
      name: 'Узнать больше',
      kind: 'link',
      link_kind: 'custom',
      link_url: 'https://example.test/material',
    },
    {
      id: 'preview-email-2',
      name: 'Повторное письмо',
      kind: 'email',
      email_template_id: null,
      document_template_ids: [],
    },
  ],
  edges: [
    {
      id: 'preview-edge-1',
      source_id: 'preview-email-1',
      target_id: 'preview-link',
      button_label: 'Узнать больше',
    },
    {
      id: 'preview-edge-2',
      source_id: 'preview-email-1',
      target_id: 'preview-email-2',
      button_label: 'Следующее письмо',
    },
  ],
};

const DEMO_LAYOUT = computeChainLayout(DEMO_CHAIN);

type OnboardingChainPreviewProps = {
  open: boolean;
};

export function OnboardingChainPreview({ open }: OnboardingChainPreviewProps) {
  return (
    <Modal
      open={open}
      title="Конструктор цепочки — демонстрация"
      width={960}
      footer={null}
      closable={false}
      maskClosable={false}
    >
      <div className="onboarding-chain-preview">
        <div
          data-onboarding-id="chain-publish"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
            marginBottom: 16,
          }}
        >
          <div data-onboarding-id="chain-name-status">
            <Typography.Text strong>Пример цепочки</Typography.Text>
            <div>
              <Tag>Черновик</Tag>
            </div>
          </div>
          <Space wrap data-onboarding-id="chain-save-publish">
            <Button disabled data-onboarding-id="chain-save">Сохранить</Button>
            <Button type="primary" disabled data-onboarding-id="chain-publish-button">
              Опубликовать
            </Button>
          </Space>
        </div>

        <Space wrap data-onboarding-id="chain-add-nodes" style={{ marginBottom: 16 }}>
          <Button disabled>Добавить письмо</Button>
          <Button disabled>Добавить ссылку</Button>
          <Typography.Text type="secondary">
            Новые узлы добавляются после выбранного письма.
          </Typography.Text>
        </Space>

        <div className="onboarding-chain-preview__workspace">
          <div
            data-onboarding-id="chain-builder"
            className="email-chain-canvas-wrap"
            style={{ height: 330, minHeight: 330 }}
          >
            <div
              className="email-chain-canvas-stage"
              style={{ width: DEMO_LAYOUT.width, height: DEMO_LAYOUT.height }}
            >
              <ChainCanvas layout={DEMO_LAYOUT} />
              <ChainNodeBlock
                chain={DEMO_CHAIN}
                layout={DEMO_LAYOUT}
                selectedNodeId="preview-email-1"
                onSelectNode={() => undefined}
                onAddChildEmail={() => undefined}
                onAddChildLink={() => undefined}
              />
            </div>
          </div>

          <Card
            size="small"
            title="Настройки узла"
            data-onboarding-id="chain-settings"
          >
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <div data-onboarding-id="chain-email-template">
                <Typography.Text type="secondary">Шаблон письма</Typography.Text>
                <div><Typography.Text>Первое касание</Typography.Text></div>
              </div>
              <div data-onboarding-id="chain-documents">
                <Typography.Text type="secondary">Документы</Typography.Text>
                <div><Typography.Text>Коммерческое предложение.pdf</Typography.Text></div>
              </div>
              <div data-onboarding-id="chain-transitions">
                <Typography.Text type="secondary">Переходы</Typography.Text>
                <div><Typography.Text>Ссылка и следующее письмо</Typography.Text></div>
              </div>
              <div data-onboarding-id="chain-link-purpose">
                <Typography.Text type="secondary">Назначение ссылки</Typography.Text>
                <div><Typography.Text>Обычная ссылка, подписка или отписка</Typography.Text></div>
              </div>
            </Space>
          </Card>
        </div>
      </div>
    </Modal>
  );
}
