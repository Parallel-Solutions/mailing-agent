import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Progress, Space, Tag } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';
import type { Campaign } from '@/api/types';

const statusColor: Record<string, string> = {
  draft: 'default',
  scheduled: 'processing',
  running: 'success',
  paused: 'warning',
  completed: 'blue',
  completed_with_errors: 'orange',
  cancelled: 'error',
};

export function CampaignsListPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => campaignsApi.list({ limit: 100 }),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['campaigns'] });

  const pause = useMutation({
    mutationFn: (id: string) => campaignsApi.pause(id),
    onSuccess: () => {
      message.success('Пауза');
      invalidate();
    },
  });
  const resume = useMutation({
    mutationFn: (id: string) => campaignsApi.resume(id),
    onSuccess: () => {
      message.success('Продолжено');
      invalidate();
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => campaignsApi.cancel(id),
    onSuccess: () => {
      message.success('Отменено');
      invalidate();
    },
  });
  const duplicate = useMutation({
    mutationFn: (id: string) => campaignsApi.duplicate(id),
    onSuccess: (camp) => {
      message.success('Копия создана');
      navigate(`/campaigns/new?id=${camp.id}`);
    },
  });

  return (
    <ProTable<Campaign>
      rowKey="id"
      loading={isLoading}
      search={false}
      headerTitle="Рассылки"
      toolBarRender={() => [
        <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => navigate('/campaigns/new')}>
          Создать рассылку
        </Button>,
      ]}
      dataSource={data?.items || []}
      columns={[
        {
          title: 'Название',
          dataIndex: 'name',
          render: (_, row) => <Link to={`/campaigns/${row.id}`}>{row.name}</Link>,
        },
        {
          title: 'Статус',
          dataIndex: 'status',
          render: (_, row) => <Tag color={statusColor[row.status] || 'default'}>{row.status}</Tag>,
        },
        { title: 'Тема', dataIndex: 'mail_subject', ellipsis: true },
        { title: 'Провайдер', dataIndex: 'transport' },
        {
          title: 'Прогресс',
          render: (_, row) => (
            <Progress
              percent={row.progress || 0}
              size="small"
              format={() => `${row.sent_count || 0}/${row.total_count || 0}`}
            />
          ),
        },
        { title: 'Ошибки', dataIndex: 'error_count' },
        { title: 'Создана', dataIndex: 'created_at', valueType: 'dateTime' },
        {
          title: 'Действия',
          valueType: 'option',
          render: (_, row) => (
            <Space>
              <a onClick={() => navigate(`/campaigns/${row.id}`)}>Открыть</a>
              <a onClick={() => navigate(`/campaigns/new?id=${row.id}`)}>Редактировать</a>
              <a onClick={() => duplicate.mutate(row.id)}>Дублировать</a>
              {row.status === 'paused' ? (
                <a onClick={() => resume.mutate(row.id)}>Продолжить</a>
              ) : row.status === 'running' || row.status === 'scheduled' ? (
                <a onClick={() => pause.mutate(row.id)}>Пауза</a>
              ) : null}
              {['running', 'scheduled', 'paused'].includes(row.status) ? (
                <a onClick={() => cancel.mutate(row.id)}>Отменить</a>
              ) : null}
            </Space>
          ),
        },
      ]}
    />
  );
}
