import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Empty, Tag } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { chainsApi, type ChainListItem } from '@/api/chains';
import { formatLocalDateTime } from '@/utils/dateTime';

type ChainsLocationState = {
  campaignId?: string;
};

export function ChainsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const campaignId = (location.state as ChainsLocationState | null)?.campaignId;
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['chains'],
    queryFn: () => chainsApi.list({ limit: 100 }),
  });

  const chainNavigationState = campaignId ? { campaignId } : undefined;

  const campaignUrl = (chainId: string) =>
    campaignId
      ? `/campaigns/new?id=${campaignId}&email_chain_id=${chainId}`
      : `/campaigns/new?email_chain_id=${chainId}`;

  const createChain = useMutation({
    mutationFn: () => chainsApi.create({ name: 'Новая цепочка' }),
    onSuccess: (chain) => {
      void queryClient.invalidateQueries({ queryKey: ['chains'] });
      navigate(`/chains/${chain.id}`, { state: chainNavigationState });
    },
    onError: (error: Error) => message.error(error.message),
  });

  return (
    <div data-onboarding-id="chains-overview">
      <ProTable<ChainListItem>
      rowKey="id"
      loading={isLoading}
      search={false}
      headerTitle="Конструктор цепочек"
      toolBarRender={() => [
        <Button
          key="new"
          type="primary"
          icon={<PlusOutlined />}
          loading={createChain.isPending}
          onClick={() => createChain.mutate()}
        >
          Создать цепочку
        </Button>,
      ]}
      dataSource={data?.items ?? []}
      locale={{
        emptyText: (
          <Empty description="Цепочек пока нет">
            <Button
              type="primary"
              icon={<PlusOutlined />}
              loading={createChain.isPending}
              onClick={() => createChain.mutate()}
            >
              Создать цепочку
            </Button>
          </Empty>
        ),
      }}
      columns={[
        {
          title: 'Название',
          dataIndex: 'name',
          render: (_, row) => (
            <Link to={`/chains/${row.id}`} state={chainNavigationState}>
              {row.name}
            </Link>
          ),
        },
        {
          title: 'Статус',
          dataIndex: 'published',
          render: (_, row) => (
            <Tag color={row.published ? 'success' : 'default'}>
              {row.published ? 'Опубликована' : 'Черновик'}
            </Tag>
          ),
        },
        { title: 'Обновлена', dataIndex: 'updated_at', render: (_, row) => formatLocalDateTime(row.updated_at) },
        {
          title: 'Действия',
          valueType: 'option',
          render: (_, row) => [
            <a
              key="open"
              onClick={() => navigate(`/chains/${row.id}`, { state: chainNavigationState })}
            >
              Открыть конструктор
            </a>,
            <a key="campaign" onClick={() => navigate(campaignUrl(row.id))}>
              К рассылке
            </a>,
          ],
        },
      ]}
      />
    </div>
  );
}
