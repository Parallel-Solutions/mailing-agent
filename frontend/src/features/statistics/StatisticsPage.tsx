import { Alert, Button, DatePicker, Select, Space, Tabs, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import dayjs from 'dayjs';
import { statisticsApi } from '@/api/statistics';
import { AUTO_REFRESH_MS, MANAGEMENT_TAB_KEYS, PAGE_TITLES, PROVIDER_OPTIONS, STATS_TABS } from './constants';
import { StatisticsProvider, useStatistics } from './StatisticsContext';
import { StatisticsModals } from './modals/StatisticsModals';
import { DashboardTab } from './tabs/DashboardTab';
import { CampaignsTab } from './tabs/CampaignsTab';
import { RecipientsTab } from './tabs/RecipientsTab';
import { CampaignAnalyticsTab } from './tabs/CampaignAnalyticsTab';
import { CampaignFullAnalyticsTab } from './fullAnalytics/CampaignFullAnalyticsTab';
import { MarketingConsentsTab } from './tabs/MarketingConsentsTab';
import { ExternalSpendTab } from './tabs/ExternalSpendTab';
import { CampaignsListPage } from '@/pages/CampaignsListPage';
import { AudiencesPage } from '@/pages/AudiencesPage';
import { asRecordArray } from './utils';
import { formatLocalDateTime } from '@/utils/dateTime';
import { managerDashboardParams, managerDashboardQueryKey } from './dashboardQuery';
import { usePermissions } from '@/hooks/usePermissions';

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
    refreshProviders,
    error,
    setError,
    openFiltersModal,
  } = useStatistics();
  const { isAppAdmin } = usePermissions();

  const campaignsQuery = useQuery({
    queryKey: ['stats-campaigns-shell', refreshNonce],
    queryFn: () => statisticsApi.campaigns(),
  });

  useEffect(() => {
    if (campaignsQuery.data) setCampaigns(asRecordArray(campaignsQuery.data.campaigns));
  }, [campaignsQuery.data, setCampaigns]);

  useEffect(() => {
    const timer = window.setInterval(() => requestRefresh(), AUTO_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [requestRefresh]);

  const metaQuery = useQuery({
    queryKey: managerDashboardQueryKey(apiBaseParams, refreshNonce),
    queryFn: () =>
      statisticsApi.managerDashboard(
        managerDashboardParams(apiBaseParams, refreshProviders),
      ),
    enabled: tab === 'dashboard',
  });

  const isManagementTab = MANAGEMENT_TAB_KEYS.includes(tab as (typeof MANAGEMENT_TAB_KEYS)[number]);

  const generatedAt =
    tab === 'dashboard'
      ? formatLocalDateTime(String(metaQuery.data?.generated_at || ''))
      : '—';

  return (
    <div data-testid="statistics-page" data-onboarding-id="statistics-overview">
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
          {tab === 'dashboard' ? (
            <Typography.Text type="secondary">Обновлено: {generatedAt}</Typography.Text>
          ) : null}
        </div>
        {!isManagementTab ? (
          <Space wrap data-onboarding-id="statistics-filters">
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
              data-testid="statistics-campaign-filter"
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="Все рассылки"
              style={{ minWidth: 200 }}
              value={filters.campaign || undefined}
              onChange={(value) => setFilters({ campaign: value || undefined }, { resetPages: true })}
              options={campaigns.map((item) => ({
                value: String(item.job_id),
                label: String(item.title || 'Рассылка без названия'),
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
            <Button onClick={openFiltersModal}>Расширенные фильтры</Button>
            <Button type="primary" onClick={() => requestRefresh({ provider: true })}>
              Обновить
            </Button>
          </Space>
        ) : null}
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
        items={STATS_TABS.filter((item) => item.key !== 'external-spend' || isAppAdmin).map((item) => ({
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
  let body: React.ReactNode = null;

  switch (tabKey) {
    case 'dashboard':
      body = <DashboardTab />;
      break;
    case 'campaign-list':
      body = <CampaignsListPage embedded scope="launched" />;
      break;
    case 'draft-list':
      body = <CampaignsListPage embedded scope="draft" />;
      break;
    case 'audiences':
      body = <AudiencesPage embedded />;
      break;
    case 'campaigns':
      body = <CampaignsTab />;
      break;
    case 'recipients':
      body = <RecipientsTab />;
      break;
    case 'campaign-analytics':
      body = <CampaignAnalyticsTab />;
      break;
    case 'campaign-full-analytics':
      body = <CampaignFullAnalyticsTab />;
      break;
    case 'marketing-consents':
      body = <MarketingConsentsTab />;
      break;
    case 'external-spend':
      body = <ExternalSpendTab />;
      break;
    default:
      body = null;
  }

  return (
    <div data-onboarding-id={`statistics-body-${tabKey}`}>
      {body}
    </div>
  );
}
