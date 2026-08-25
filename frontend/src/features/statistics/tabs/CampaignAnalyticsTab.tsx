import { Alert, Button, Card, Col, Divider, Empty, List, Modal, Row, Select, Space, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { FunnelRow } from '../components/FunnelRow';
import { AnalyticsCharts } from '../components/StatsCharts';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt, fmtMetric } from '../utils';
import { formatLocalDateTime } from '@/utils/dateTime';

export function CampaignAnalyticsTab() {
  const {
    filters,
    refreshNonce,
    refreshProviders,
    openDrilldown,
    setError,
  } = useStatistics();
  const selectedCampaignIds = (filters.campaign || '').split(',').filter(Boolean);
  // Chain/link analytics is inherently a per-campaign view, so with several
  // campaigns selected in the top filter we show the detail for the first
  // one and surface a hint rather than silently ignoring the rest.
  const jobId = selectedCampaignIds[0] || '';

  const query = useQuery({
    queryKey: [
      'stats-campaign-analytics',
      jobId,
      filters.period_from,
      filters.period_to,
      refreshNonce,
    ],
    enabled: Boolean(jobId),
    queryFn: () =>
      statisticsApi.campaignAnalytics(jobId, {
        period_from: filters.period_from || undefined,
        period_to: filters.period_to || undefined,
        refresh: refreshProviders ? true : undefined,
      }),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить аналитику рассылки.');
  }, [query.isError, setError]);

  const result = query.data || {};
  const campaign = asRecord(result.campaign);
  const linkAnalytics = asRecord(result.link_analytics);
  const linkSteps = asRecordArray(linkAnalytics.steps);
  const [selectedStepId, setSelectedStepId] = useState('');
  const [selectedLink, setSelectedLink] = useState<Record<string, unknown> | null>(null);
  const isChain = linkAnalytics.mode === 'chain';
  const selectedChainStep =
    isChain && selectedStepId
      ? linkSteps.find(
          (step) => String(step.node_id || step.id || '') === selectedStepId,
        )
      : undefined;
  const selectedStep = (isChain ? selectedChainStep : linkSteps[0]) || {};
  const isAllChainSteps = isChain && !selectedChainStep;
  const activeStepId = String(selectedStep.node_id || selectedStep.id || '');
  const stepAnalytics = asRecord(selectedStep.analytics);
  const analyticsView = Object.keys(stepAnalytics).length ? stepAnalytics : result;
  const activeLinks = asRecordArray(selectedStep.links);
  const activeDocuments = asRecordArray(selectedStep.documents);

  useEffect(() => {
    setSelectedLink(null);
    setSelectedStepId('');
  }, [jobId]);

  useEffect(() => {
    setSelectedLink(null);
  }, [activeStepId]);

  const activeLinkEntries = activeLinks.map((link, linkIndex) => ({
    link,
    key: String(link.id || link.edge_id || link.url || linkIndex),
    label: String(link.label || link.url || `Ссылка ${linkIndex + 1}`),
    clickers: Number(link.unique_clickers || 0),
  }));
  const activeDocumentEntries = activeDocuments.map((document, documentIndex) => ({
    document,
    key: String(document.id || document.template_id || documentIndex),
    label: String(document.label || `Документ ${documentIndex + 1}`),
    openers: Number(document.unique_openers || 0),
  }));

  const resourceGroups = linkSteps.map((step, stepIndex) => {
    const groupAnalytics = asRecord(step.analytics);
    return {
      step,
      key: String(step.node_id || step.id || stepIndex),
      name: String(step.name || `Письмо ${stepIndex + 1}`),
      analytics: groupAnalytics,
      linkEntries: asRecordArray(step.links).map((link, linkIndex) => ({
        link,
        key: String(link.id || link.edge_id || link.url || linkIndex),
        label: String(link.label || link.url || `Ссылка ${linkIndex + 1}`),
        clickers: Number(link.unique_clickers || 0),
      })),
      documentEntries: asRecordArray(step.documents).map((document, documentIndex) => ({
        document,
        key: String(document.id || document.template_id || documentIndex),
        label: String(document.label || `Документ ${documentIndex + 1}`),
        openers: Number(document.unique_openers || 0),
      })),
    };
  });

  const buildResourceKpis = (
    linkEntries: typeof activeLinkEntries,
    documentEntries: typeof activeDocumentEntries,
    denominator: number,
    testPrefix = 'campaign-analytics',
  ) => [
    ...linkEntries.map((entry) => ({
      title: entry.label,
      value: `${fmtMetric(entry.clickers)} / ${
        denominator > 0
          ? Math.round((entry.clickers / denominator) * 1000) / 10
          : 0
      }%`,
      drill: `link:${entry.key}`,
      metricId:
        String(entry.link.kind || entry.link.link_kind || '').toLowerCase() === 'unsubscribe'
          ? 'chain_unsubscribe'
          : String(entry.link.kind || entry.link.link_kind || '').toLowerCase() === 'subscribe'
            ? 'chain_subscribe'
            : 'tracked_link',
      testId: `${testPrefix}-link-card-${entry.key}`,
    })),
    ...documentEntries.map((entry) => ({
      title: `Документ: ${entry.label}`,
      value: `${fmtMetric(entry.openers)} / ${
        denominator > 0
          ? Math.round((entry.openers / denominator) * 1000) / 10
          : 0
      }%`,
      drill: `document:${entry.key}`,
      metricId: 'tracked_document',
      testId: `${testPrefix}-document-card-${entry.key}`,
    })),
  ];

  const buildKpis = (
    analytics: Record<string, unknown>,
    linkEntries: typeof activeLinkEntries,
    documentEntries: typeof activeDocumentEntries,
    testPrefix = 'campaign-analytics',
  ) => {
    const kpiSummary = asRecord(analytics.summary);
    const kpiRates = asRecord(analytics.rates);
    const kpiTotalAttempts = Number(kpiSummary.total_attempts || 0);
    const kpiSentToProvider = Number(kpiSummary.sent || 0);
    return [
      {
        title: 'Всего',
        value: fmtMetric(kpiTotalAttempts),
        drill: 'all_attempts',
        metricId: 'all_attempts',
        testId: `${testPrefix}-total-card`,
      },
      {
        title: 'Не дошло до отправки',
        value: fmtMetric(
          kpiSummary.not_sent ??
            Math.max(0, kpiTotalAttempts - kpiSentToProvider),
        ),
        drill: 'not_sent',
        testId: `${testPrefix}-not-sent-card`,
      },
      {
        title: 'Отправлено в почтовый провайдер',
        value: fmtMetric(kpiSentToProvider),
        drill: 'sent',
        testId: `${testPrefix}-sent-card`,
      },
      {
        title: 'Ошибки почтового провайдера',
        value: fmtMetric(kpiSummary.provider_errors ?? kpiSummary.errors),
        drill: 'errors',
        testId: `${testPrefix}-provider-errors-card`,
      },
      {
        title: 'Доставлено реальное письмо',
        value: `${fmtMetric(kpiSummary.delivered)} / ${kpiRates.delivery_rate ?? 0}%`,
        drill: 'delivered',
        testId: `${testPrefix}-delivered-card`,
      },
      {
        title: 'Открыто',
        value: `${fmtMetric(kpiSummary.opened)} / ${kpiRates.open_rate ?? 0}%`,
        drill: 'opened',
        testId: `${testPrefix}-opened-card`,
      },
      ...buildResourceKpis(
        linkEntries,
        documentEntries,
        kpiSentToProvider,
        testPrefix,
      ),
      {
        title: 'Отписались у почтового провайдера',
        value: fmtMetric(kpiSummary.unsubscribed),
        drill: 'unsubscribed',
        testId: `${testPrefix}-unsubscribed-card`,
      },
      {
        title: 'Добавили в спам',
        value: fmtMetric(kpiSummary.spam),
        drill: 'spam',
        testId: `${testPrefix}-spam-card`,
      },
    ];
  };

  const kpis = buildKpis(
    analyticsView,
    activeLinkEntries,
    activeDocumentEntries,
  );

  return (
    <div>
      {selectedCampaignIds.length > 1 ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="Выбрано несколько рассылок. Детальная аналитика по письмам и ссылкам показана для первой из них — для сводных показателей по всем выбранным рассылкам используйте вкладку «Показатели рассылок»."
        />
      ) : null}
      <Row gutter={[12, 12]} align="middle" style={{ marginBottom: 16 }}>
        <Col flex="auto">
          {jobId ? (
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                flexWrap: 'wrap',
              }}
            >
              <Space size={12} wrap>
                <Typography.Text strong>{String(campaign.title || 'Рассылка')}</Typography.Text>
                <Typography.Text type="secondary">
                  {String(result.period_from || '')}
                  {result.period_to ? ` — ${String(result.period_to)}` : ''}
                </Typography.Text>
              </Space>
              {isChain && linkSteps.length ? (
                <Select
                  data-testid="campaign-chain-step-selector"
                  style={{ minWidth: 140 }}
                  allowClear
                  placeholder="Все письма"
                  value={activeStepId || undefined}
                  onChange={(value) => setSelectedStepId(value || '')}
                  options={linkSteps.map((step, stepIndex) => ({
                    value: String(step.node_id || step.id || stepIndex),
                    label: String(step.name || `Письмо ${stepIndex + 1}`),
                  }))}
                />
              ) : null}
            </div>
          ) : null}
        </Col>
        {jobId ? <Col>
          <Button
            onClick={() => {
              window.location.href = statisticsApi.autoCallContactsUrl(jobId);
            }}
          >
            Выгрузить для обзвона
          </Button>
        </Col> : null}
      </Row>

      {!jobId ? (
        <Empty description="Выберите рассылку для детальной аналитики" />
      ) : (
        <>
          {!isAllChainSteps ? (
            <KpiGrid
              items={kpis}
              loading={query.isLoading}
              onDrill={(key) => {
                if (key.startsWith('link:')) {
                  const linkKey = key.slice('link:'.length);
                  const entry = activeLinkEntries.find((item) => item.key === linkKey);
                  if (entry) {
                    setSelectedLink({
                      ...entry.link,
                      step_name: String(selectedStep.name || 'Письмо'),
                      resource_type: 'link',
                    });
                  }
                  return;
                }
                if (key.startsWith('document:')) {
                  const documentKey = key.slice('document:'.length);
                  const entry = activeDocumentEntries.find(
                    (item) => item.key === documentKey,
                  );
                  if (entry) {
                    setSelectedLink({
                      ...entry.document,
                      label: entry.label,
                      step_name: String(selectedStep.name || 'Письмо'),
                      resource_type: 'document',
                    });
                  }
                  return;
                }
                void openDrilldown(
                  key,
                  { params: { campaign: jobId } },
                );
              }}
            />
          ) : (
            <div
              data-testid="campaign-all-step-resource-groups"
            >
              {resourceGroups.map((group) => {
                const groupKpis = buildKpis(
                  group.analytics,
                  group.linkEntries,
                  group.documentEntries,
                  `campaign-analytics-step-${group.key}`,
                );
                return (
                  <div
                    key={group.key}
                    data-testid={`campaign-step-resource-group-${group.key}`}
                  >
                    <Divider orientation="left" plain>
                      {group.name}
                    </Divider>
                    {groupKpis.length ? (
                      <KpiGrid
                        items={groupKpis}
                        loading={query.isLoading}
                        onDrill={(key) => {
                          if (key.startsWith('link:')) {
                            const linkKey = key.slice('link:'.length);
                            const entry = group.linkEntries.find(
                              (item) => item.key === linkKey,
                            );
                            if (entry) {
                              setSelectedLink({
                                ...entry.link,
                                step_name: group.name,
                                resource_type: 'link',
                              });
                            }
                            return;
                          }
                          if (key.startsWith('document:')) {
                            const documentKey = key.slice('document:'.length);
                            const entry = group.documentEntries.find(
                              (item) => item.key === documentKey,
                            );
                            if (entry) {
                              setSelectedLink({
                                ...entry.document,
                                label: entry.label,
                                step_name: group.name,
                                resource_type: 'document',
                              });
                            }
                            return;
                          }
                          void openDrilldown(
                            key,
                            { params: { campaign: jobId } },
                          );
                        }}
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
          <FunnelRow
            funnel={asRecordArray(analyticsView.funnel).filter(
              (step) => String(step.id || '') !== 'clicked',
            )}
          />
          <AnalyticsCharts
            daily={analyticsView.daily}
            reasons={analyticsView.undelivery_reasons}
            providerEff={analyticsView.provider_effectiveness}
          />

          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} lg={12}>
              <Card title="Высокий интерес" size="small">
                <Table
                  size="small"
                  pagination={false}
                  rowKey={(row) => String(row.organization)}
                  dataSource={asRecordArray(analyticsView.high_interest_companies)}
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
                    { title: 'Принято провайдером', dataIndex: 'sent', render: (v) => fmt(v) },
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
                  dataSource={asRecordArray(analyticsView.problem_addresses)}
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
              dataSource={(analyticsView.recommendations as string[] | undefined) || []}
              locale={{ emptyText: 'Нет рекомендаций' }}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />
          </Card>
        </>
      )}
      <Modal
        open={Boolean(selectedLink)}
        onCancel={() => setSelectedLink(null)}
        footer={null}
        width={1180}
        title={
          selectedLink
            ? `${String(selectedLink.step_name || 'Письмо')}: ${String(selectedLink.label || selectedLink.url || 'Ссылка')}`
            : 'Переходы по ссылке'
        }
      >
        {selectedLink?.url ? (
          <Typography.Paragraph type="secondary" copyable style={{ marginBottom: 12 }}>
            {String(selectedLink.url)}
          </Typography.Paragraph>
        ) : null}
        <Table
          size="small"
          rowKey={(row, index) =>
            String(row.recipient_id || row.email || row.row_id || `${row.clicked_at || ''}-${index}`)
          }
          dataSource={asRecordArray(selectedLink?.clickers)}
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          locale={{
            emptyText:
              selectedLink?.resource_type === 'document'
                ? 'Этот документ пока никто не открывал'
                : 'По этой ссылке пока никто не переходил',
          }}
          columns={[
            {
              title:
                selectedLink?.resource_type === 'document'
                  ? 'Кто открыл'
                  : 'Кто нажал',
              render: (_, row) =>
                String(row.company || row.contact_name || row.email || 'Получатель'),
            },
            { title: 'Email', dataIndex: 'email', render: (value) => String(value || '—') },
            {
              title:
                selectedLink?.resource_type === 'document'
                  ? 'Дата открытия'
                  : 'Дата перехода',
              dataIndex: 'clicked_at',
              render: (value) => formatLocalDateTime(String(value || '')),
            },
            {
              title: 'IP',
              dataIndex: 'clicked_ip',
              width: 150,
              render: (value) => String(value || '—'),
            },
            {
              title: 'Метод',
              dataIndex: 'clicked_http_method',
              width: 85,
              render: (value) => String(value || '—'),
            },
            {
              title: 'User-Agent',
              dataIndex: 'clicked_user_agent',
              ellipsis: true,
              render: (value) => {
                const text = String(value || '—');
                return <Typography.Text title={text}>{text}</Typography.Text>;
              },
            },
          ]}
        />
      </Modal>
    </div>
  );
}
