import { Alert, Modal, Spin, Tabs, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { campaignsApi } from '@/api/campaigns';
import type { EmailChainPreviewItem } from '@/api/types';
import { buildEmailPreviewDocument } from '@/features/templates/emailTemplateUtils';
import './ChainEmailPreviewModal.css';

type Props = {
  open: boolean;
  campaignId: string;
  onClose: () => void;
};

function recipientLabel(company?: string, contactName?: string, email?: string): string {
  return [company, contactName, email].filter(Boolean).join(' · ') || '—';
}

function PreviewTabContent({ item, recipientId, campaignId }: {
  item: EmailChainPreviewItem;
  recipientId: number;
  campaignId: string;
}) {
  const previewHtml = useMemo(
    () => buildEmailPreviewDocument(item.body_html),
    [item.body_html],
  );

  return (
    <div className="chain-email-preview-tab">
      <Typography.Text type="secondary">Тема: {item.subject || '—'}</Typography.Text>
      <div className="chain-email-preview-frame-wrap">
        <iframe title={`Предпросмотр: ${item.node_name}`} sandbox="" srcDoc={previewHtml} />
      </div>
      {item.attachments.length > 0 ? (
        <div className="chain-email-preview-attachments">
          <Typography.Text strong>Вложения</Typography.Text>
          <ul>
            {item.attachments.map((attachment) => (
              <li key={attachment.template_id}>
                {attachment.has_content ? (
                  <a
                    href={campaignsApi.previewEmailChainAttachmentUrl(
                      campaignId,
                      recipientId,
                      attachment.template_id,
                    )}
                    download={attachment.filename || undefined}
                  >
                    Скачать {attachment.filename}
                  </a>
                ) : (
                  <Typography.Text type="danger">
                    {attachment.filename || attachment.template_id}
                    {attachment.error ? `: ${attachment.error}` : ''}
                  </Typography.Text>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function ChainEmailPreviewModal({ open, campaignId, onClose }: Props) {
  const previewQuery = useQuery({
    queryKey: ['email-chain-preview', campaignId],
    queryFn: () => campaignsApi.previewEmailChain(campaignId),
    enabled: open && Boolean(campaignId),
  });

  const preview = previewQuery.data;
  const recipientCaption = preview
    ? recipientLabel(preview.recipient.company, preview.recipient.contact_name, preview.recipient.email)
    : '';

  return (
    <Modal
      open={open}
      title="Предпросмотр цепочки писем"
      width={920}
      onCancel={onClose}
      footer={null}
      destroyOnClose
    >
      {previewQuery.isLoading ? (
        <div className="chain-email-preview-loading">
          <Spin tip="Формируем препросмотр…" />
        </div>
      ) : null}
      {previewQuery.isError ? (
        <Alert
          type="error"
          showIcon
          message={(previewQuery.error as Error).message || 'Не удалось загрузить препросмотр'}
        />
      ) : null}
      {preview ? (
        <>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
            Данные первой строки списка: {recipientCaption}
          </Typography.Paragraph>
          <Tabs
            items={preview.items.map((item) => ({
              key: item.node_id,
              label: item.node_name,
              children: (
                <PreviewTabContent
                  item={item}
                  recipientId={preview.recipient.id}
                  campaignId={campaignId}
                />
              ),
            }))}
          />
        </>
      ) : null}
    </Modal>
  );
}
