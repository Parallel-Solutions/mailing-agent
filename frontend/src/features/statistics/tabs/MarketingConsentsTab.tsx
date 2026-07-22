import { Card, Input, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt } from '../utils';

export function MarketingConsentsTab() {
  const {
    apiBaseParams,
    filters,
    setFilters,
    pagination,
    setPage,
    refreshNonce,
    setError,
  } = useStatistics();
  const [search, setSearch] = useState(filters.q || '');

  useEffect(() => {
    const timer = setTimeout(() => {
      if ((filters.q || '') !== search) {
        setFilters({ q: search || undefined }, { resetPages: true });
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [search, filters.q, setFilters]);

  const subscribesQuery = useQuery({
    queryKey: ['stats-subscribes', apiBaseParams, filters.q, pagination.subscribes, refreshNonce],
    queryFn: () =>
      statisticsApi.chainSubscribes({
        ...apiBaseParams,
        q: filters.q,
        page: pagination.subscribes,
        per_page: 10,
      }),
  });

  const unsubscribesQuery = useQuery({
    queryKey: ['stats-unsubscribes', apiBaseParams, filters.q, pagination.unsubscribes, refreshNonce],
    queryFn: () =>
      statisticsApi.unsubscribes({
        ...apiBaseParams,
        q: filters.q,
        page: pagination.unsubscribes,
        per_page: 10,
      }),
  });

  useEffect(() => {
    if (subscribesQuery.isError || unsubscribesQuery.isError) {
      setError('Не удалось загрузить подписки и отписки.');
    }
  }, [subscribesQuery.isError, unsubscribesQuery.isError, setError]);

  const subscribesResult = subscribesQuery.data || {};
  const subscribesSummary = asRecord(subscribesResult.summary);
  const subscribesItems = asRecordArray(subscribesResult.items);
  const subscribesPage = asRecord(subscribesResult.pagination);

  const unsubscribesResult = unsubscribesQuery.data || {};
  const unsubscribesSummary = asRecord(unsubscribesResult.summary);
  const unsubscribesItems = asRecordArray(unsubscribesResult.items);
  const unsubscribesPage = asRecord(unsubscribesResult.pagination);

  const subscribeKpis = [
    { title: 'Подписались', value: fmt(subscribesSummary.total) },
    { title: 'Активные подписки', value: fmt(subscribesSummary.active) },
    { title: 'Истекли', value: fmt(subscribesSummary.expired) },
  ];

  const unsubscribeKpis = [
    { title: 'Отписались', value: fmt(unsubscribesSummary.total) },
    { title: 'Через кнопку в письме', value: fmt(unsubscribesSummary.chain) },
    { title: 'Через провайдера', value: fmt(unsubscribesSummary.provider) },
  ];

  return (
    <div>
      <Input.Search
        allowClear
        placeholder="Поиск по email или компании"
        style={{ maxWidth: 360 }}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />

      <Typography.Title level={5} style={{ marginTop: 24 }}>
        Подписались
      </Typography.Title>
      <KpiGrid items={subscribeKpis} loading={subscribesQuery.isLoading} />
      <Card size="small" style={{ marginTop: 16 }}>
        <Table
          loading={subscribesQuery.isLoading}
          rowKey={(row) => `${String(row.email)}-${String(row.subscribed_at)}`}
          dataSource={subscribesItems}
          pagination={{
            current: Number(subscribesPage.page || pagination.subscribes),
            pageSize: Number(subscribesPage.per_page || 10),
            total: Number(subscribesPage.total || 0),
            showSizeChanger: false,
            onChange: (page) => setPage('subscribes', page),
          }}
          columns={[
            { title: 'Компания', dataIndex: 'organization', render: (value) => String(value || '—') },
            { title: 'Контакт', dataIndex: 'contact_name', render: (value) => String(value || '—') },
            { title: 'Email', dataIndex: 'email' },
            { title: 'Рассылка', dataIndex: 'campaign_name', render: (value) => String(value || '—') },
            { title: 'Дата', dataIndex: 'subscribed_at_label' },
            {
              title: 'Действует до',
              dataIndex: 'expires_at_label',
              render: (value, row) => (
                <span style={{ color: row.active ? undefined : '#999' }}>{String(value || '—')}</span>
              ),
            },
          ]}
        />
      </Card>

      <Typography.Title level={5} style={{ marginTop: 32 }}>
        Отписались
      </Typography.Title>
      <KpiGrid items={unsubscribeKpis} loading={unsubscribesQuery.isLoading} />
      <Card size="small" style={{ marginTop: 16 }}>
        <Table
          loading={unsubscribesQuery.isLoading}
          rowKey={(row) => `${String(row.email)}-${String(row.unsubscribed_at)}`}
          dataSource={unsubscribesItems}
          pagination={{
            current: Number(unsubscribesPage.page || pagination.unsubscribes),
            pageSize: Number(unsubscribesPage.per_page || 10),
            total: Number(unsubscribesPage.total || 0),
            showSizeChanger: false,
            onChange: (page) => setPage('unsubscribes', page),
          }}
          columns={[
            { title: 'Email', dataIndex: 'email' },
            { title: 'Компания', dataIndex: 'organization', render: (value) => String(value || '—') },
            { title: 'Источник', dataIndex: 'source_label', render: (value) => String(value || '—') },
            { title: 'Рассылка', dataIndex: 'campaign_name', render: (value) => String(value || '—') },
            { title: 'Дата', dataIndex: 'unsubscribed_at_label' },
          ]}
        />
      </Card>
    </div>
  );
}
