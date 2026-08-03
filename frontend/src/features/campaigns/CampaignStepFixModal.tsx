import { Alert, Button, Modal, Space, Typography } from 'antd';
import type { FormInstance } from 'antd';
import { useNavigate } from 'react-router-dom';
import type { Audience, Campaign, DeliveryConnection, Recipient } from '@/api/types';
import {
  CAMPAIGN_WIZARD_STEP_TITLES,
  isLanguageIssue,
  isMappingRelatedMessage,
  type CampaignWizardStepIndex,
  type StepValidationState,
} from '@/features/campaigns/campaignStepValidation';
import { CampaignWizardBasicsStep } from '@/features/campaigns/wizard/CampaignWizardBasicsStep';
import { CampaignWizardRecipientsStep } from '@/features/campaigns/wizard/CampaignWizardRecipientsStep';
import { CampaignWizardScheduleStep } from '@/features/campaigns/wizard/CampaignWizardScheduleStep';
import { CampaignWizardSenderStep } from '@/features/campaigns/wizard/CampaignWizardSenderStep';
import type { ScheduleFormValues } from '@/utils/scheduleForm';
import './CampaignWizardSteps.css';

type Option = { label: string; value: string };

type Props = {
  open: boolean;
  step: CampaignWizardStepIndex;
  validation: StepValidationState;
  campaignId?: string;
  draft: Partial<Campaign>;
  linkedChainId?: string | null;
  basicsForm: FormInstance;
  senderForm: FormInstance;
  scheduleForm: FormInstance;
  chainOptions: Option[];
  companyOptions: Option[];
  workTypeOptions: Option[];
  selectedCompanyId?: string;
  chainsLoading: boolean;
  companiesLoading: boolean;
  workTypesLoading: boolean;
  isAppAdmin: boolean;
  isCompanyAdmin: boolean;
  mailboxes: DeliveryConnection[];
  audiences: Audience[];
  recipients: Recipient[];
  recipientsLoading?: boolean;
  scheduleInitialValues: ScheduleFormValues;
  batchCountPreview: number;
  estimatedDurationHours?: number;
  saving?: boolean;
  onClose: () => void;
  onSave: () => void | Promise<void>;
  onAutosave: (patch: Record<string, unknown>) => void;
  onAudienceSelect: (audienceId: string) => Promise<void>;
  onImportRecipients: (file: File) => Promise<void>;
  onOpenGenerate: () => void;
  onOpenTopup: () => void;
  onScheduleChange: (values: ScheduleFormValues) => void | Promise<void>;
  onOpenChainPreview?: () => void;
};

