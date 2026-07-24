import { InfoCircleOutlined } from '@ant-design/icons';
import { Popover, Typography } from 'antd';
import { getMetricGlossary } from './metricGlossary';

type Props = {
  metricId: string;
  label?: string;
};

export function MetricInfo({ metricId, label }: Props) {
  const entry = getMetricGlossary(metricId);
  if (!entry) return label ? <span>{label}</span> : null;

  const content = (
    <div style={{ maxWidth: 360 }}>
      <Typography.Paragraph style={{ marginBottom: 8 }}>{entry.description}</Typography.Paragraph>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
        Как считается: {entry.formula}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ display: 'block' }}>
        Источник: {entry.source}
      </Typography.Text>
    </div>
  );

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {label ?? entry.title}
      <Popover title={entry.title} content={content} trigger="click">
        <InfoCircleOutlined
          role="button"
          aria-label={`Справка: ${entry.title}`}
          style={{ color: '#8c8c8c', cursor: 'pointer', fontSize: 12 }}
          onClick={(event) => event.stopPropagation()}
        />
      </Popover>
    </span>
  );
}
