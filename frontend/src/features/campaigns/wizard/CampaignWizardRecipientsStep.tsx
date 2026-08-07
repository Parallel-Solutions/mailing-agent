import { useEffect, useState } from 'react';
import { ProFormSelect } from '@ant-design/pro-components';
import { Alert, Button, Progress, Space, Table, Tag, Upload } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { campaignsApi } from '@/api/campaigns';
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
  const queryClient = useQueryClient();
  const validationQuery = useQuery({
    queryKey: ['campaign-email-validation', campaignId],
    queryFn: () => campaignsApi.emailValidation(campaignId!),
    enabled: Boolean(campaignId),
    refetchInterval: 3000,
  });
  const startValidation = useMutation({
    mutationFn: () => campaignsApi.startEmailValidation(campaignId!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['campaign-email-validation', campaignId] });
    },
  });
  const validation = validationQuery.data;

  useEffect(() => {
    if (!campaignId || validation?.status !== 'completed') return;
    void queryClient.invalidateQueries({ queryKey: ['campaign-recipients', campaignId] });
  }, [campaignId, queryClient, validation?.completed_at, validation?.status]);

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
      {campaignId && validation?.enabled ? (
        <Alert
          showIcon
          type={
            validation.status === 'failed'
              ? 'error'
              : validation.status === 'completed' && validation.unknown_count === 0
                ? 'success'
                : 'info'
          }
          message="Предварительная проверка SMTP.BZ"
          description={(
            <Space direction="vertical" style={{ width: '100%' }}>
              <Progress percent={validation.progress_percent} size="small" />
              <span>
                Проверено: {validation.processed_count}/{validation.total_count}. Валидных: {validation.valid_count},
                невалидных: {validation.invalid_count}, требуют повтора: {validation.unknown_count}.
              </span>
              {validation.error ? <span>{validation.error}</span> : null}
            </Space>
          )}
          action={(
            <Button
              size="small"
              loading={startValidation.isPending}
              onClick={() => startValidation.mutate()}
            >
              {validation.status === 'not_started' ? 'Запустить проверку' : 'Проверить повторно'}
            </Button>
          )}
        />
      ) : null}
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
              <Tag color={v === 'valid' ? 'green' : v === 'invalid' ? 'red' : 'gold'}>
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
