import { Alert, Button, Drawer, Empty, Input, Select, Space, Spin, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { formatLocalDateTime } from '@/utils/dateTime';
import { useMemo, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import { previewApi } from '@/api/preview';
import { buildEmailPreviewDocument } from '@/features/templates/emailTemplateUtils';
import { asRecord, asRecordArray, fmt } from '../utils';

type Props = {
  campaignId: string;
  recipients: Array<Record<string, unknown>>;
};

export function FullAnalyticsEmailsSection({ campaignId, recipients }: Props) {
  const [recipientId, setRecipientId] = useState<number | undefined>(
    recipients[0]?.id ? Number(recipients[0].id) : undefined,
  );
  const [activeNodeIndex, setActiveNodeIndex] = useState(0);

  const previewQuery = useQuery({
    queryKey: ['sent-email-preview', campaignId, recipientId],
    enabled: Boolean(campaignId && recipientId),
    queryFn: () => campaignsApi.sentEmailPreview(campaignId, Number(recipientId)),
  });

  const items = asRecordArray(previewQuery.data?.items);
  const activeItem = asRecord(items[activeNodeIndex] || items[0]);
  const previewHtml = useMemo(
    () => buildEmailPreviewDocument(String(activeItem.body_html || '')),
    [activeItem.body_html],
  );

  const recipientOptions = recipients.map((row) => ({
    value: Number(row.id),
    label: [row.company, row.contact_name, row.email].filter(Boolean).join(' · ') || `#${row.id}`,
  }));

  if (!campaignId) {
    return <Empty description="Кампания не связана с job — превью письма недоступно" />;
  }

  if (!recipients.length) {
    return <Empty description="Нет получателей для просмотра писем" />;
  }

  return (
    <div>
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          style={{ minWidth: 320 }}
          showSearch
          optionFilterProp="label"
          placeholder="Выберите получателя"
          value={recipientId}
          options={recipientOptions}
          onChange={(value) => {
            setRecipientId(value);
            setActiveNodeIndex(0);
          }}
        />
        {items.length > 1 ? (
          <Select
            style={{ minWidth: 220 }}
            value={activeNodeIndex}
            options={items.map((item, index) => ({
              value: index,
              label: String(item.node_name || `Письмо ${index + 1}`),
            }))}
            onChange={setActiveNodeIndex}
          />
        ) : null}
      </Space>

      {previewQuery.isLoading ? <Spin /> : null}
      {previewQuery.isError ? (
        <Alert type="error" showIcon message="Не удалось загрузить превью письма" />
      ) : null}

      {previewQuery.data ? (
        <>
          <Typography.Paragraph>
            <Typography.Text strong>Тема: </Typography.Text>
            {String(activeItem.subject || '—')}
            {activeItem.sent_at ? (
              <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
                Принято провайдером: {formatLocalDateTime(String(activeItem.sent_at))}
              </Typography.Text>
            ) : null}
          </Typography.Paragraph>
          <div
            style={{
              border: '1px solid #e2e7d8',
              borderRadius: 8,
              overflow: 'hidden',
              minHeight: 320,
              background: '#fff',
            }}
          >
            <iframe
              title="Превью письма"
              srcDoc={previewHtml}
              sandbox=""
              style={{ width: '100%', minHeight: 420, border: 0 }}
            />
          </div>
          {asRecordArray(activeItem.attachments).length ? (
            <Table
              size="small"
              style={{ marginTop: 16 }}
              pagination={false}
              rowKey={(row) => String(row.template_id || row.filename)}
              dataSource={asRecordArray(activeItem.attachments)}
              columns={[
                { title: 'Файл', dataIndex: 'filename', render: (v) => String(v || '—') },
                {
                  title: '',
                  render: (_, row) =>
                    row.template_id && recipientId ? (
                      <Button
                        size="small"
                        href={campaignsApi.previewEmailChainAttachmentUrl(
                          campaignId,
                          recipientId,
                          String(row.template_id),
                          { download: true },
                        )}
                      >
                        Скачать
                      </Button>
                    ) : null,
                },
              ]}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

type DocumentsProps = {
  jobId: string;
  documentFilter: string;
  onDocumentFilterChange: (value: string) => void;
};

export function FullAnalyticsDocumentsSection({
  jobId,
  documentFilter,
  onDocumentFilterChange,
}: DocumentsProps) {
  const [page, setPage] = useState(1);
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const perPage = 20;

  const query = useQuery({
    queryKey: ['full-analytics-documents', jobId, page, documentFilter],
    enabled: Boolean(jobId),
    queryFn: () =>
      previewApi.archive(jobId, {
        offset: (page - 1) * perPage,
        limit: perPage,
        q: documentFilter || undefined,
      }),
  });

  const entries = asRecordArray(query.data?.entries);
  const total = Number(query.data?.total || 0);

  return (
    <div>
      <Space wrap style={{ marginBottom: 16 }}>
        <Input.Search
          allowClear
          placeholder="Поиск по названию или row_id"
          style={{ minWidth: 280 }}
          defaultValue={documentFilter}
          onSearch={(value) => {
            onDocumentFilterChange(value);
            setPage(1);
          }}
        />
      </Space>
      <Table
        size="small"
        loading={query.isLoading}
        rowKey={(row) => String(row.path)}
        dataSource={entries}
        pagination={{
          current: page,
          pageSize: perPage,
          total,
          onChange: setPage,
          showTotal: (value) => `Всего ${fmt(value)}`,
        }}
        columns={[
          { title: 'Документ', dataIndex: 'label', render: (v, row) => String(v || row.name || '—') },
          { title: 'Тип', dataIndex: 'ext', width: 80 },
          {
            title: 'Размер',
            dataIndex: 'size',
            width: 100,
            render: (v) => `${fmt(Math.round(Number(v || 0) / 1024))} КБ`,
          },
          {
            title: '',
            width: 120,
            render: (_, row) => {
              const ext = String(row.ext || '').toLowerCase();
              if (ext === '.pdf') {
                return (
                  <Button size="small" onClick={() => setPreviewPath(String(row.path))}>
                    Просмотр
                  </Button>
                );
              }
              return (
                <Button
                  size="small"
                  href={previewApi.fileUrl(jobId, String(row.path))}
                  target="_blank"
                  rel="noreferrer"
                >
                  Скачать
                </Button>
              );
            },
          },
        ]}
      />
      <Drawer
        title="Просмотр PDF"
        width="80%"
        open={Boolean(previewPath)}
        onClose={() => setPreviewPath(null)}
        destroyOnClose
      >
        {previewPath ? (
          <iframe
            title="PDF preview"
            src={previewApi.fileUrl(jobId, previewPath)}
            style={{ width: '100%', height: '80vh', border: 0 }}
          />
        ) : null}
      </Drawer>
    </div>
  );
}
