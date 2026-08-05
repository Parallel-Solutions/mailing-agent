import { PlusOutlined } from '@ant-design/icons';
import {
  ModalForm,
  ProFormSelect,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import { App, Button, Space, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import {
  adminUsersApi,
  type AdminUser,
  type AdminUserRole,
} from '@/api/adminUsers';
import { companiesApi } from '@/api/companies';
import { usePermissions } from '@/hooks/usePermissions';

type UserFormValues = {
  username: string;
  password?: string;
  password_confirm?: string;
  role: AdminUserRole;
  visible_company_ids?: string[];
  managed_company_ids?: string[];
};

const ROLE_OPTIONS = [
  { label: 'Супер-администратор', value: 'admin' },
  { label: 'Администратор компаний', value: 'company_admin' },
  { label: 'Пользователь', value: 'user' },
];

function roleLabel(role: AdminUserRole) {
  return ROLE_OPTIONS.find((item) => item.value === role)?.label || role;
}

function companyAccesses(values: UserFormValues) {
  const visible = new Set(values.visible_company_ids || []);
  const managed = new Set(values.managed_company_ids || []);
  return [...new Set([...visible, ...managed])].map((companyId) => ({
    company_id: companyId,
    access_level: managed.has(companyId) ? ('manage' as const) : ('view' as const),
  }));
}

type UserFormModalProps = {
  user?: AdminUser;
  companyOptions: Array<{ label: string; value: string }>;
  trigger?: React.ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onSuccess: () => void;
};

function UserFormModal({
  user,
  companyOptions,
  trigger,
  open,
  onOpenChange,
  onSuccess,
}: UserFormModalProps) {
  const { message } = App.useApp();
  const isEdit = Boolean(user);
  return (
    <ModalForm<UserFormValues>
      title={isEdit ? `Права пользователя ${user?.username}` : 'Новый пользователь'}
      trigger={trigger}
      open={open}
      onOpenChange={onOpenChange}
      modalProps={{ destroyOnHidden: true, okText: 'Сохранить', cancelText: 'Отмена' }}
      initialValues={
        user
          ? {
              username: user.username,
              role: user.role,
              visible_company_ids: user.company_accesses.map((item) => item.company_id),
              managed_company_ids: user.company_accesses
                .filter((item) => item.access_level === 'manage')
                .map((item) => item.company_id),
            }
          : { role: 'user', visible_company_ids: [], managed_company_ids: [] }
      }
      onFinish={async (values) => {
        const accesses = companyAccesses(values);
        try {
          if (user) {
            await adminUsersApi.update(user.username, {
              role: values.role,
              company_accesses: accesses,
            });
            message.success('Права пользователя сохранены');
          } else {
            await adminUsersApi.create({
              username: values.username,
              password: values.password || '',
              password_confirm: values.password_confirm,
              role: values.role,
              company_accesses: accesses,
            });
            message.success('Пользователь создан');
          }
          onSuccess();
          return true;
        } catch (error) {
          message.error(error instanceof Error ? error.message : 'Не удалось сохранить пользователя');
          return false;
        }
      }}
    >
      <ProFormText
        name="username"
        label="Логин"
        disabled={isEdit}
        rules={[{ required: true }, { min: 3 }, { max: 32 }]}
      />
      {!isEdit ? (
        <>
          <ProFormText.Password
            name="password"
            label="Пароль"
            rules={[{ required: true }, { min: 8 }]}
          />
          <ProFormText.Password
            name="password_confirm"
            label="Повторите пароль"
            dependencies={['password']}
            rules={[
              { required: true },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return !value || getFieldValue('password') === value
                    ? Promise.resolve()
                    : Promise.reject(new Error('Пароли не совпадают'));
                },
              }),
            ]}
          />
        </>
      ) : null}
      <ProFormSelect
        name="role"
        label="Роль"
        options={ROLE_OPTIONS}
        rules={[{ required: true }]}
      />
      <ProFormSelect
        name="visible_company_ids"
        label="Компании для просмотра"
        mode="multiple"
        options={companyOptions}
      />
      <ProFormSelect
        name="managed_company_ids"
        label="Компании для настройки"
        mode="multiple"
        options={companyOptions}
        tooltip="Настройка также автоматически даёт право просмотра"
      />
    </ModalForm>
  );
}

export function UsersRolesPage() {
  const queryClient = useQueryClient();
  const { isAppAdmin } = usePermissions();
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const usersQuery = useQuery({
    queryKey: ['admin-users'],
    queryFn: adminUsersApi.list,
    enabled: isAppAdmin,
  });
  const companiesQuery = useQuery({
    queryKey: ['companies'],
    queryFn: companiesApi.list,
    enabled: isAppAdmin,
  });

  if (!isAppAdmin) return <Navigate to="/" replace />;

  const companyOptions = (companiesQuery.data?.items || []).map((company) => ({
    label: company.name,
    value: company.id,
  }));
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['admin-users'] });

  return (
    <div>
      <Typography.Title level={3}>Пользователи и роли</Typography.Title>
      <Typography.Paragraph type="secondary">
        Супер-администратор создаёт пользователей и назначает доступ к компаниям.
      </Typography.Paragraph>
      <ProTable<AdminUser>
        rowKey="username"
        search={false}
        options={false}
        loading={usersQuery.isLoading || companiesQuery.isLoading}
        dataSource={usersQuery.data?.items || []}
        pagination={{ total: usersQuery.data?.total || 0 }}
        toolBarRender={() => [
          <UserFormModal
            key="create"
            companyOptions={companyOptions}
            trigger={
              <Button type="primary" icon={<PlusOutlined />}>
                Добавить пользователя
              </Button>
            }
            onSuccess={refresh}
          />,
        ]}
        columns={[
          { title: 'Пользователь', dataIndex: 'username' },
          {
            title: 'Роль',
            dataIndex: 'role',
            render: (_, row) => <Tag color={row.role === 'admin' ? 'red' : 'blue'}>{roleLabel(row.role)}</Tag>,
          },
          {
            title: 'Компании',
            render: (_, row) => (
              <Space wrap>
                {row.company_accesses.map((access) => (
                  <Tag key={access.company_id} color={access.access_level === 'manage' ? 'green' : 'default'}>
                    {access.company_name} · {access.access_level === 'manage' ? 'настройка' : 'просмотр'}
                  </Tag>
                ))}
              </Space>
            ),
          },
          {
            title: 'Действия',
            valueType: 'option',
            render: (_, row) => [
              <a key="edit" onClick={() => setEditingUser(row)}>
                Изменить права
              </a>,
            ],
          },
        ]}
      />
      {editingUser ? (
        <UserFormModal
          key={editingUser.username}
          user={editingUser}
          companyOptions={companyOptions}
          open
          onOpenChange={(nextOpen) => {
            if (!nextOpen) setEditingUser(null);
          }}
          onSuccess={() => {
            refresh();
            setEditingUser(null);
          }}
        />
      ) : null}
    </div>
  );
}
