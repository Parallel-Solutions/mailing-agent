import { useEffect, useState } from 'react';
import { ProFormSelect } from '@ant-design/pro-components';
import { Alert, Button, Progress, Space, Table, Tag, Tooltip, Upload } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { campaignsApi } from '@/api/campaigns';
import type { Audience, Campaign, Recipient } from '@/api/types';
import { emailValidationReason, localEmailValidationStatusLabel } from '@/utils/emailValidation';
import { emailValidationRefetchInterval } from '@/utils/emailValidationPolling';
import { campaignEmailValidationQueryKey } from '../campaignQueryUtils';

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
  const validationQueryKey = campaignEmailValidationQueryKey(campaignId || '');
  const validationQuery = useQuery({
    queryKey: validationQueryKey,
    queryFn: () => campaignsApi.emailValidation(campaignId!),
    enabled: Boolean(campaignId),
    refetchOnMount: 'always',
    refetchInterval: (query) => emailValidationRefetchInterval(query.state.data?.status),
  });
  const startValidation = useMutation({
    mutationFn: () => campaignsApi.startEmailValidation(campaignId!),
    onSuccess: (run) => {
      queryClient.setQueryData(validationQueryKey, run);
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
              : validation.status === 'completed'
                ? validation.invalid_count > 0 ? 'warning' : 'success'
                : 'info'
          }
          message="Внутренняя проверка формата и DNS/MX"
          description={(
            <Space direction="vertical" style={{ width: '100%' }}>
              <Progress percent={validation.progress_percent} size="small" />
              <span>
                Проверено: {validation.processed_count}/{validation.total_count}. Корректные: {validation.valid_count},
                некорректные: {validation.invalid_count}, временно не проверены: {validation.unknown_count}.
              </span>
              <span>Некорректный формат или отсутствующий почтовый маршрут исключают адрес из рассылки.</span>
              {validation.error ? <span>{validation.error}</span> : null}
            </Space>
          )}
          action={(
            <Button
              size="small"
              loading={startValidation.isPending}
              onClick={() => startValidation.mutate()}
            >
              {validation.status === 'not_started' ? 'Проверить адреса' : 'Проверить повторно'}
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
            render: (v, row: Recipient) => {
              const reason = emailValidationReason(row);
              return (
                <Tooltip title={reason || undefined}>
                  <Tag color={v === 'valid' ? 'green' : v === 'invalid' ? 'red' : 'gold'}>
                    {localEmailValidationStatusLabel(String(v || ''))}
                  </Tag>
                </Tooltip>
              );
            },
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
