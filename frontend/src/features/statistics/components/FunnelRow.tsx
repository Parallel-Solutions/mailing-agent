import { Card, Col, Row, Typography } from 'antd';
import { asRecordArray } from '../utils';

type Props = {
  funnel: unknown;
  title?: string;
};

export function FunnelRow({ funnel, title = 'Воронка рассылки' }: Props) {
  const steps = asRecordArray(funnel);
  return (
    <Card title={title} size="small" style={{ marginTop: 16 }}>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
        Доля от базы воронки на каждом этапе
      </Typography.Paragraph>
      <Row gutter={[12, 12]}>
        {steps.map((step) => (
          <Col key={String(step.label)} flex="1 1 120px">
            <div style={{ textAlign: 'center', padding: '8px 4px' }}>
              <div style={{ fontSize: 22, fontWeight: 600 }}>{Number(step.percent ?? 0)}%</div>
              <Typography.Text type="secondary">{String(step.label || '')}</Typography.Text>
            </div>
          </Col>
        ))}
        {!steps.length ? (
          <Col span={24}>
            <Typography.Text type="secondary">Нет данных</Typography.Text>
          </Col>
        ) : null}
      </Row>
    </Card>
  );
}
