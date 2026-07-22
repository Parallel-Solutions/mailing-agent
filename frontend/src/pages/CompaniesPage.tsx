import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { Button, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate } from 'react-router-dom';
import { companiesApi } from '@/api/companies';
import type { Company } from '@/api/types';
import { CompanyFormModal } from '@/features/companies/CompanyFormModal';
import { CompanyWorkTypesModal } from '@/features/companies/CompanyWorkTypesModal';
import { usePermissions } from '@/hooks/usePermissions';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';

export function CompaniesPage() {
  const queryClient = useQueryClient();
  const { isAppAdmin } = usePermissions();
  const { searchParams, pushParams } = useUrlNavigation();
  const editId = searchParams.get('edit');
  const workTypesId = searchParams.get('work_types');

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