export function CampaignStepFixModal({
  open,
  step,
  validation,
  campaignId,
  draft,
  linkedChainId,
  basicsForm,
  senderForm,
  scheduleForm,
  chainOptions,
  companyOptions,
  workTypeOptions,
  selectedCompanyId,
  chainsLoading,
  companiesLoading,
  workTypesLoading,
  isAppAdmin,
  isCompanyAdmin,
  mailboxes,
  audiences,
  recipients,
  recipientsLoading,
  scheduleInitialValues,
  batchCountPreview,
  estimatedDurationHours,
  saving,
  onClose,
  onSave,
  onAutosave,
  onAudienceSelect,
  onImportRecipients,
  onOpenGenerate,
  onOpenTopup,
  onScheduleChange,
  onOpenChainPreview,
}: Props) {
  const navigate = useNavigate();

  return (
    <Modal
      open={open}
      title={`Исправление: ${CAMPAIGN_WIZARD_STEP_TITLES[step]}`}
      onCancel={onClose}
      width={720}
      footer={
        <Space>
          <Button onClick={onClose}>Закрыть</Button>
          <Button type="primary" loading={saving} onClick={() => void onSave()}>
            Сохранить
          </Button>
        </Space>
      }
    >
      <div className="campaign-step-fix-modal__issues">
        {validation.errors.length > 0 ? (
          <Alert
            type="error"
            showIcon
            message="Ошибки"
            description={
              <ul>
                {validation.errors.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            }
          />
        ) : null}
        {validation.warnings.length > 0 ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: validation.errors.length > 0 ? 12 : 0 }}
            message="Предупреждения"
            description={
              <ul>
                {validation.warnings.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            }
          />
        ) : null}
      </div>

      {step === 0 ? (
        <CampaignWizardBasicsStep
          form={basicsForm}
          draft={draft}
          chainOptions={chainOptions}
          companyOptions={companyOptions}
          workTypeOptions={workTypeOptions}
          selectedCompanyId={selectedCompanyId}
          linkedChainId={linkedChainId}
          campaignId={campaignId}
          chainsLoading={chainsLoading}
          companiesLoading={companiesLoading}
          workTypesLoading={workTypesLoading}
          isAppAdmin={isAppAdmin}
          isCompanyAdmin={isCompanyAdmin}
          onAutosave={onAutosave}
          onNavigateChain={() => linkedChainId && navigate(`/chains/${linkedChainId}`)}
          onNavigateChainsList={() => navigate('/chains')}
          onNavigateCompanies={() => navigate('/companies')}
        />
      ) : null}

      {step === 1 ? (
        <CampaignWizardSenderStep
          form={senderForm}
          draft={draft}
          mailboxes={mailboxes}
          onAutosave={onAutosave}
          onNavigateConnections={() => navigate('/connections')}
        />
      ) : null}

      {step === 2 ? (
        <>
          {validation.errors.some(isMappingRelatedMessage) ? (
            <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
              Окно сопоставления переменных откроется автоматически.
            </Typography.Paragraph>
          ) : null}
          <CampaignWizardRecipientsStep
            campaignId={campaignId}
            draft={draft}
            audiences={audiences}
            recipients={recipients}
            recipientsTotal={recipients.length}
            recipientsLoading={recipientsLoading}
            onAudienceSelect={onAudienceSelect}
            onImportRecipients={onImportRecipients}
            onOpenGenerate={onOpenGenerate}
            onOpenTopup={onOpenTopup}
          />
        </>
      ) : null}

      {step === 3 ? (
        <CampaignWizardScheduleStep
          form={scheduleForm}
          initialValues={scheduleInitialValues}
          batchCountPreview={batchCountPreview}
          estimatedDurationHours={estimatedDurationHours}
          onValuesChange={onScheduleChange}
        />
      ) : null}

      {step === 4 ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          {validation.templateIssues.map((issue, index) => (
            <div key={`${issue.token}-${index}`}>
              <Typography.Text strong>{issue.template_name || 'Шаблон'}</Typography.Text>
              <Typography.Paragraph type={!isLanguageIssue(issue) && issue.severity === 'error' ? 'danger' : undefined}>
                {issue.message}
              </Typography.Paragraph>
              {issue.fragment ? (
                <Typography.Text type="secondary">Фрагмент: {issue.fragment}</Typography.Text>
              ) : null}
              {issue.suggestion ? (
                <Typography.Paragraph>
                  Предложение: <Typography.Text code>{issue.suggestion}</Typography.Text>
                </Typography.Paragraph>
              ) : null}
              {issue.template_id ? (
                <Button type="link" onClick={() => navigate(`/templates/${issue.template_id}`)}>
                  Открыть шаблон
                </Button>
              ) : null}
            </div>
          ))}
          <Space wrap>
            {linkedChainId && onOpenChainPreview ? (
              <Button onClick={onOpenChainPreview}>Предпросмотр цепочки</Button>
            ) : null}
            {linkedChainId ? (
              <Button type="link" onClick={() => navigate(`/chains/${linkedChainId}`)}>
                Настроить цепочку
              </Button>
            ) : null}
          </Space>
        </Space>
      ) : null}
    </Modal>
  );
}
