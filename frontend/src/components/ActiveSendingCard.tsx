import { PauseCircleOutlined, PlayCircleOutlined } from '@ant-design/icons';
import { ProCard } from '@ant-design/pro-components';
import { Button, Progress, Space, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';

export function ActiveSendingCard() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['active-sending'],
    queryFn: () => campaignsApi.activeSending(),
    refetchInterval: 10_000,
  });

  const pauseMutation = useMutation({
    mutationFn: (id: string) => campaignsApi.pause(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['active-sending'] }),
  });
  const resumeMutation = useMutation({
    mutationFn: (id: string) => campaignsApi.resume(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['active-sending'] }),
  });

  if (isLoading || !data) {
    return null;
  }

  return (
    <ProCard
      title={data.name}
      bordered
      data-testid="active-sending-card"
      style={{ marginBottom: 16 }}
      extra={<Link to={`/campaigns/${data.campaign_id}`}>Открыть</Link>}
    >
      <Progress percent={data.progress} status={data.status === 'paused' ? 'exception' : 'active'} />
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Typography.Text>
          Отправлено {data.sent_count} из {data.total_count} (осталось {data.remaining})
        </Typography.Text>
        <Typography.Text type="secondary">
          В очереди пакетов: {data.queued_batches}; сейчас отправляется: {data.sending_now}; следующий
          пакет: {data.next_batch_size} в {data.next_batch_at || '—'}
        </Typography.Text>
        <Typography.Text type="secondary">
          Пакет {data.batch_size}, интервал {data.interval_seconds}с, лимит час/день:{' '}
          {data.max_per_hour || '∞'}/{data.max_per_day || '∞'}
        </Typography.Text>
        <Space>
          {data.status === 'paused' ? (
            <Button
              icon={<PlayCircleOutlined />}
              loading={resumeMutation.isPending}
              onClick={() => resumeMutation.mutate(data.campaign_id)}
            >
              Продолжить
            </Button>
          ) : (
            <Button
              icon={<PauseCircleOutlined />}
              loading={pauseMutation.isPending}
              onClick={() => pauseMutation.mutate(data.campaign_id)}
            >
              Пауза
            </Button>
          )}
          <Link to={`/campaigns/${data.campaign_id}?tab=queue`}>Очередь</Link>
        </Space>
      </Space>
    </ProCard>
  );
}
