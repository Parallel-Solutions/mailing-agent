import { ProCard } from '@ant-design/pro-components';
import { App, Button, Progress, Space, Table, Tabs, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';

export function CampaignDetailPage() {
  const { id = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'overview';
  const { message } = App.useApp();
  const queryClient = useQueryClient();

  const campaignQuery = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => campaignsApi.get(id),
    enabled: Boolean(id),
  });
  const recipientsQuery = useQuery({
    queryKey: ['campaign-recipients', id],
    queryFn: () => campaignsApi.recipients(id, { limit: 50 }),
    enabled: Boolean(id),
  });
  const batchesQuery = useQuery({
    queryKey: ['campaign-batches', id],
    queryFn: () => campaignsApi.batches(id),
    enabled: Boolean(id),
    refetchInterval: 8_000,
  });
  const scheduleQuery = useQuery({
    queryKey: ['campaign-schedule', id],
    queryFn: () => campaignsApi.getSchedule(id),
    enabled: Boolean(id),
  });

  const camp = campaignQuery.data;

  const chainStatsQuery = useQuery({
    queryKey: ['email-chain-stats', id],
    queryFn: () => campaignsApi.getEmailChainStats(id),
    enabled: Boolean(id) && camp?.send_scenario === 'email_chain',
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

  return (
    <div>
      <Space style={{ marginBottom: 16 }} wrap>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {camp?.name || 'Рассылка'}
        </Typography.Title>
        <Tag>{camp?.status}</Tag>
        <Link to={`/campaigns/new?id=${id}`}>Редактировать</Link>
        <Link to={`/campaigns/${id}/chain`}>Настроить цепочку</Link>
        {camp?.status === 'paused' ? (
          <Button loading={resume.isPending} onClick={() => resume.mutate()}>
            Продолжить
          </Button>
        ) : (
          <Button loading={pause.isPending} onClick={() => pause.mutate()}>
            Пауза
          </Button>
        )}
        <Button danger loading={cancel.isPending} onClick={() => cancel.mutate()}>
          Отменить
        </Button>
      </Space>

      <Progress percent={camp?.progress || 0} />

      <Tabs
        activeKey={tab}
        onChange={(key) => setParams({ tab: key })}
        items={[
          {
            key: 'overview',
            label: 'Обзор',
            children: (
              <ProCard bordered>
                <p>Тема: {camp?.mail_subject}</p>
                <p>
                  Прогресс: {camp?.sent_count}/{camp?.total_count}, ошибки: {camp?.error_count}
                </p>
                <p>Сценарий: {camp?.send_scenario}</p>
                <p>Job: {camp?.job_id}</p>
                {camp?.send_scenario === 'email_chain' && (
                  <div>
                    <Typography.Text strong>Переходы по веткам: </Typography.Text>
                    {(chainStatsQuery.data?.edges ?? []).map((edge) => (
                      <div key={edge.edge_id}>
                        {edge.edge_id}: {edge.clicks} / {edge.tokens}
                      </div>
                    ))}
                    <div style={{ marginTop: 8 }}>
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
                  { title: 'Статус', dataIndex: 'send_status' },
                  { title: 'Ошибка', dataIndex: 'last_error' },
                ]}
              />
            ),
          },
          {
            key: 'queue',
            label: 'Очередь',
            children: (
              <Table
                rowKey="id"
                loading={batchesQuery.isLoading}
                dataSource={batchesQuery.data || []}
                columns={[
                  { title: 'Пакет', dataIndex: 'batch_index' },
                  { title: 'Время', dataIndex: 'scheduled_at' },
                  { title: 'Кол-во', dataIndex: 'size' },
                  { title: 'Отправлено', dataIndex: 'sent_count' },
                  { title: 'Осталось', dataIndex: 'remaining' },
                  { title: 'Статус', dataIndex: 'status' },
                  { title: 'Ошибки', dataIndex: 'error_count' },
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
            ),
          },
          {
            key: 'stats',
            label: 'Статистика',
            children: (
              <ProCard bordered>
                <p>Отправлено: {camp?.sent_count}</p>
                <p>Ошибки: {camp?.error_count}</p>
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
                  { title: 'Ошибка', dataIndex: 'last_error' },
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
                      Старт: {scheduleQuery.data.start_at || '—'}
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
