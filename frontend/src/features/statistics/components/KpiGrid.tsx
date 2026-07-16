import { Card, Col, Row, Typography } from 'antd';

export type KpiItem = {
  title: string;
  value: string | number;
  drill?: string;
};

type Props = {
  items: KpiItem[];
  onDrill?: (drillKey: string) => void;
  loading?: boolean;
};

export function KpiGrid({ items, onDrill, loading }: Props) {
  return (
    <Row gutter={[12, 12]}>
      {items.map((item) => {
        const clickable = Boolean(item.drill && onDrill);
        return (
          <Col key={`${item.title}-${item.drill || ''}`} xs={12} sm={8} md={6} lg={6} xl={3}>
            <Card
              size="small"
              loading={loading}
              hoverable={clickable}
              onClick={() => {
                if (item.drill && onDrill) onDrill(item.drill);
              }}
              style={{
                cursor: clickable ? 'pointer' : 'default',
                height: '100%',
                borderColor: '#e2e7d8',
              }}
            >
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {item.title}
              </Typography.Text>
              <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>{item.value}</div>
            </Card>
          </Col>
        );
      })}
    </Row>
  );
}
