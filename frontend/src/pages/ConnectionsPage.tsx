import { PlusOutlined } from '@ant-design/icons';
import { ModalForm, ProFormDigit, ProFormSwitch, ProFormText, ProTable } from '@ant-design/pro-components';
import { App, Button, Space, Tag } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { connectionsApi } from '@/api/connections';
import type { SmtpMailbox } from '@/api/types';

export function ConnectionsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['mailboxes'],
    queryFn: () => connectionsApi.list(),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.remove(id),
    onSuccess: () => {
      message.success('Подключение удалено');
      void queryClient.invalidateQueries({ queryKey: ['mailboxes'] });
    },
  });

  return (
    <ProTable<SmtpMailbox>
      rowKey="id"
      loading={isLoading}
      search={false}
      headerTitle="Подключения"
      toolBarRender={() => [
        <ModalForm
          key="add"
          title="Подключить SMTP"
          trigger={
            <Button type="primary" icon={<PlusOutlined />}>
              Добавить
            </Button>
          }
          initialValues={{
            provider: 'custom',
            host: 'mailpit',
            port: 1025,
            use_ssl: false,
            use_starttls: false,
            email: 'sender@mailpit.local',
            password: 'mailpit',
            sender_name: 'CampaignFlow Test',
          }}
          onFinish={async (values) => {
            await connectionsApi.create({
              ...values,
              provider: 'custom',
              send_test: false,
              make_default: false,
            });
            message.success('Подключение создано');
            void queryClient.invalidateQueries({ queryKey: ['mailboxes'] });
            return true;
          }}
        >
          <ProFormText name="email" label="Email" rules={[{ required: true }]} />
          <ProFormText.Password name="password" label="Пароль приложения" rules={[{ required: true }]} />
          <ProFormText name="sender_name" label="Имя отправителя" />
          <ProFormText name="host" label="SMTP host" rules={[{ required: true }]} />
          <ProFormDigit name="port" label="Port" rules={[{ required: true }]} />
          <ProFormSwitch name="use_ssl" label="SSL" />
          <ProFormSwitch name="use_starttls" label="STARTTLS" />
        </ModalForm>,
      ]}
      dataSource={data || []}
      columns={[
        { title: 'Email', dataIndex: 'email' },
        { title: 'Провайдер', dataIndex: 'provider' },
        { title: 'Host', dataIndex: 'host' },
        {
          title: 'Статус',
          dataIndex: 'status',
          render: (_, row) => <Tag color={row.status === 'active' ? 'green' : 'red'}>{row.status}</Tag>,
        },
        {
          title: 'По умолчанию',
          dataIndex: 'is_default',
          render: (v) => (v ? 'да' : ''),
        },
        {
          title: 'Действия',
          valueType: 'option',
          render: (_, row) => (
            <Space>
              <a
                onClick={async () => {
                  await connectionsApi.test(row.id);
                  message.success('Проверка выполнена');
                  void queryClient.invalidateQueries({ queryKey: ['mailboxes'] });
                }}
              >
                Проверить
              </a>
              <a onClick={() => removeMutation.mutate(row.id)}>Удалить</a>
            </Space>
          ),
        },
      ]}
    />
  );
}
