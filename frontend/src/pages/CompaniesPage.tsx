import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Popconfirm, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate } from 'react-router-dom';
import { companiesApi } from '@/api/companies';
import type { Company } from '@/api/types';
import { CompanyFormModal } from '@/features/companies/CompanyFormModal';
import { CompanyWorkTypesModal } from '@/features/companies/CompanyWorkTypesModal';
import { usePermissions } from '@/hooks/usePermissions';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';

export function CompaniesPage() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const { isAppAdmin } = usePermissions();
  const { searchParams, pushParams } = useUrlNavigation();
  const editId = searchParams.get('edit');
  const workTypesId = searchParams.get('work_types');
  const deleteCompany = useMutation({
    mutationFn: companiesApi.remove,
    onSuccess: (_, companyId) => {
      if (editId === companyId) pushParams({}, ['edit']);
      if (workTypesId === companyId) pushParams({}, ['work_types']);
      void queryClient.invalidateQueries({ queryKey: ['companies'] });
      message.success('Компания удалена');
    },
    onError: (error) => {
      message.error(
        error instanceof Error ? error.message : 'Не удалось удалить компанию',
      );
    },
  });



  const { data, isLoading } = useQuery({
    queryKey: ['companies'],
    queryFn: () => companiesApi.list(),
    enabled: isAppAdmin,
  });

  if (!isAppAdmin) {
    return <Navigate to="/" replace />;
  }

  const editCompany = data?.items.find((item) => item.id === editId);
  const workTypesCompany = data?.items.find((item) => item.id === workTypesId);

  return (
    <>
      <Typography.Title level={3}>Компании</Typography.Title>
      <Typography.Paragraph type="secondary">Управление организациями.</Typography.Paragraph>
      <ProTable<Company>
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items || []}
        search={false}
        options={false}
        pagination={{ total: data?.total || 0 }}
        toolBarRender={() => [
          <CompanyFormModal
            key="create"
            mode="create"
            trigger={
              <Button type="primary" icon={<PlusOutlined />}>
                Создать компанию
              </Button>
            }
            onSuccess={() => {
              void queryClient.invalidateQueries({ queryKey: ['companies'] });
            }}
          />,
        ]}
        columns={[
          { title: 'Название', dataIndex: 'name' },
          { title: 'Телефон', dataIndex: 'phone' },
          { title: 'Контактное лицо', dataIndex: 'contact_person_name' },
          { title: 'Участников', dataIndex: 'member_count', width: 120 },
          {
            title: 'Действия',
            valueType: 'option',
            render: (_, row) => [
              <a key="edit" onClick={() => pushParams({ edit: row.id })}>
                Редактировать
              </a>,
              <a key="work-types" onClick={() => pushParams({ work_types: row.id })}>
                Виды работ
              </a>,
              <Popconfirm
                key="delete"
                title={`Удалить компанию «${row.name}»?`}
                description="Компания и её настройки будут удалены. Аккаунты участников и их данные сохранятся."
                okText="Удалить"
                cancelText="Отмена"
                okButtonProps={{ danger: true }}
                onConfirm={() => deleteCompany.mutateAsync(row.id)}
              >
                <Button
                  type="link"
                  danger
                  icon={<DeleteOutlined />}
                  loading={deleteCompany.isPending && deleteCompany.variables === row.id}
                >
                  Удалить
                </Button>
              </Popconfirm>,
            ],
          },
        ]}
      />

      {editCompany ? (
        <CompanyFormModal
          mode="edit"
          company={editCompany}
          open
          onOpenChange={(open) => {
            if (!open) pushParams({}, ['edit']);
          }}
          onSuccess={() => {
            void queryClient.invalidateQueries({ queryKey: ['companies'] });
            pushParams({}, ['edit']);
          }}
        />
      ) : null}

      {workTypesCompany ? (
        <CompanyWorkTypesModal
          company={workTypesCompany}
          open
          onOpenChange={(open) => {
            if (!open) pushParams({}, ['work_types']);
          }}
        />
      ) : null}
    </>
  );
}
