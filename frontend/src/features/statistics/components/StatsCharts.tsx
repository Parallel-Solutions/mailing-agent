import { Card, Col, Empty, Row } from 'antd';
import { Column, Line, Pie } from '@ant-design/charts';
import { asRecordArray, fmt } from '../utils';

const COLORS = ['#22c55e', '#8b5cf6', '#2563eb', '#ef4444', '#f59e0b', '#64748b'];

type NamedCount = { label?: string; count?: number; provider?: string };

const typeValueTooltip = (d: { type: string; value: number }) => ({
  name: d.type,
  value: fmt(d.value),
});

const labelValueTooltip = (d: { label: string; value: number }) => ({
  name: d.label,
  value: fmt(d.value),
});

const seriesTooltip = (d: { type: string; value: number; date?: string; provider?: string }) => ({
  name: d.date ? `${d.date} — ${d.type}` : d.provider ? `${d.provider} — ${d.type}` : d.type,
  value: fmt(d.value),
});

export function DashboardCharts({
  statuses,
  providers,
  roles,
}: {
  statuses: unknown;
  providers: unknown;
  roles: unknown;
}) {
  const statusItems = asRecordArray(statuses) as NamedCount[];
  const providerItems = asRecordArray(providers) as NamedCount[];
  const roleItems = asRecordArray(roles) as NamedCount[];

  return (
    <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
      <Col xs={24} md={8}>
        <Card title="Статусы доставки" size="small">
          {statusItems.length ? (
            <Pie
              height={240}
              data={statusItems.map((item) => ({ type: String(item.label), value: Number(item.count || 0) }))}
              angleField="value"
              colorField="type"
              radius={0.9}
              legend={{ position: 'bottom' }}
              color={COLORS}
              tooltip={typeValueTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
      <Col xs={24} md={8}>
        <Card title="По провайдерам" size="small">
          {providerItems.length ? (
            <Column
              height={240}
              data={providerItems.map((item) => ({
                label: String(item.label),
                value: Number(item.count || 0),
              }))}
              xField="value"
              yField="label"
              seriesField="label"
              legend={false}
              color="#2563eb"
              tooltip={labelValueTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
      <Col xs={24} md={8}>
        <Card title="По роли адреса" size="small">
          {roleItems.length >= 2 ? (
            <Pie
              height={240}
              data={roleItems.map((item) => ({ type: String(item.label), value: Number(item.count || 0) }))}
              angleField="value"
              colorField="type"
              radius={0.9}
              color={['#5a9e1f', '#c9b98a']}
              tooltip={typeValueTooltip}
            />
          ) : (
            <Empty description="Недостаточно данных для сравнения" />
          )}
        </Card>
      </Col>
    </Row>
  );
}

export function AnalyticsCharts({
  daily,
  reasons,
  providerEff,
}: {
  daily: unknown;
  reasons: unknown;
  providerEff: unknown;
}) {
  const dailyItems = asRecordArray(daily);
  const reasonItems = asRecordArray(reasons);
  const effItems = asRecordArray(providerEff);

  const dailyData = dailyItems.flatMap((item) => [
    { date: String(item.date), type: 'Отправлено', value: Number(item.sent || 0) },
    { date: String(item.date), type: 'Доставлено', value: Number(item.delivered || 0) },
    { date: String(item.date), type: 'Открыто', value: Number(item.opened || 0) },
  ]);

  const effData = effItems.flatMap((item) => [
    {
      provider: String(item.provider),
      type: 'Доставляемость',
      value: Number(item.delivery_rate || 0),
    },
    {
      provider: String(item.provider),
      type: 'Открываемость',
      value: Number(item.open_rate || 0),
    },
  ]);

  return (
    <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
      <Col xs={24} lg={12}>
        <Card title="Динамика по дням" size="small">
          {dailyData.length ? (
            <Line
              height={260}
              data={dailyData}
              xField="date"
              yField="value"
              seriesField="type"
              tooltip={seriesTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
      <Col xs={24} lg={6}>
        <Card title="Причины недоставки" size="small">
          {reasonItems.length ? (
            <Column
              height={260}
              data={reasonItems.map((item) => ({
                label: String(item.label),
                value: Number(item.count || 0),
              }))}
              xField="value"
              yField="label"
              color="#ef4444"
              legend={false}
              tooltip={labelValueTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
      <Col xs={24} lg={6}>
        <Card title="Эффективность провайдеров" size="small">
          {effData.length ? (
            <Column
              height={260}
              data={effData}
              xField="provider"
              yField="value"
              seriesField="type"
              isGroup
              tooltip={seriesTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
    </Row>
  );
}

export function ProblemsCharts({ reasons, domains }: { reasons: unknown; domains: unknown }) {
  const reasonItems = asRecordArray(reasons);
  const domainItems = asRecordArray(domains);
  return (
    <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
      <Col xs={24} md={12}>
        <Card title="Причины" size="small">
          {reasonItems.length ? (
            <Pie
              height={240}
              data={reasonItems.map((item) => ({ type: String(item.label), value: Number(item.count || 0) }))}
              angleField="value"
              colorField="type"
              radius={0.9}
              tooltip={typeValueTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
      <Col xs={24} md={12}>
        <Card title="По доменам / провайдерам" size="small">
          {domainItems.length ? (
            <Column
              height={240}
              data={domainItems.map((item) => ({
                label: String(item.provider),
                value: Number(item.count || 0),
              }))}
              xField="value"
              yField="label"
              color="#f97316"
              legend={false}
              tooltip={labelValueTooltip}
            />
          ) : (
            <Empty description="Нет данных" />
          )}
        </Card>
      </Col>
    </Row>
  );
}
