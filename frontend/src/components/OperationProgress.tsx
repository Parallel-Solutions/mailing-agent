import { CheckCircleFilled, LoadingOutlined } from '@ant-design/icons';
import { Spin, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { formatElapsedTime, operationStageIndex } from './operationProgressUtils';
import './OperationProgress.css';

type Props = {
  active: boolean;
  title: string;
  stages: string[];
  estimatedSeconds?: number | [number, number];
  compact?: boolean;
};

export function OperationProgress({
  active,
  title,
  stages,
  estimatedSeconds = [10, 30],
  compact = false,
}: Props) {
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [minimumSeconds, maximumSeconds] = useMemo(
    () =>
      Array.isArray(estimatedSeconds)
        ? estimatedSeconds
        : [estimatedSeconds, estimatedSeconds],
    [estimatedSeconds],
  );

  useEffect(() => {
    if (!active) {
      setElapsedSeconds(0);
      return;
    }
    setElapsedSeconds(0);
    const interval = globalThis.setInterval(() => {
      setElapsedSeconds((current) => current + 1);
    }, 1000);
    return () => globalThis.clearInterval(interval);
  }, [active]);

  if (!active) return null;

  const currentStage = operationStageIndex(
    elapsedSeconds,
    Math.max(1, stages.length),
    Math.max(1, maximumSeconds),
  );
  const estimateLabel =
    minimumSeconds === maximumSeconds
      ? `Обычно около ${maximumSeconds} сек.`
      : `Обычно ${minimumSeconds}–${maximumSeconds} сек.`;
  const isLongerThanExpected = elapsedSeconds > maximumSeconds;

  return (
    <div
      className={[
        'operation-progress',
        compact ? 'operation-progress--compact' : '',
      ].filter(Boolean).join(' ')}
      role="status"
      aria-live="polite"
    >
      <div className="operation-progress__heading">
        <Spin indicator={<LoadingOutlined spin />} size="small" />
        <div>
          <Typography.Text strong>{title}</Typography.Text>
          <div className="operation-progress__meta">
            <Typography.Text type="secondary">
              Прошло {formatElapsedTime(elapsedSeconds)}
            </Typography.Text>
            <Typography.Text type={isLongerThanExpected ? 'warning' : 'secondary'}>
              {isLongerThanExpected
                ? 'Идёт дольше обычного, операция продолжается.'
                : estimateLabel}
            </Typography.Text>
          </div>
        </div>
      </div>
      <div className="operation-progress__stages">
        {stages.map((stage, index) => {
          const complete = index < currentStage;
          const current = index === currentStage;
          return (
            <div
              key={`${index}-${stage}`}
              className={[
                'operation-progress__stage',
                complete ? 'operation-progress__stage--complete' : '',
                current ? 'operation-progress__stage--current' : '',
              ].filter(Boolean).join(' ')}
            >
              <span className="operation-progress__stage-icon" aria-hidden>
                {complete ? <CheckCircleFilled /> : current ? <LoadingOutlined spin /> : index + 1}
              </span>
              <Typography.Text type={current || complete ? undefined : 'secondary'}>
                {stage}
                {current ? ' — выполняется' : ''}
              </Typography.Text>
            </div>
          );
        })}
      </div>
      <Typography.Text type="secondary" className="operation-progress__note">
        Процент не показывается: сервер выполняет эту операцию одним запросом и не сообщает промежуточные значения.
      </Typography.Text>
    </div>
  );
}
