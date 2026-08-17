import { ProCard } from '@ant-design/pro-components';
import { Alert, App, Button, Progress, Space, Table, Tabs, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';
import {
  campaignProgressLabel,
  canCampaignAction,
  shouldPollCampaign,
} from '@/features/campaigns/campaignLifecycle';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { formatScheduleDateTime } from '@/utils/scheduleForm';
import { errorLabel, scenarioLabel, statusLabel } from '@/utils/presentation';
import { readEnumParam } from '@/utils/urlState';

const CAMPAIGN_DETAIL_TABS = ['overview', 'recipients', 'queue', 'stats', 'errors', 'settings'] as const;

export function CampaignDetailPage() {
  const { id = '' } = useParams();
  const { searchParams, pushParams } = useUrlNavigation();
  const tab = readEnumParam(searchParams, 'tab', CAMPAIGN_DETAIL_TABS, 'overview');
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const campaignQuery = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => campaignsApi.get(id),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return shouldPollCampaign(status) ? 10_000 : 30_000;
    },
  });
  const camp = campaignQuery.data;

  const recipientsQuery = useQuery({
    queryKey: ['campaign-recipients', id],
    queryFn: () => campaignsApi.recipients(id, { limit: 50 }),
    enabled: Boolean(id),
    refetchInterval: () => (shouldPollCampaign(camp?.status) ? 10_000 : 30_000),
  });
  const batchesQuery = useQuery({
    queryKey: ['campaign-batches', id],
    queryFn: () => campaignsApi.batches(id),
    enabled: Boolean(id),
    refetchInterval: () => (shouldPollCampaign(camp?.status) ? 10_000 : 30_000),
  });
  const scheduleQuery = useQuery({
    queryKey: ['campaign-schedule', id],
    queryFn: () => campaignsApi.getSchedule(id),
    enabled: Boolean(id),
    refetchInterval: 30_000,
  });

  const chainStatsQuery = useQuery({
    queryKey: ['email-chain-stats', id],
    queryFn: () => campaignsApi.getEmailChainStats(id),
    enabled: Boolean(id) && camp?.send_scenario === 'email_chain',
    refetchInterval: () => (shouldPollCampaign(camp?.status) ? 10_000 : 30_000),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['campaign', id] });
    void queryClient.invalidateQueries({ queryKey: ['campaign-batches', id] });
    void queryClient.invalidateQueries({ queryKey: ['active-sending'] });
  };

  const pause = useMutation({
    mutationFn: () => campaignsApi.pause(id),
    onSuccess: () => {
      message.success('Пауза');
      invalidate();
    },
  });
  const resume = useMutation({
    mutationFn: () => campaignsApi.resume(id),
    onSuccess: () => {
      message.success('Продолжено');
      invalidate();
    },
  });
  const cancel = useMutation({
    mutationFn: () => campaignsApi.cancel(id),
    onSuccess: () => {
      message.success('Отменено');
      invalidate();
    },
  });
  const duplicate = useMutation({
    mutationFn: () => campaignsApi.duplicate(id),
    onSuccess: (copy) => {
      message.success('Копия создана');
      navigate(`/campaigns/new?id=${copy.id}`);
    },
    onError: (error: Error) => message.error(error.message),
  });

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {camp?.name || 'Рассылка'}
        </Typography.Title>
        <Tag>{statusLabel(camp?.status)}</Tag>
        {canCampaignAction(camp, 'edit') ? <Link to={`/campaigns/new?id=${id}`}>Редактировать</Link> : null}
        {canCampaignAction(camp, 'duplicate') ? (
          <Button loading={duplicate.isPending} onClick={() => duplicate.mutate()}>
            Создать копию
          </Button>
        ) : null}
        {canCampaignAction(camp, 'edit') ? <Link to={`/campaigns/${id}/chain`}>Настроить цепочку</Link> : null}
        {canCampaignAction(camp, 'resume') ? (
          <Button loading={resume.isPending} onClick={() => resume.mutate()}>
            Продолжить
          </Button>
        ) : canCampaignAction(camp, 'pause') ? (
          <Button loading={pause.isPending} onClick={() => pause.mutate()}>
            Пауза
          </Button>
        ) : null}
        {canCampaignAction(camp, 'cancel') ? (
          <Button danger loading={cancel.isPending} onClick={() => cancel.mutate()}>
            Отменить
          </Button>
        ) : null}
      </Space>

      <Progress
        percent={camp?.progress || 0}
        format={() => (camp ? campaignProgressLabel(camp) : '0/0')}
      />

      <Tabs
        activeKey={tab}
        onChange={(key) => pushParams({ tab: key === 'overview' ? null : key })}
        items={[
          {
            key: 'overview',
            label: 'Обзор',
            children: (
              <ProCard bordered>
                <p>Тема: {camp?.mail_subject}</p>
                <p>
                  Обработано: {camp?.processed_count}/{camp?.total_count}, принято провайдером:{' '}
                  {camp?.success_count ?? camp?.sent_count}, пропущено: {camp?.skipped_count ?? 0}, ошибки:{' '}
                  {camp?.failed_recipient_count ?? 0}
                  {typeof camp?.layout_error_count === 'number' && camp.layout_error_count > 0
                    ? `, КП не влезло: ${camp.layout_error_count}`
                    : ''}
                </p>
                <p>Сценарий: {scenarioLabel(camp?.send_scenario)}</p>
                {camp?.send_scenario === 'email_chain' && (
                  <div>
                    <Typography.Text strong>Переходы по веткам: </Typography.Text>
                    {(chainStatsQuery.data?.edges ?? []).map((edge, index) => (
                      <div key={edge.edge_id}>
                        Переход {index + 1}: {edge.clicks} из {edge.tokens}
                      </div>
                    ))}
                    <div style={{ marginTop: 8 }}>
                      <Typography.Text strong>Запросили КП: </Typography.Text>
                      {chainStatsQuery.data?.consents?.materials_request?.count ?? 0}
                    </div>
                    <div>
                      <Typography.Text strong>Подписались: </Typography.Text>
                      {chainStatsQuery.data?.consents?.subscribe?.count ?? 0}
                    </div>
                    <div>
                      <Typography.Text strong>Отписались: </Typography.Text>
                      {chainStatsQuery.data?.consents?.unsubscribe?.count ?? 0}
                    </div>
                  </div>
                )}
              </ProCard>
            ),
          },
          {
            key: 'recipients',
            label: 'Получатели',
            children: (
              <Table
                rowKey="id"
                loading={recipientsQuery.isLoading}
                dataSource={recipientsQuery.data?.items || []}
                columns={[
                  { title: 'Компания', dataIndex: 'company' },
                  { title: 'Email', dataIndex: 'email' },
                  {
                    title: 'Статус',
                    dataIndex: 'send_status',
                    render: (value: string, row) => (
                      <Space size={4}>
                        <span>{statusLabel(value)}</span>
                        {row.layout_error_code === 'kp_font_compact' ? (
                          <Tag color="error">КП не влезло</Tag>
                        ) : null}
                      </Space>
                    ),
                  },
                  { title: 'Ошибка', dataIndex: 'last_error', render: errorLabel },
                ]}
              />
            ),
          },
          {
            key: 'queue',
            label: 'Очередь',
            children: (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Alert
                  showIcon
                  type="info"
                  message="Очередь обновляется автоматически без перезагрузки страницы."
                  description="Позиция берётся из реальной очереди отправщика. Плановое время и причина ожидания учитывают расписание, паузу и повтор после ошибки."
                />
                <Table
                  rowKey="id"
                  loading={batchesQuery.isLoading}
                  dataSource={batchesQuery.data || []}
                  columns={[
                    { title: 'Пакет', dataIndex: 'batch_index', render: (value: number) => value + 1 },
                    {
                      title: 'Позиция',
                      dataIndex: 'queue_position',
                      render: (value: number | null, row) =>
                        row.is_current ? <Tag color="processing">Отправляется сейчас</Tag> : value ? `№ ${value}` : '—',
                    },
                    {
                      title: 'Плановое время',
                      dataIndex: 'available_at',
                      render: (value: string) => formatScheduleDateTime(value),
                    },
                    { title: 'Получателей', dataIndex: 'size' },
                    { title: 'Принято провайдером', dataIndex: 'sent_count' },
                    { title: 'Обработано', dataIndex: 'processed_count' },
                    { title: 'Итоговые ошибки', dataIndex: 'failed_recipient_count' },
                    { title: 'Осталось', dataIndex: 'remaining' },
                    {
                      title: 'Статус',
                      dataIndex: 'task_status',
                      render: (value: string, row) => statusLabel(value || row.status),
                    },
                    { title: 'Причина ожидания', dataIndex: 'wait_reason' },
                    { title: 'Неудачные попытки', dataIndex: 'error_count' },
                    {
                      title: 'Действия',
                      render: (_, row) =>
                        ['pending', 'paused'].includes(row.status) ? (
                          <Button
                            size="small"
                            onClick={async () => {
                              await campaignsApi.cancelBatch(id, row.id);
                              message.success('Пакет отменён');
                              invalidate();
                            }}
                          >
                            Отменить
                          </Button>
                        ) : null,
                    },
                  ]}
                />
              </Space>
            ),
          },
          {
            key: 'stats',
            label: 'Статистика',
            children: (
              <ProCard bordered>
                <p>Обработано: {camp?.processed_count ?? 0} из {camp?.total_count ?? 0}</p>
                <p>Попытки отправки: {camp?.attempt_count ?? 0}</p>
                <p>Принято провайдером: {camp?.success_count ?? camp?.sent_count ?? 0} ({camp?.success_rate ?? 0}%)</p>
                <p>Пропущено: {camp?.skipped_count ?? 0}</p>
                <p>Итоговые ошибки получателей: {camp?.failed_recipient_count ?? 0}</p>
                <p>Технические ошибки попыток: {camp?.attempt_error_count ?? camp?.error_count ?? 0}</p>
                <Typography.Paragraph type="secondary">
                  Неудачная отправка увеличивает только число попыток. «Доставлено» появляется после подтверждения почтового сервиса в подробной статистике.
                </Typography.Paragraph>
                {(camp?.layout_error_count ?? 0) > 0 ? (
                  <p>КП не влезло на 1 стр.: {camp?.layout_error_count}</p>
                ) : null}
                <Link
                  to={{
                    pathname: '/',
                    search: camp?.job_id
                      ? `?tab=campaign-analytics&campaign=${encodeURIComponent(String(camp.job_id))}`
                      : '',
                  }}
                >
                  Открыть статистику отправок
                </Link>
              </ProCard>
            ),
          },
          {
            key: 'errors',
            label: 'Ошибки',
            children: (
              <Table
                rowKey="id"
                dataSource={(recipientsQuery.data?.items || []).filter((r) => r.send_status === 'failed')}
                columns={[
                  { title: 'Email', dataIndex: 'email' },
                  { title: 'Ошибка', dataIndex: 'last_error', render: errorLabel },
                ]}
              />
            ),
          },
          {
            key: 'settings',
            label: 'Настройки',
            children: (
              <ProCard bordered loading={scheduleQuery.isLoading} title="Расписание">
                {scheduleQuery.data ? (
                  <Space direction="vertical">
                    <Typography.Text>
                      Старт: {formatScheduleDateTime(scheduleQuery.data.start_at, scheduleQuery.data.timezone)}
                    </Typography.Text>
                    <Typography.Text>
                      Размер пакета: {scheduleQuery.data.batch_size ?? '—'}
                    </Typography.Text>
                    <Typography.Text>
                      Интервал: {scheduleQuery.data.interval_seconds ?? '—'} сек
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      Изменение расписания — в мастере создания или через API.
                    </Typography.Text>
                  </Space>
                ) : (
                  <Typography.Text type="secondary">Расписание не задано</Typography.Text>
                )}
              </ProCard>
            ),
          },
        ]}
      />
    </div>
  );
}
