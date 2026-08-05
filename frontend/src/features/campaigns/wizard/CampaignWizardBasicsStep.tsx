import {
  ProForm,
  ProFormSelect,
  ProFormText,
} from '@ant-design/pro-components';
import { Button, type FormInstance } from 'antd';
import type { Campaign } from '@/api/types';
import { buildCampaignChainSelectionPatch } from '@/features/campaigns/campaignQueryUtils';

type Option = { label: string; value: string };

type Props = {
  form: FormInstance;
  draft: Partial<Campaign>;
  chainOptions: Option[];
  companyOptions: Option[];
  workTypeOptions: Option[];
  selectedCompanyId?: string;
  linkedChainId?: string | null;
  campaignId?: string;
  chainsLoading: boolean;
  companiesLoading: boolean;
  workTypesLoading: boolean;
  isAppAdmin: boolean;
  isCompanyAdmin: boolean;
  onAutosave: (patch: Record<string, unknown>) => void;
  onNavigateChain: () => void;
  onNavigateChainsList: () => void;
  onNavigateCompanies: () => void;
};

export function CampaignWizardBasicsStep({
  form,
  draft,
  chainOptions,
  companyOptions,
  workTypeOptions,
  selectedCompanyId,
  linkedChainId,
  campaignId,
  chainsLoading,
  companiesLoading,
  workTypesLoading,
  isAppAdmin,
  isCompanyAdmin,
  onAutosave,
  onNavigateChain,
  onNavigateChainsList,
  onNavigateCompanies,
}: Props) {
  return (
    <ProForm
      form={form}
      submitter={false}
      initialValues={draft}
      onValuesChange={(changedValues, values) => {
        const chainSelectionChanged = Object.prototype.hasOwnProperty.call(
          changedValues,
          'email_chain_id',
        );
        const patch = chainSelectionChanged
          ? { ...values, ...buildCampaignChainSelectionPatch(changedValues.email_chain_id) }
          : values;
        onAutosave(patch);
      }}
    >
      <div data-onboarding-id="campaign-name">
        <ProFormText name="name" label="Название" rules={[{ required: true }]} />
      </div>
      <div data-onboarding-id="campaign-chain">
        <ProFormSelect
          name="email_chain_id"
          label="Цепочка писем (необязательно)"
          placeholder="Выберите цепочку"
          options={chainOptions}
          fieldProps={{
            allowClear: true,
            loading: chainsLoading,
          }}
        />
      </div>
      {campaignId && linkedChainId ? (
        <Button type="link" onClick={onNavigateChain}>
          Настроить цепочку писем
        </Button>
      ) : null}
      {campaignId && !linkedChainId && chainOptions.length === 0 && !chainsLoading ? (
        <Button type="link" onClick={onNavigateChainsList}>
          Создать цепочку
        </Button>
      ) : null}
      <div data-onboarding-id="campaign-company">
        <ProFormSelect
          name="company_id"
          label="Компания (необязательно)"
          placeholder="Выберите компанию"
          options={companyOptions}
          fieldProps={{
            loading: companiesLoading,
            onChange: (value: string) => {
              form.setFieldsValue({
                company_id: value,
                company_work_type_id: undefined,
                work_type_name: undefined,
              });
              onAutosave({
                company_id: value,
                company_work_type_id: '',
                work_type_name: '',
              });
            },
          }}
        />
      </div>
      <div data-onboarding-id="campaign-work-type">
        <ProFormSelect
          name="company_work_type_id"
          label="Вид работ (необязательно)"
          placeholder={
            selectedCompanyId
              ? workTypeOptions.length > 0
                ? 'Выберите вид работ'
                : 'Для компании пока нет видов работ'
              : 'Сначала выберите компанию'
          }
          options={workTypeOptions}
          fieldProps={{
            disabled: !selectedCompanyId,
            loading: workTypesLoading,
            onChange: (value: string) => {
              const item = workTypeOptions.find((option) => option.value === value);
              const patch = {
                company_work_type_id: value,
                work_type_name: item?.label || '',
              };
              form.setFieldsValue(patch);
              onAutosave(patch);
            },
          }}
        />
      </div>
      {(isAppAdmin || isCompanyAdmin) && selectedCompanyId ? (
        <Button type="link" onClick={onNavigateCompanies}>
          Настроить виды работ
        </Button>
      ) : null}
    </ProForm>
  );
}
