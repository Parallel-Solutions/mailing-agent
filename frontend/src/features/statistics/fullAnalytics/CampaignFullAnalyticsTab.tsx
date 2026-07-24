import { Alert, Card, Col, Collapse, Empty, Row, Select, Space, Table, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { statisticsApi } from '@/api/statistics';
import { AnalyticsCharts } from '../components/StatsCharts';
import { FunnelRow } from '../components/FunnelRow';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt } from '../utils';
import { FullAnalyticsDocumentsSection, FullAnalyticsEmailsSection } from './FullAnalyticsMaterialsSections';
import { MetricInfo } from './MetricInfo';
import { useCampaignJobSelector } from './useCampaignJobSelector';

function metricValue(summary: Record<string, unknown>, key: string): string {
  return fmt(summary[key]);
}

export function CampaignFullAnalyticsTab() {
  const { refreshNonce, setError } = useStatistics();
  const { jobId, options, setJobId } = useCampaignJobSelector();
  const [deliveryPage, setDeliveryPage] = useState(1);
  const [sentLogPage, setSentLogPage] = useState(1);
  const [attemptsPage, setAttemptsPage] = useState(1);
  const [documentFilter, setDocumentFilter] = useState('');
  const [selectedRecipientId, setSelectedRecipientId] = useState<number | undefined>();

  const query = useQuery({
    queryKey: [
      'stats-campaign-full-analytics',
      jobId,
      refreshNonce,
      deliveryPage,
      sentLogPage,
      attemptsPage,
      documentFilter,
    ],
    enabled: Boolean(jobId),
    queryFn: () =>
      statisticsApi.campaignFullAnalytics(jobId, {
        refresh: refreshNonce > 0 ? true : undefined,
        delivery_page: deliveryPage,
        sent_log_page: sentLogPage,
        attempts_page: attemptsPage,
        documents_q: documentFilter || undefined,
        per_page: 20,
      }),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить полную аналитику рассылки.');
  }, [query.isError, setError]);

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const rates = asRecord(result.rates);
  const campaign = asRecord(result.campaign);
  const operational = asRecord(result.operational);
  const delivery = asRecord(result.delivery);
  const domainStats = asRecord(result.domain_stats);
  const consents = asRecord(result.consents);
  const chain = asRecord(result.chain);
  const deliveryRows = asRecord(result.delivery_rows);
  const sentMailLog = asRecord(result.sent_mail_log);
  const deliveryAttempts = asRecord(result.delivery_attempts);
  const recipients = asRecordArray(result.recipients);
  const campaignId = String(result.campaign_id || '');

  useEffect(() => {
    if (recipients.length && !selectedRecipientId) {
      setSelectedRecipientId(Number(recipients[0].id));
    }
  }, [recipients, selectedRecipientId]);

  useEffect(() => {
    if (selectedRecipientId) {
      const recipient = recipients.find((row) => Number(row.id) === selectedRecipientId);
      if (recipient?.company) {
        setDocumentFilter(String(recipient.id));
      }
    }
  }, [selectedRecipientId, recipients]);

  const kpiItems = useMemo(
    () => [
      { id: 'sent', value: metricValue(summary, 'sent') },
      { id: 'delivered', value: `${metricValue(summary, 'delivered')} / ${rates.delivery_rate ?? 0}%` },
      { id: 'opened', value: `${metricValue(summary, 'opened')} / ${rates.open_rate ?? 0}%` },
      { id: 'clicked', value: `${metricValue(summary, 'clicked')} / ${rates.ctr ?? 0}%` },
      { id: 'errors', value: `${metricValue(summary, 'errors')} / ${rates.error_rate ?? 0}%` },
      { id: 'layout_errors', value: metricValue(summary, 'layout_errors') },
      { id: 'pending', value: `${metricValue(summary, 'pending')} / ${rates.pending_rate ?? 0}%` },
      { id: 'consents', value: metricValue(summary, 'consents') },
      { id: 'materials_sent', value: metricValue(summary, 'materials_sent') },
      { id: 'unsubscribed', value: metricValue(summary, 'unsubscribed') },
      { id: 'spam', value: metricValue(summary, 'spam') },
    ],
    [summary, rates],
  );

  if (!jobId) {
    return <Empty description="Выберите рассылку для полной аналитики" />;
  }

  const refreshFlags = [
    delivery.refresh_in_progress ? 'Обновление у провайдера' : null,
    delivery.awaiting_provider_events ? 'Ждём webhook-события' : null,
  ].filter(Boolean);

  return (
    <div data-testid="campaign-full-analytics-tab">
      <Row gutter={[12, 12]} align="middle" style={{ marginBottom: 16 }}>
        <Col flex="auto">
          <Select
            style={{ minWidth: 280 }}
            showSearch
            optionFilterProp="label"
            value={jobId}
            onChange={setJobId}
            options={options}
          />
          <div style={{ marginTop: 8 }}>
            <Typography.Text strong>{String(campaign.title || campaign.name || 'Рассылка')}</Typography.Text>
            <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
              {String(result.period_from || '')}
              {result.period_to ? ` — ${String(result.period_to)}` : ''}
            </Typography.Text>
          </div>
        </Col>
      </Row>

      {refreshFlags.length ? (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={refreshFlags.join(' · ')}
        />
      ) : null}

      <Collapse
        defaultActiveKey={['summary', 'delivery', 'emails', 'documents']}
        items={[
          {
            key: 'summary',
            label: 'Сводка показателей',
            children: (
              <Row gutter={[12, 12]}>
                {kpiItems.map((item) => (
                  <Col key={item.id} xs={12} sm={8} md={6} lg={4}>
                    <Card size="small" style={{ height: '100%' }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        <MetricInfo metricId={item.id} />
                      </Typography.Text>
                      <div style={{ fontSize: 18, fontWeight: 600, marginTop: 4 }}>{item.value}</div>
                    </Card>
                  </Col>
                ))}
              </Row>
            ),
          },
          {
            key: 'operational',
            label: 'Операционные данные',
            children: operational.available === false ? (
              <Empty description="Нет данных CampaignFlow для этого job" />
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Row gutter={16}>
                  <Col>
                    <MetricInfo metricId="operational_progress" label="Прогресс" />:{' '}
                    {fmt(operational.sent_count)} / {fmt(operational.total_count)} (
                    {Number(operational.progress ?? 0)}%)
                  </Col>
                  <Col>
                    Ошибки: {fmt(operational.error_count)} · КП не влезло:{' '}
                    {fmt(operational.layout_error_count)}
                  </Col>
                  <Col>
                    Транспорт: {String(operational.transport || '—')} · Статус:{' '}
                    <Tag>{String(operational.status || '—')}</Tag>
                  </Col>
                </Row>
                {operational.live_send ? (
                  <Alert
                    type="warning"
                    showIcon
                    message={
                      <>
                        <MetricInfo metricId="live_send" label="Идёт отправка" />: осталось{' '}
                        {fmt(asRecord(operational.live_send).remaining)}, в очереди батчей{' '}
                        {fmt(asRecord(operational.live_send).queued_batches)}
                      </>
                    }
                  />
                ) : null}
                <Table
                  size="small"
                  pagination={false}
                  rowKey={(row) => String(row.id)}
                  dataSource={asRecordArray(operational.batches)}
                  columns={[
                    { title: '№', dataIndex: 'batch_index', width: 60 },
                    { title: 'Запланирован', dataIndex: 'scheduled_at' },
                    { title: 'Размер', dataIndex: 'size', render: (v) => fmt(v) },
                    { title: 'Отправлено', dataIndex: 'sent_count', render: (v) => fmt(v) },
                    { title: 'Ошибки', dataIndex: 'error_count', render: (v) => fmt(v) },
                    { title: 'Статус', dataIndex: 'status' },
                  ]}
                />
              </Space>
            ),
          },
          {
            key: 'delivery',
            label: 'Доставка и вовлечённость',
            children: (
              <>
                <FunnelRow funnel={delivery.funnel} />
                <AnalyticsCharts
                  daily={delivery.daily}
                  reasons={delivery.undelivery_reasons}
                  providerEff={delivery.provider_effectiveness}
                />
                <Card size="small" title={<MetricInfo metricId="domain_stats" />} style={{ marginTop: 16 }}>
                  <Table
                    size="small"
                    pagination={false}
                    rowKey={(row) => String(row.provider)}
                    dataSource={asRecordArray(domainStats.providers)}
                    columns={[
                      { title: 'Домен', dataIndex: 'provider' },
                      { title: 'Отправлено', dataIndex: 'sent', render: (v) => fmt(v) },
                      { title: 'Доставлено', dataIndex: 'delivered', render: (v) => fmt(v) },
                      { title: 'Открыто', dataIndex: 'opened', render: (v) => fmt(v) },
                      { title: 'Bounce', dataIndex: 'bounced', render: (v) => fmt(v) },
                    ]}
                  />
                </Card>
              </>
            ),
          },
          {
            key: 'chain',
            label: 'Цепочка и согласия',
            children: (
              <Row gutter={16}>
                <Col xs={24} md={12}>
                  <Card size="small" title={<MetricInfo metricId="chain_clicks" />}>
                    <Table
                      size="small"
                      pagination={false}
                      rowKey={(row) => String(row.edge_id)}
                      dataSource={asRecordArray(chain.edges)}
                      columns={[
                        { title: 'Ветка', dataIndex: 'edge_id' },
                        { title: 'Токенов', dataIndex: 'tokens', render: (v) => fmt(v) },
                        { title: 'Кликов', dataIndex: 'clicks', render: (v) => fmt(v) },
                      ]}
                    />
                  </Card>
                </Col>
                <Col xs={24} md={12}>
                  <Card size="small" title={<MetricInfo metricId="consents" />}>
                    <Space direction="vertical">
                      <div>Всего: {fmt(consents.total)}</div>
                      <div>Подтверждено: {fmt(consents.confirmed)}</div>
                      <div>Ожидают: {fmt(consents.pending)}</div>
                      <div>Материалы отправлены: {fmt(consents.materials_sent)}</div>
                    </Space>
                  </Card>
                </Col>
              </Row>
            ),
          },
          {
            key: 'delivery_rows',
            label: 'Детальная таблица доставки',
            children: (
              <Table
                size="small"
                loading={query.isLoading}
                scroll={{ x: 1400 }}
                rowKey={(row, index) => String(row.row_key || row.email || index)}
                dataSource={asRecordArray(deliveryRows.items)}
                pagination={{
                  current: deliveryPage,
                  pageSize: 20,
                  total: Number(asRecord(deliveryRows.pagination).total || 0),
                  onChange: setDeliveryPage,
                }}
                columns={[
                  { title: 'Компания', dataIndex: 'organization', width: 160 },
                  { title: 'Email', dataIndex: 'email', width: 180 },
                  {
                    title: <MetricInfo metricId="provider" />,
                    dataIndex: 'provider',
                    width: 100,
                    render: (v) => String(v || '—'),
                  },
                  {
                    title: 'Статус',
                    render: (_, row) => String(asRecord(row.manager_status).label || '—'),
                  },
                  { title: 'Тема', dataIndex: 'subject', width: 180 },
                  {
                    title: <MetricInfo metricId="bounce_reason" />,
                    dataIndex: 'bounce_reason_label',
                    width: 140,
                  },
                  {
                    title: <MetricInfo metricId="message_id" />,
                    dataIndex: 'email_id',
                    width: 120,
                    ellipsis: true,
                  },
                  { title: 'Отправлено', dataIndex: 'sent_at', width: 140 },
                  { title: 'Проверено', dataIndex: 'checked_at', width: 140 },
                ]}
              />
            ),
          },
          {
            key: 'sent_log',
            label: 'Журнал отправок',
            children: (
              <Table
                size="small"
                loading={query.isLoading}
                scroll={{ x: 1200 }}
                rowKey={(row, index) => `${row.sent_at}-${row.recipient}-${index}`}
                dataSource={asRecordArray(sentMailLog.items)}
                pagination={{
                  current: sentLogPage,
                  pageSize: 20,
                  total: Number(asRecord(sentMailLog.pagination).total || 0),
                  onChange: setSentLogPage,
                }}
                columns={[
                  { title: 'Дата', dataIndex: 'sent_at', width: 160 },
                  { title: 'Email', dataIndex: 'recipient', width: 180 },
                  { title: 'Компания', dataIndex: 'organization' },
                  { title: 'Тема', dataIndex: 'subject' },
                  { title: 'Транспорт', dataIndex: 'transport', width: 100 },
                  { title: 'Статус', dataIndex: 'status', width: 90 },
                  { title: 'ID провайдера', dataIndex: 'provider_message_id', ellipsis: true },
                ]}
              />
            ),
          },
          {
            key: 'attempts',
            label: 'Попытки доставки',
            children: (
              <Table
                size="small"
                loading={query.isLoading}
                rowKey={(row) => String(row.id)}
                dataSource={asRecordArray(deliveryAttempts.items)}
                pagination={{
                  current: attemptsPage,
                  pageSize: 20,
                  total: Number(asRecord(deliveryAttempts.pagination).total || 0),
                  onChange: setAttemptsPage,
                }}
                columns={[
                  { title: '№', dataIndex: 'attempt_number', width: 60 },
                  { title: 'Компания', dataIndex: 'company' },
                  { title: 'Email', dataIndex: 'delivery_email' },
                  { title: 'Статус', dataIndex: 'status' },
                  { title: 'Ошибка', dataIndex: 'error', ellipsis: true },
                  { title: 'Создано', dataIndex: 'created_at', width: 160 },
                ]}
              />
            ),
          },
          {
            key: 'emails',
            label: 'Письма',
            children: (
              <FullAnalyticsEmailsSection campaignId={campaignId} recipients={recipients} />
            ),
          },
          {
            key: 'documents',
            label: 'Документы',
            children: (
              <FullAnalyticsDocumentsSection
                jobId={jobId}
                documentFilter={documentFilter}
                onDocumentFilterChange={setDocumentFilter}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
