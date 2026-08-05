import { useState } from 'react';
import { ProFormSelect } from '@ant-design/pro-components';
import { Button, Space, Table, Tag, Upload } from 'antd';
import type { Audience, Campaign, Recipient } from '@/api/types';
import { statusLabel } from '@/utils/presentation';

type Props = {
  campaignId?: string;
  draft: Partial<Campaign>;
  audiences: Audience[];
  recipients: Recipient[];
  recipientsTotal: number;
  recipientsLoading?: boolean;
  onAudienceSelect: (audienceId: string) => Promise<void>;
  onImportRecipients: (file: File) => Promise<void>;
  onOpenGenerate: () => void;
  onOpenTopup: () => void;
};

export function CampaignWizardRecipientsStep({
  campaignId,
  draft,
  audiences,
  recipients,
  recipientsLoading,
  onAudienceSelect,
  onImportRecipients,
  onOpenGenerate,
  onOpenTopup,
  recipientsTotal,
}: Props) {
  const [importing, setImporting] = useState(false);
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <div data-onboarding-id="campaign-audience">
        <ProFormSelect
          name="audience_id"
          label="Сохранённая аудитория"
          options={audiences.map((a) => ({
            label: `${a.name} (${a.member_count})`,
            value: a.id,
          }))}
          fieldProps={{
            onChange: async (audienceId: string) => {
              if (!campaignId || !audienceId) return;
              await onAudienceSelect(audienceId);
            },
          }}
        />
      </div>
      <Space wrap data-onboarding-id="campaign-recipient-sources">
        <Upload
          accept=".csv,.xlsx"
          showUploadList={false}
          disabled={importing}
          customRequest={async ({ file, onSuccess, onError }) => {
            setImporting(true);
            try {
              await onImportRecipients(file as File);
              onSuccess?.({});
            } catch (error) {
              onError?.(error as Error);
            } finally {
              setImporting(false);
            }
          }}
        >
          <Button loading={importing}>Загрузить Excel / CSV</Button>
        </Upload>
        <Button disabled={!campaignId || !draft.job_id} onClick={onOpenGenerate}>
          Сгенерировать список
        </Button>
        <Button
          disabled={!campaignId || !draft.job_id || !recipientsTotal}
          onClick={onOpenTopup}
        >
          Дозаполнить
        </Button>
      </Space>
      <div data-onboarding-id="campaign-recipient-check">
        <Table
          rowKey="id"
          size="small"
          loading={recipientsLoading}
          dataSource={recipients}
          pagination={{ pageSize: 10 }}
          columns={[
          { title: 'Компания', dataIndex: 'company' },
          { title: 'Контакт', dataIndex: 'contact_name' },
          { title: 'Email', dataIndex: 'email' },
          {
            title: 'Проверка',
            dataIndex: 'validation_status',
            render: (v) => (
              <Tag color={v === 'valid' ? 'green' : 'red'}>
                {statusLabel(String(v || ''))}
              </Tag>
            ),
          },
          {
            title: 'Исключён',
            dataIndex: 'excluded',
            render: (v) => (v ? 'да' : 'нет'),
          },
          ]}
        />
      </div>
    </Space>
  );
}
