import { ProForm, ProFormSwitch, ProFormText, ProFormTextArea } from '@ant-design/pro-components';
import { App, Tabs, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { profileApi } from '@/api/profile';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { readEnumParam } from '@/utils/urlState';
import { ConnectionsPage } from './ConnectionsPage';

export const PROFILE_TABS = ['main', 'connections', 'security', 'defaults', 'notifications'] as const;

export function ProfilePage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { searchParams, pushParams } = useUrlNavigation();
  const activeTab = readEnumParam(searchParams, 'tab', PROFILE_TABS, 'main');
  const { data, isLoading } = useQuery({
    queryKey: ['profile'],
    queryFn: () => profileApi.get(),
  });

  return (
    <Tabs
      activeKey={activeTab}
      onChange={(key) => pushParams({ tab: key === 'main' ? null : key })}
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
              <ProFormText name="display_name" label="Отображаемое имя" />
              <ProFormText name="company" label="Компания" />
              <ProFormText name="job_title" label="Должность" />
              <ProFormText name="email" label="Email" />
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
            <Typography.Paragraph type="secondary">
              Смена пароля в этом интерфейсе недоступна. В локальном окружении создайте нового
              пользователя через регистрацию или обратитесь к администратору.
            </Typography.Paragraph>
          ),
        },
        {
          key: 'defaults',
          label: 'Настройки рассылок',
          children: (
            <div data-onboarding-id="profile-defaults">
              <ProForm
                initialValues={data?.mailing_defaults || {}}
                onFinish={async (values) => {
                  await profileApi.update({ mailing_defaults: values });
                  message.success('Настройки сохранены');
                  void queryClient.invalidateQueries({ queryKey: ['profile'] });
                }}
              >
                <ProFormText name="default_subject" label="Тема по умолчанию" />
                <ProFormText name="default_batch_size" label="Размер пакета по умолчанию" />
              </ProForm>
            </div>
          ),
        },
        {
          key: 'notifications',
          label: 'Уведомления',
          children: (
            <div data-onboarding-id="profile-notifications">
              <ProForm
                initialValues={data?.notifications || { email_on_complete: true, email_on_error: true }}
                onFinish={async (values) => {
                  await profileApi.update({ notifications: values });
                  message.success('Уведомления сохранены');
                  void queryClient.invalidateQueries({ queryKey: ['profile'] });
                }}
              >
                <ProFormSwitch name="email_on_complete" label="Письмо при завершении рассылки" />
                <ProFormSwitch name="email_on_error" label="Письмо при ошибке отправки" />
              </ProForm>
            </div>
          ),
        },
      ]}
    />
  );
}
