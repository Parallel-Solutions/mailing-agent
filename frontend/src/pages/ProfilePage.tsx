import { ProForm, ProFormText, ProFormTextArea } from '@ant-design/pro-components';
import { App, Tabs } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { profileApi } from '@/api/profile';
import { ConnectionsPage } from './ConnectionsPage';

export function ProfilePage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: () => profileApi.get(),
  });

  return (
    <Tabs
      items={[
        {
          key: 'main',
          label: 'Основные данные',
          children: (
            <ProForm
              loading={isLoading}
              initialValues={data}
              onFinish={async (values) => {
                await profileApi.update(values);
                message.success('Профиль сохранён');
                void queryClient.invalidateQueries({ queryKey: ['profile'] });
              }}
            >
              <ProFormText name="display_name" label="Имя" />
              <ProFormText name="email" label="Email" />
              <ProFormText name="company" label="Компания" />
              <ProFormText name="job_title" label="Должность" />
              <ProFormTextArea name="signature" label="Подпись" />
              <ProFormText name="timezone" label="Часовой пояс" />
            </ProForm>
          ),
        },
        { key: 'connections', label: 'Подключения', children: <ConnectionsPage /> },
        {
          key: 'security',
          label: 'Безопасность',
          children: (
            <p>Смена пароля выполняется администратором или через регистрацию нового пользователя в локальном окружении.</p>
          ),
        },
        {
          key: 'defaults',
          label: 'Настройки рассылок',
          children: (
            <ProForm
              initialValues={data?.mailing_defaults || {}}
              onFinish={async (values) => {
                await profileApi.update({ mailing_defaults: values });
                message.success('Настройки сохранены');
                void queryClient.invalidateQueries({ queryKey: ['profile'] });
              }}
            >
              <ProFormText name="default_subject" label="Тема по умолчанию" />
              <ProFormText name="default_document_mode" label="Тип документов по умолчанию" />
              <ProFormText name="default_batch_size" label="Размер пакета по умолчанию" />
            </ProForm>
          ),
        },
        {
          key: 'notifications',
          label: 'Уведомления',
          children: (
            <ProForm
              initialValues={data?.notifications || { email_on_complete: true, email_on_error: true }}
              onFinish={async (values) => {
                await profileApi.update({ notifications: values });
                message.success('Уведомления сохранены');
                void queryClient.invalidateQueries({ queryKey: ['profile'] });
              }}
            >
              <ProFormText name="email_on_complete" label="Письмо при завершении (true/false)" />
              <ProFormText name="email_on_error" label="Письмо при ошибке (true/false)" />
            </ProForm>
          ),
        },
      ]}
    />
  );
}
