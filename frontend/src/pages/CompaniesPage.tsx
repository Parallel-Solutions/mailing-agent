import { ArrowLeftOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Popconfirm, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { companiesApi } from '@/api/companies';
import type { Company } from '@/api/types';
import { CompanyFormModal } from '@/features/companies/CompanyFormModal';
import { CompanyWorkTypesModal } from '@/features/companies/CompanyWorkTypesModal';
import { resolveCampaignReturnTarget } from '@/features/campaigns/campaignNavigation';
import { usePermissions } from '@/hooks/usePermissions';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import {
  useActiveOnboardingStep,
} from '@/features/onboarding/events';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';

const ONBOARDING_COMPANY: Company = {
  id: 'onboarding-preview',
  name: 'Пример компании',
  phone: '+7 (000) 000-00-00',
  contact_person_name: 'Ответственный сотрудник',
};

export function CompaniesPage() {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const location = useLocation();
  const navigate = useNavigate();
  const activeCampaignId = useCampaignDraftStore((state) => state.campaignId);
  const campaignContext = resolveCampaignReturnTarget(location.state, activeCampaignId);
  const { isAppAdmin, canAccessCompanies, canManageCompany } = usePermissions();
  const { searchParams, pushParams } = useUrlNavigation();
  const editId = searchParams.get('edit');
  const workTypesId = searchParams.get('work_types');
  const [onboardingPreviewStep, setOnboardingPreviewStep] = useState<string | null>(null);
  const activeOnboardingStep = useActiveOnboardingStep();
  const previousOnboardingStepRef = useRef<string | null>(null);
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
    enabled: canAccessCompanies,
  });

  useEffect(() => {
    const previousStep = previousOnboardingStepRef.current;
    previousOnboardingStepRef.current = activeOnboardingStep;
    if (activeOnboardingStep?.startsWith('company-')) {
      setOnboardingPreviewStep(activeOnboardingStep);
    } else if (previousStep?.startsWith('company-')) {
      setOnboardingPreviewStep(null);
    }
  }, [activeOnboardingStep]);

  if (!canAccessCompanies) {
    return <Navigate to="/" replace />;
  }

  const editCompany = data?.items.find(
    (item) => item.id === editId && canManageCompany(item.id),
  );
  const workTypesCompany = data?.items.find(
    (item) => item.id === workTypesId && canManageCompany(item.id),
  );

  return (
    <div>
      <div data-onboarding-id="companies-overview">
        <Typography.Title level={3}>Компании</Typography.Title>
        <Typography.Paragraph type="secondary">Управление организациями.</Typography.Paragraph>
      </div>
      <ProTable<Company>
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items || []}
        search={false}
        options={false}
        pagination={{ total: data?.total || 0 }}
        toolBarRender={() => [
          ...(campaignContext
            ? [
                <Button
                  key="return-to-campaign"
                  icon={<ArrowLeftOutlined />}
                  onClick={() => navigate(campaignContext.path)}
                >
                  {'\u0412\u0435\u0440\u043d\u0443\u0442\u044c\u0441\u044f \u043a \u0440\u0430\u0441\u0441\u044b\u043b\u043a\u0435'}
                </Button>,
              ]
            : []),
          ...(isAppAdmin
            ? [
                <CompanyFormModal
                  key="create"
                  mode="create"
                  trigger={
                    <Button
                      type="primary"
                      icon={<PlusOutlined />}
                      data-onboarding-id="company-create"
                    >
                      Создать компанию
                    </Button>
                  }
                  onSuccess={() => {
                    void queryClient.invalidateQueries({ queryKey: ['companies'] });
                  }}
                />,
              ]
            : []),
        ]}
        columns={[
          { title: 'Название', dataIndex: 'name' },
          { title: 'Телефон', dataIndex: 'phone' },
          { title: 'Контактное лицо', dataIndex: 'contact_person_name' },
          { title: 'Участников', dataIndex: 'member_count', width: 120 },
          {
            title: 'Действия',
            valueType: 'option',
            render: (_, row) => {
              if (!canManageCompany(row.id)) return [];
              return [
                <a key="edit" onClick={() => pushParams({ edit: row.id })}>
                  Редактировать
                </a>,
                <a key="work-types" onClick={() => pushParams({ work_types: row.id })}>
                  Виды работ
                </a>,
                ...(isAppAdmin
                  ? [
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
                    ]
                  : []),
              ];
            },
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
          onDelete={
            isAppAdmin
              ? () => deleteCompany.mutateAsync(editCompany.id)
              : undefined
          }
          deleting={deleteCompany.isPending && deleteCompany.variables === editCompany.id}
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

      {onboardingPreviewStep === 'company-details' ? (
        <CompanyFormModal
          mode="create"
          open
          onboardingPreview
          onOpenChange={() => undefined}
        />
      ) : null}

      {onboardingPreviewStep === 'company-work-types' ? (
        <CompanyWorkTypesModal
          company={ONBOARDING_COMPANY}
          open
          onboardingPreview
          onOpenChange={() => undefined}
        />
      ) : null}
    </div>
  );
}
