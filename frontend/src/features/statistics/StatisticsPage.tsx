import { Alert, Button, DatePicker, Select, Space, Tabs, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import dayjs from 'dayjs';
import { statisticsApi } from '@/api/statistics';
import { AUTO_REFRESH_MS, PAGE_TITLES, PROVIDER_OPTIONS, STATS_TABS } from './constants';
import { StatisticsProvider, useStatistics } from './StatisticsContext';
import { StatisticsModals } from './modals/StatisticsModals';
import { DashboardTab } from './tabs/DashboardTab';
import { CampaignsTab } from './tabs/CampaignsTab';
import { RecipientsTab } from './tabs/RecipientsTab';
import { CampaignAnalyticsTab } from './tabs/CampaignAnalyticsTab';
import { ConsentsTab } from './tabs/ConsentsTab';
import { ProblemsTab } from './tabs/ProblemsTab';
import { ReportsTab } from './tabs/ReportsTab';
import { asRecordArray } from './utils';

export function StatisticsPage() {
  return (
    <StatisticsProvider>
      <StatisticsPageInner />
    </StatisticsProvider>
  );
}

function StatisticsPageInner() {
  const {
    tab,
    setTab,
    filters,
    setFilters,
    campaigns,
    setCampaigns,
    apiBaseParams,
    requestRefresh,
    refreshNonce,
    error,
    setError,
    openFiltersModal,
    openExportModal,
  } = useStatistics();

  const campaignsQuery = useQuery({
    queryKey: ['stats-campaigns-shell', apiBaseParams],
    queryFn: () => statisticsApi.campaigns(apiBaseParams),
  });

  useEffect(() => {
    if (campaignsQuery.data) setCampaigns(asRecordArray(campaignsQuery.data.campaigns));
  }, [campaignsQuery.data, setCampaigns]);

  useEffect(() => {
    const timer = window.setInterval(() => requestRefresh(), AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [requestRefresh]);

  const metaQuery = useQuery({
    queryKey: ['stats-dashboard-meta', apiBaseParams, refreshNonce],
    queryFn: () => statisticsApi.managerDashboard(apiBaseParams),
    enabled: tab === 'dashboard',
  });

  const generatedAt =
    tab === 'dashboard'
      ? String(metaQuery.data?.generated_at_label || metaQuery.data?.generated_at || '—')
      : '—';

  return (
    <div data-testid="statistics-page">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          gap: 16,
          flexWrap: 'wrap',
          marginBottom: 16,
        }}
      >
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>
            {PAGE_TITLES[tab]}
          </Typography.Title>
          <Typography.Text type="secondary">Обновлено: {generatedAt}</Typography.Text>
        </div>
        <Space wrap>
          <DatePicker.RangePicker
            value={[
              filters.period_from ? dayjs(filters.period_from) : null,
              filters.period_to ? dayjs(filters.period_to) : null,
            ]}
            onChange={(value) =>
              setFilters(
                {
                  period_from: value?.[0]?.format('YYYY-MM-DD'),
                  period_to: value?.[1]?.format('YYYY-MM-DD'),
                },
                { resetPages: true },
              )
            }
          />
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="Все рассылки"
            style={{ minWidth: 200 }}
            value={filters.campaign}
            onChange={(value) => setFilters({ campaign: value || undefined }, { resetPages: true })}
            options={campaigns.map((item) => ({
              value: String(item.job_id),
              label: String(item.title || item.job_id),
            }))}
          />
          <Select
            allowClear
            placeholder="Все провайдеры"
            style={{ minWidth: 160 }}
            value={filters.provider}
            onChange={(value) =>
              setFilters(
                { provider: value || undefined, providers: undefined },
                { resetPages: true },
              )
            }
            options={PROVIDER_OPTIONS.filter((item) => item.value)}
          />
          {tab !== 'reports' ? (
            <Button onClick={openFiltersModal}>Расширенные фильтры</Button>
          ) : (
            <Button onClick={() => openExportModal()}>Экспорт отчёта</Button>
          )}
          <Button type="primary" onClick={() => requestRefresh()}>
            Обновить
          </Button>
        </Space>
      </div>

      {error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={error}
          action={
            <Button
              size="small"
              onClick={() => {
                setError(null);
                requestRefresh();
              }}
            >
              Повторить
            </Button>
          }
          closable
          onClose={() => setError(null)}
        />
      ) : null}

      <Tabs
        activeKey={tab}
        onChange={(key) => setTab(key as typeof tab)}
        items={STATS_TABS.map((item) => ({
          key: item.key,
          label: item.label,
          children: <TabBody tabKey={item.key} />,
        }))}
      />

      <StatisticsModals />
    </div>
  );
}

function TabBody({ tabKey }: { tabKey: string }) {
  switch (tabKey) {
    case 'dashboard':
      return <DashboardTab />;
    case 'campaigns':
      return <CampaignsTab />;
    case 'recipients':
      return <RecipientsTab />;
    case 'campaign-analytics':
      return <CampaignAnalyticsTab />;
    case 'consents':
      return <ConsentsTab />;
    case 'problems':
      return <ProblemsTab />;
    case 'reports':
      return <ReportsTab />;
    default:
      return null;
  }
}
