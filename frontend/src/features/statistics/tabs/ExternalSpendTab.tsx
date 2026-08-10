import { Alert, Card, Col, Empty, Row, Table, Tag, Typography } from 'antd';
import { Column, Line } from '@ant-design/charts';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt } from '../utils';
import { formatLocalDateTime } from '@/utils/dateTime';

const SPEND_PERIOD_MINUTES = 24 * 60;
const LIVE_EVENTS_LIMIT = 200;

type SpendEvent = {
  service?: string;
  operation?: string;
  model?: string | null;
  request_count?: number;
  cost_usd?: number;
  job_id?: string | null;
  owner_username?: string | null;
  status?: string;
  created_at?: string;
};

function formatUsd(value: unknown): string {
  const n = Number(value || 0);
  if (!n) return '$0.00';
  return `$${n.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}

export function ExternalSpendTab() {
  const { refreshNonce, setError } = useStatistics();
  const [liveEvents, setLiveEvents] = useState<SpendEvent[]>([]);

  const query = useQuery({
    queryKey: ['stats-external-spend', refreshNonce],
    queryFn: () => statisticsApi.externalSpendSnapshot(SPEND_PERIOD_MINUTES),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить расходы на внешние сервисы.');
  }, [query.isError, setError]);

  // Snapshot refetch is the source of truth — drop live-only deltas each time
  // a fresh snapshot arrives so the feed never permanently drifts from the DB.
  useEffect(() => {
    setLiveEvents([]);
  }, [query.data]);

  useEffect(() => {
    const source = statisticsApi.openExternalSpendStream((event) => {
      if (event.kind !== 'spend') return;
      setLiveEvents((prev) => [event as SpendEvent, ...prev].slice(0, LIVE_EVENTS_LIMIT));
    });
    return () => source?.close();
  }, []);

  const result = asRecord(query.data);
  const byService = asRecordArray(result.by_service) as Array<{
    service?: string;
    cost_usd?: number;
    request_count?: number;
  }>;
  const byHour = asRecordArray(result.by_hour) as Array<{ bucket?: string; cost_usd?: number }>;
  const recentCalls = asRecordArray(result.recent_calls) as SpendEvent[];

  const topService = byService[0]?.service ? String(byService[0].service) : '—';

  const kpis = [
    { title: 'Расход за 24 часа', value: formatUsd(result.total_cost_usd) },
    { title: 'Запросов за 24 часа', value: fmt(result.total_requests) },
    { title: 'Самый дорогой сервис', value: topService },
    { title: 'Событий в live-ленте', value: fmt(liveEvents.length) },
  ];

  // Live events since the last snapshot refetch are shown on top of the
  // persisted recent-calls list — instant feel, DB stays the source of truth.
  const combinedRecent = [...liveEvents, ...recentCalls].slice(0, 100);

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Данные учитываются с момента внедрения этой вкладки — история вызовов до этого не восстановлена."
      />

      <KpiGrid items={kpis} loading={query.isLoading} />

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={14}>
          <Card title="Расход по времени (24 часа)" size="small">
            {byHour.length ? (
              <Line
                height={240}
                data={byHour.map((item) => ({
                  bucket: String(item.bucket || ''),
                  value: Number(item.cost_usd || 0),
                }))}
                xField="bucket"
                yField="value"
                color="#2563eb"
                tooltip={(d: { bucket: string; value: number }) => ({
                  name: d.bucket,
                  value: formatUsd(d.value),
                })}
              />
            ) : (
              <Empty description="Нет данных" />
            )}
          </Card>
        </Col>
        <Col xs={24} md={10}>
          <Card title="Расход по сервисам" size="small">
            {byService.length ? (
              <Column
                height={240}
                data={byService.map((item) => ({
                  service: String(item.service || ''),
                  value: Number(item.cost_usd || 0),
                }))}
                xField="value"
                yField="service"
                seriesField="service"
                legend={false}
                color="#22c55e"
                tooltip={(d: { service: string; value: number }) => ({
                  name: d.service,
                  value: formatUsd(d.value),
                })}
              />
            ) : (
              <Empty description="Нет данных" />
            )}
          </Card>
        </Col>
      </Row>

      <Card title="Последние вызовы" size="small" style={{ marginTop: 16 }}>
        <Table
          size="small"
          rowKey={(row, index) => `${row.created_at || ''}-${index}`}
          dataSource={combinedRecent}
          loading={query.isLoading}
          pagination={{ pageSize: 20 }}
          columns={[
            {
              title: 'Время',
              dataIndex: 'created_at',
              width: 180,
              render: (value: string) => formatLocalDateTime(value || ''),
            },
            { title: 'Сервис', dataIndex: 'service' },
            { title: 'Операция', dataIndex: 'operation' },
            { title: 'Модель', dataIndex: 'model', render: (value: string | null) => value || '—' },
            {
              title: 'Стоимость',
              dataIndex: 'cost_usd',
              render: (value: number) => formatUsd(value),
            },
            {
              title: 'Статус',
              dataIndex: 'status',
              render: (value: string) => (
                <Tag color={value === 'error' ? 'error' : 'success'}>{value || 'ok'}</Tag>
              ),
            },
          ]}
        />
      </Card>

      {!query.isLoading && !combinedRecent.length ? (
        <Typography.Text type="secondary">Вызовов к внешним сервисам пока не было.</Typography.Text>
      ) : null}
    </div>
  );
}
