import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Empty, Tag } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { chainsApi, type ChainListItem } from '@/api/chains';

export function ChainsPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['chains'],
    queryFn: () => chainsApi.list({ limit: 100 }),
  });

  const createChain = useMutation({
    mutationFn: () => chainsApi.create({ name: 'Новая цепочка' }),
    onSuccess: (chain) => {
      void queryClient.invalidateQueries({ queryKey: ['chains'] });
      navigate(`/chains/${chain.id}`);
    },
    onError: (error: Error) => message.error(error.message),
  });

  return (
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
          render: (_, row) => <Link to={`/chains/${row.id}`}>{row.name}</Link>,
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
        { title: 'Обновлена', dataIndex: 'updated_at', valueType: 'dateTime' },
        {
          title: 'Действия',
          valueType: 'option',
          render: (_, row) => [
            <a key="open" onClick={() => navigate(`/chains/${row.id}`)}>
              Открыть конструктор
            </a>,
            <a key="campaign" onClick={() => navigate(`/campaigns/new?email_chain_id=${row.id}`)}>
              К рассылке
            </a>,
          ],
        },
      ]}
    />
  );
}
