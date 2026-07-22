import { Button, Card, Col, Empty, List, Row, Select, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { FunnelRow } from '../components/FunnelRow';
import { AnalyticsCharts } from '../components/StatsCharts';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt } from '../utils';

export function CampaignAnalyticsTab() {
  const {
    apiBaseParams,
    filters,
    setFilters,
    campaigns,
    setCampaigns,
    refreshNonce,
    openDrilldown,
    setError,
  } = useStatistics();

  const campaignsQuery = useQuery({
    queryKey: ['stats-campaigns-options', apiBaseParams],
    queryFn: () => statisticsApi.campaigns(apiBaseParams),
  });

  useEffect(() => {
    if (campaignsQuery.data) setCampaigns(asRecordArray(campaignsQuery.data.campaigns));
  }, [campaignsQuery.data, setCampaigns]);

  const jobId = filters.campaign || String(campaigns[0]?.job_id || '');

  const query = useQuery({
    queryKey: ['stats-campaign-analytics', jobId, refreshNonce],
    enabled: Boolean(jobId),
    queryFn: () =>
      statisticsApi.campaignAnalytics(jobId, {
        refresh: refreshNonce > 0 ? true : undefined,
      }),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить аналитику рассылки.');
  }, [query.isError, setError]);

  if (!jobId) {
    return <Empty description="Выберите рассылку для детальной аналитики" />;
  }

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const rates = asRecord(result.rates);
  const campaign = asRecord(result.campaign);

  const kpis = [
    { title: 'Отправлено', value: fmt(summary.sent), drill: 'sent' },
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
    { title: 'Недоставлено', value: fmt(summary.errors), drill: 'errors' },
    {
      title: 'КП не влезло',
      value: fmt(summary.layout_errors),
      drill: 'kp_layout',
    },
    {
      title: 'Отписки и спам',
      value: fmt(Number(summary.unsubscribed || 0) + Number(summary.spam || 0)),
      drill: 'unsub_spam',
    },
  ];

  return (
    <div>
      <Row gutter={[12, 12]} align="middle" style={{ marginBottom: 16 }}>
        <Col flex="auto">
          <Select
            style={{ minWidth: 280 }}
            showSearch
            optionFilterProp="label"
            value={jobId}
            onChange={(value) => setFilters({ campaign: value })}
            options={(campaigns.length ? campaigns : asRecordArray(campaignsQuery.data?.campaigns)).map(
              (item) => ({
                value: String(item.job_id),
                label: String(item.title || item.job_id),
              }),
            )}
          />
          <div style={{ marginTop: 8 }}>
            <Typography.Text strong>{String(campaign.title || 'Рассылка')}</Typography.Text>
            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
              {String(result.period_from || '')}
              {result.period_to ? ` — ${String(result.period_to)}` : ''}
            </Typography.Text>
          </div>
        </Col>
        <Col>
          <Button
            onClick={() => {
              window.location.href = statisticsApi.autoCallContactsUrl(jobId);
            }}
          >
            Выгрузить для обзвона
          </Button>
        </Col>
      </Row>

      <KpiGrid items={kpis} loading={query.isLoading} onDrill={(key) => void openDrilldown(key)} />
      <FunnelRow funnel={result.funnel} />
      <AnalyticsCharts
        daily={result.daily}
        reasons={result.undelivery_reasons}
        providerEff={result.provider_effectiveness}
      />

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="Высокий интерес" size="small">
            <Table
              size="small"
              pagination={false}
              rowKey={(row) => String(row.organization)}
              dataSource={asRecordArray(result.high_interest_companies)}
              locale={{ emptyText: 'Нет компаний с высоким интересом' }}
              onRow={(row) => ({
                onClick: () =>
                  void openDrilldown('sent', {
                    title: String(row.organization),
                    params: { organization: String(row.organization) },
                  }),
                style: { cursor: 'pointer' },
              })}
              columns={[
                { title: 'Компания', dataIndex: 'organization' },
                { title: 'Отправлено', dataIndex: 'sent', render: (v) => fmt(v) },
                { title: 'Open %', dataIndex: 'open_rate', render: (v) => `${v}%` },
                { title: 'Клики', dataIndex: 'clicked', render: (v) => fmt(v) },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="Проблемные адреса" size="small">
            <Table
              size="small"
              pagination={false}
              rowKey={(row, index) => String(row.email || row.organization || index)}
              dataSource={asRecordArray(result.problem_addresses)}
              locale={{ emptyText: 'Нет проблемных адресов' }}
              onRow={(row) => ({
                onClick: () => {
                  if (row.organization) {
                    void openDrilldown('problems', {
                      title: String(row.organization),
                      params: { organization: String(row.organization) },
                    });
                  }
                },
                style: { cursor: 'pointer' },
              })}
              columns={[
                {
                  title: 'Компания / email',
                  render: (_, r) => String(r.organization || r.email || '—'),
                },
                { title: 'Причина', dataIndex: 'reason_label' },
                { title: 'Провайдер', dataIndex: 'provider_label' },
                { title: 'Писем', dataIndex: 'attempts', render: (v) => fmt(v) },
              ]}
            />
          </Card>
        </Col>
      </Row>

      <Card title="Рекомендации" size="small" style={{ marginTop: 16 }}>
        <List
          dataSource={(result.recommendations as string[] | undefined) || []}
          locale={{ emptyText: 'Нет рекомендаций' }}
          renderItem={(item) => <List.Item>{item}</List.Item>}
        />
      </Card>
    </div>
  );
}
