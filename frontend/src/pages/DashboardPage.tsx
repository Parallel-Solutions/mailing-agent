import { ProCard, StatisticCard } from '@ant-design/pro-components';
import { Col, Row, Table, Tabs, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { ActiveSendingCard } from '@/components/ActiveSendingCard';
import { statisticsApi } from '@/api/statistics';

export function DashboardPage() {
  const dashboardQuery = useQuery({
    queryKey: ['manager-dashboard'],
    queryFn: () => statisticsApi.managerDashboard(),
  });
  const campaignsQuery = useQuery({
    queryKey: ['stats-campaigns'],
    queryFn: () => statisticsApi.campaigns(),
  });
  const consentsQuery = useQuery({
    queryKey: ['stats-consents'],
    queryFn: () => statisticsApi.consents(),
  });
  const problemsQuery = useQuery({
    queryKey: ['stats-problems'],
    queryFn: () => statisticsApi.problems(),
  });

  const dash = dashboardQuery.data || {};
  const kpis = (dash.kpis || dash.summary || {}) as Record<string, number>;
  const campaigns = ((campaignsQuery.data as { items?: unknown[] })?.items ||
    (campaignsQuery.data as { campaigns?: unknown[] })?.campaigns ||
    []) as Record<string, unknown>[];

  return (
    <div>
      <Typography.Title level={3}>Дашборд</Typography.Title>
      <Typography.Paragraph type="secondary">
        Менеджерская статистика отправок и активная очередь
      </Typography.Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <StatisticCard.Group direction="row" loading={dashboardQuery.isLoading}>
            <StatisticCard statistic={{ title: 'Отправлено', value: kpis.sent ?? kpis.total_sent ?? 0 }} />
            <StatisticCard
              statistic={{ title: 'Доставлено %', value: kpis.delivered_rate ?? kpis.delivery_rate ?? 0 }}
            />
            <StatisticCard statistic={{ title: 'Ошибки', value: kpis.errors ?? kpis.failed ?? 0 }} />
            <StatisticCard statistic={{ title: 'Открытия %', value: kpis.open_rate ?? 0 }} />
          </StatisticCard.Group>
        </Col>
        <Col xs={24} lg={8}>
          <ActiveSendingCard />
        </Col>
      </Row>

      <ProCard style={{ marginTop: 16 }} bordered>
        <Tabs
          items={[
            {
              key: 'campaigns',
              label: 'Рассылки',
              children: (
                <Table
                  rowKey={(row) => String(row.job_id || row.id || row.campaign_name)}
                  loading={campaignsQuery.isLoading}
                  dataSource={campaigns}
                  pagination={{ pageSize: 10 }}
                  columns={[
                    {
                      title: 'Название',
                      dataIndex: 'campaign_name',
                      render: (_, r) => String(r.campaign_name || r.name || ''),
                    },
                    { title: 'Статус', dataIndex: 'status' },
                    {
                      title: 'Отправлено',
                      dataIndex: 'sent',
                      render: (_, r) => String(r.sent ?? r.sent_count ?? 0),
                    },
                    { title: 'Провайдер', dataIndex: 'transport' },
                  ]}
                />
              ),
            },
            {
              key: 'consents',
              label: 'Согласия',
              children: (
                <Table
                  rowKey={(_, index) => String(index)}
                  loading={consentsQuery.isLoading}
                  dataSource={
                    ((consentsQuery.data as { items?: unknown[] })?.items ||
                      (consentsQuery.data as { consents?: unknown[] })?.consents ||
                      []) as Record<string, unknown>[]
                  }
                  columns={[
                    { title: 'Email', dataIndex: 'email' },
                    { title: 'Статус', dataIndex: 'status' },
                    { title: 'Организация', dataIndex: 'organization' },
                  ]}
                />
              ),
            },
            {
              key: 'problems',
              label: 'Проблемы с email',
              children: (
                <Table
                  rowKey={(_, index) => String(index)}
                  loading={problemsQuery.isLoading}
                  dataSource={
                    ((problemsQuery.data as { items?: unknown[] })?.items ||
                      (problemsQuery.data as { problems?: unknown[] })?.problems ||
                      []) as Record<string, unknown>[]
                  }
                  columns={[
                    { title: 'Email', dataIndex: 'email' },
                    { title: 'Причина', dataIndex: 'reason' },
                    { title: 'Источник', dataIndex: 'source' },
                  ]}
                />
              ),
            },
          ]}
        />
      </ProCard>
    </div>
  );
}
