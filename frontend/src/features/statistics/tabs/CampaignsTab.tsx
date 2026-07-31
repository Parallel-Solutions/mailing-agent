import { DeleteOutlined } from '@ant-design/icons';
import { App, Button, Input, Space, Table } from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt } from '../utils';

export function CampaignsTab() {
  const { message, modal } = App.useApp();
  const {
    apiBaseParams,
    refreshNonce,
    requestRefresh,
    openDrilldown,
    openCampaignSummary,
    setTab,
    setFilters,
    setError,
  } = useStatistics();
  const [search, setSearch] = useState('');

  const query = useQuery({
    queryKey: ['stats-campaigns', apiBaseParams, refreshNonce, search],
    queryFn: () =>
      statisticsApi.campaigns({
        ...apiBaseParams,
        q: search || undefined,
        refresh: refreshNonce > 0 ? true : undefined,
      }),
  });

  const archiveCampaign = useMutation({
    mutationFn: (campaignId: string) => campaignsApi.archive(campaignId),
    onSuccess: () => {
      message.success('Рассылка удалена');
      requestRefresh();
    },
    onError: (error: Error) => message.error(error.message),
  });

  const confirmDelete = (row: Record<string, unknown>) => {
    const campaignId = String(row.campaign_id || '');
    if (!campaignId) return;
    modal.confirm({
      title: 'Удалить рассылку?',
      content: `Рассылка «${String(row.title || 'Без названия')}» исчезнет из общего списка и статистики. История отправки сохранится.`,
      okText: 'Удалить',
      cancelText: 'Отмена',
      okButtonProps: { danger: true },
      onOk: () => archiveCampaign.mutateAsync(campaignId),
    });
  };

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить рассылки.');
  }, [query.isError, setError]);

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const campaigns = asRecordArray(result.campaigns);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return campaigns;
    return campaigns.filter((item) => String(item.title || '').toLowerCase().includes(q));
  }, [campaigns, search]);

  const kpis = [
    { title: 'Всего рассылок', value: fmt(summary.total), drill: 'campaigns_all' },
    { title: 'Активные', value: fmt(summary.active), drill: 'campaigns_active' },
    { title: 'Завершённые', value: fmt(summary.completed), drill: 'campaigns_completed' },
    { title: 'Черновики', value: fmt(summary.draft), drill: 'campaigns_draft' },
    { title: 'Запланированные', value: fmt(summary.scheduled), drill: 'campaigns_scheduled' },
    {
      title: 'Средняя доставляемость',
      value: `${summary.avg_delivery_rate ?? 0}%`,
      drill: 'campaigns_delivery',
    },
    {
      title: 'Средняя открываемость',
      value: `${summary.avg_open_rate ?? 0}%`,
      drill: 'campaigns_open',
    },
  ];

  return (
    <div>
      <KpiGrid items={kpis} loading={query.isLoading} onDrill={(key) => void openDrilldown(key)} />
      <Input.Search
        allowClear
        placeholder="Поиск рассылки"
        style={{ marginTop: 16, maxWidth: 360 }}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <Table
        style={{ marginTop: 16 }}
        loading={query.isLoading}
        rowKey={(row) => String(row.job_id)}
        dataSource={rows}
        pagination={{ pageSize: 10 }}
        onRow={(row) => ({
          onClick: () => openCampaignSummary(String(row.job_id)),
          style: { cursor: 'pointer' },
        })}
        locale={{ emptyText: 'Пока нет рассылок с отправками' }}
        columns={[
          { title: 'Название', dataIndex: 'title' },
          { title: 'Период', dataIndex: 'period_label' },
          { title: 'Провайдер', dataIndex: 'provider_label' },
          { title: 'Принято провайдером', dataIndex: 'sent', render: (v) => fmt(v) },
          {
            title: 'Доставлено',
            render: (_, r) => `${fmt(r.delivered)} / ${r.delivery_rate}%`,
          },
          {
            title: 'Открыто',
            render: (_, r) => `${fmt(r.opened)} / ${r.open_rate}%`,
          },
          {
            title: 'Переходы',
            render: (_, r) => `${fmt(r.clicked)} / ${r.ctr}%`,
          },
          { title: 'Согласия', dataIndex: 'consents', render: (v) => fmt(v) },
          { title: 'Статус', dataIndex: 'status_label' },
          {
            title: 'Действия',
            key: 'actions',
            fixed: 'right',
            width: 190,
            render: (_, r) => (
              <Space size={4}>
                <Button
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    const jobId = String(r.job_id);
                    setFilters({ campaign: jobId });
                    setTab('campaign-analytics', { campaign: jobId });
                  }}
                >
                  Аналитика
                </Button>
                {r.can_delete && r.campaign_id ? (
                  <Button
                    type="link"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    loading={
                      archiveCampaign.isPending &&
                      archiveCampaign.variables === String(r.campaign_id)
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      confirmDelete(r);
                    }}
                  >
                    Удалить
                  </Button>
                ) : null}
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}
