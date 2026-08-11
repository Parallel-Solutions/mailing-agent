import { Button, Card, Col, Empty, List, Row, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { FunnelRow } from '../components/FunnelRow';
import { DashboardCharts } from '../components/StatsCharts';
import { useStatistics } from '../StatisticsContext';
import { readDashboardCache, writeDashboardCache } from '../hooks/useStatisticsState';
import { asRecord, asRecordArray, fmt } from '../utils';
import { managerDashboardParams, managerDashboardQueryKey } from '../dashboardQuery';

export function DashboardTab() {
  const {
    apiBaseParams,
    filters,
    refreshNonce,
    refreshProviders,
    openDrilldown,
    setTab,
    setError,
  } = useStatistics();

  const cached = readDashboardCache(filters);

  const query = useQuery({
    queryKey: managerDashboardQueryKey(apiBaseParams, refreshNonce),
    queryFn: () =>
      statisticsApi.managerDashboard(
        managerDashboardParams(apiBaseParams, refreshProviders),
      ),
    placeholderData: cached || undefined,
  });

  useEffect(() => {
    if (query.data) writeDashboardCache(filters, query.data);
    if (query.isError) setError('Не удалось загрузить данные.');
  }, [query.data, query.isError, filters, setError]);

  const result = query.data || cached || {};
  const summary = asRecord(result.summary);
  const rates = asRecord(result.rates);
  const worklists = asRecord(result.work_lists);
  const insights = asRecordArray(result.insights);

  const kpis = [
    { title: 'Принято провайдером', value: fmt(summary.sent), drill: 'sent' },
    {
      title: 'Доставлено',
      value: `${fmt(summary.delivered)} / ${rates.delivery_rate ?? 0}%`,
      drill: 'delivered',
    },
    {
      title: 'Открыто',
      value: `${fmt(summary.opened)} / ${rates.open_rate ?? 0}%`,
      drill: 'opened',
    },
    {
      title: 'Переходы',
      value: `${fmt(summary.clicked)} / ${rates.ctr ?? 0}%`,
      drill: 'clicked',
    },
    {
      title: 'Ошибки',
      value: `${fmt(summary.errors)} / ${rates.error_rate ?? 0}%`,
      drill: 'problems',
    },
    { title: 'Ожидают статуса', value: fmt(summary.pending), drill: 'pending' },
    { title: 'Согласия', value: fmt(summary.consents), drill: 'consents' },
    { title: 'Материалы отправлены', value: fmt(summary.materials_sent), drill: 'materials' },
  ];

  return (
    <div>
      <KpiGrid items={kpis} loading={query.isLoading && !cached} onDrill={(key) => void openDrilldown(key)} />
      <FunnelRow funnel={result.funnels} />
      <DashboardCharts statuses={result.statuses} providers={result.providers} roles={result.roles} />

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12}>
          <Card
            title="Заинтересованные"
            size="small"
            extra={
              <Button
                size="small"
                onClick={() => setTab('recipients', { quick_filter: 'opened' })}
              >
                Посмотреть все
              </Button>
            }
          >
            <Worklist items={asRecordArray(worklists.interested)} />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card
            title="Проблемы с email"
            size="small"
            extra={
              <Button
                size="small"
                onClick={() => setTab('recipients', { quick_filter: 'problems' })}
              >
                Посмотреть все
              </Button>
            }
          >
            <Worklist items={asRecordArray(worklists.email_problems)} />
          </Card>
        </Col>
      </Row>

      <Card title="Выводы" size="small" style={{ marginTop: 16 }}>
        {insights.length ? (
          <List
            dataSource={insights}
            renderItem={(item) => (
              <List.Item>
                <Typography.Text>
                  <strong>{String(item.title)}:</strong> {String(item.text)}
                </Typography.Text>
              </List.Item>
            )}
          />
        ) : (
          <Empty description="Нет выводов" />
        )}
      </Card>

      {result.empty ? (
        <Empty style={{ marginTop: 24 }} description="Нет данных за выбранный период" />
      ) : null}
    </div>
  );
}

function Worklist({ items }: { items: Record<string, unknown>[] }) {
  if (!items.length) return <Empty description="Нет данных" />;
  return (
    <List
      size="small"
      dataSource={items}
      renderItem={(item) => (
        <List.Item>
          <span>{String(item.organization || '—')}</span>
          <strong>{fmt(item.count)}</strong>
        </List.Item>
      )}
    />
  );
}
