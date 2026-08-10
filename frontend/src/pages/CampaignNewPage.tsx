import {
  ProCard,
} from '@ant-design/pro-components';
import { ReloadOutlined, SwapOutlined } from '@ant-design/icons';
import { App, Alert, Button, Col, Collapse, Form, Row, Space, Spin, Steps, Tag, Typography } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';
import { ApiError } from '@/api/client';
import { chainsApi } from '@/api/chains';
import { companiesApi } from '@/api/companies';
import { connectionsApi } from '@/api/connections';
import { audiencesApi } from '@/api/audiences';
import type { Campaign } from '@/api/types';
import { CampaignDocumentLayoutReview } from '@/features/campaigns/CampaignDocumentLayoutReview';
import { CampaignStepFixModal } from '@/features/campaigns/CampaignStepFixModal';
import {
  buildCampaignStepValidation,
  CAMPAIGN_WIZARD_STEP_TITLES,
  isMappingRelatedMessage,
  type CampaignWizardStepIndex,
} from '@/features/campaigns/campaignStepValidation';
import {
  useActiveOnboardingStep,
} from '@/features/onboarding/events';
import '@/features/campaigns/CampaignWizardSteps.css';
import { RecipientGenerateModal } from '@/features/campaigns/RecipientGenerateModal';
import { ChainEmailPreviewModal } from '@/features/campaigns/ChainEmailPreviewModal';
import { buildCampaignReturnState } from '@/features/campaigns/campaignNavigation';
import {
  buildCampaignAutosavePayload,
  buildMappingInputsSignature,
  buildValidationSignature,
  campaignValidateQueryKey,
  invalidateCampaignDerivedData,
  invalidateCampaignMappingCache,
  resolveLinkedChainId,
  showAutoFixResultMessage,
} from '@/features/campaigns/campaignQueryUtils';
import { ValidationAutoFixButton } from '@/features/campaigns/ValidationAutoFixButton';
import { useCampaignLaunchValidation } from '@/features/campaigns/useCampaignLaunchValidation';
import { useCampaignMappingAutoSuggest } from '@/features/campaigns/useCampaignMappingAutoSuggest';
import { VariableMappingModal } from '@/features/campaigns/VariableMappingModal';
import { CampaignWizardBasicsStep } from '@/features/campaigns/wizard/CampaignWizardBasicsStep';
import { CampaignWizardRecipientsStep } from '@/features/campaigns/wizard/CampaignWizardRecipientsStep';
import { CampaignWizardScheduleStep } from '@/features/campaigns/wizard/CampaignWizardScheduleStep';
import { CampaignWizardSenderStep } from '@/features/campaigns/wizard/CampaignWizardSenderStep';
import { usePermissions } from '@/hooks/usePermissions';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';
import { useAuthStore } from '@/stores/authStore';
import { readIntParam } from '@/utils/urlState';
import { validateCampaignBasics } from '@/utils/validators';
import {
  formValuesToSchedulePayload,
  scheduleToFormValues,
} from '@/utils/scheduleForm';
import { computeLocalSchedulePreview } from '@/utils/schedulePreview';

function draftBasicsFields(draft: Partial<Campaign>) {
  const payload = draft.draft_payload || {};
  return {
    company_id: draft.company_id || payload.company_id,
    company_work_type_id: draft.company_work_type_id || payload.company_work_type_id,
    work_type_name: draft.work_type_name || payload.work_type_name,
  };
}

const CAMPAIGN_MODAL_KEYS = ['modal', 'fix_step', 'preview_node'] as const;

const ONBOARDING_CAMPAIGN_STEPS: Record<string, number> = {
  'campaign-basics': 0,
  'campaign-name': 0,
  'campaign-chain': 0,
  'campaign-company': 0,
  'campaign-work-type': 0,
  'campaign-sender': 1,
  'campaign-sender-connection': 1,
  'campaign-recipients': 2,
  'campaign-audience': 2,
  'campaign-recipient-sources': 2,
  'campaign-recipient-check': 2,
  'campaign-schedule': 3,
  'campaign-batch-size': 3,
  'campaign-start-at': 3,
  'campaign-interval': 3,
  'campaign-schedule-preview': 3,
  'campaign-launch': 4,
  'campaign-launch-checks': 4,
  'campaign-test-email': 4,
  'campaign-start': 4,
};

type CampaignModalKind = 'generate' | 'topup' | 'mapping' | 'preview' | 'layout' | 'fix';

export function CampaignNewPage() {
  const [params] = useSearchParams();
  const location = useLocation();
  const { pushParams, replaceParams } = useUrlNavigation();
  const requestedId = params.get('id');
  const isExplicitNew = params.get('new') === '1';
  const isOnboardingPreview = params.get('onboarding') === '1';
  const emailChainIdParam = params.get('email_chain_id');
  const step = readIntParam(params, 'step', 0, 0, 4);
  const modalKind = params.get('modal') as CampaignModalKind | null;
  const generateModalOpen = modalKind === 'generate' || modalKind === 'topup';
  const mappingModalOpen = modalKind === 'mapping';
  const chainPreviewOpen = modalKind === 'preview';
  const layoutReviewOpen = modalKind === 'layout';
  const fixModalStep =
    modalKind === 'fix'
      ? (readIntParam(params, 'fix_step', 0, 0, 3) as CampaignWizardStepIndex)
      : null;
  const previewNodeId = params.get('preview_node');
  const navigate = useNavigate();
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const {
    campaignId,
    draft,
    setCampaignId,
    queueDraftPatch,
    acknowledgeDraftPatch,
    clearPendingPatch,
    replaceDraft,
    saveState,
    setSaveState,
    reset: resetDraftState,
  } = useCampaignDraftStore();
  const existingId = requestedId || (!isExplicitNew ? campaignId : null);
  const [basicsForm] = Form.useForm();
  const [senderForm] = Form.useForm();
  const [scheduleForm] = Form.useForm();
  const [fixModalSaving, setFixModalSaving] = useState(false);
  const [launchBusy, setLaunchBusy] = useState<{ active: boolean; label: string; progress: number }>({
    active: false,
    label: '',
    progress: 0,
  });
  const { isAppAdmin, isCompanyAdmin } = usePermissions();
  const user = useAuthStore((s) => s.user);
  const debounceRef = useRef<number | null>(null);
  const createRequestedRef = useRef(false);
  const hydratedIdRef = useRef<string | null>(null);
  const loadRequestRef = useRef(0);
  const companyAutoSetRef = useRef<string | null>(null);
  const persistQueueRef = useRef<Promise<void>>(Promise.resolve());
  const persistRequestRef = useRef(0);
  const suppressAutosaveRef = useRef(false);
  const activeOnboardingStep = useActiveOnboardingStep();

  const setWizardStep = useCallback(
    (nextStep: number) => {
      pushParams({ step: nextStep > 0 ? String(nextStep) : null });
    },
    [pushParams],
  );

  useEffect(() => {
    const campaignStep = activeOnboardingStep
      ? ONBOARDING_CAMPAIGN_STEPS[activeOnboardingStep]
      : undefined;
    if (campaignStep !== undefined) setWizardStep(campaignStep);
  }, [activeOnboardingStep, setWizardStep]);

  const openWizardModal = useCallback(
    (kind: CampaignModalKind, extra: Record<string, string | null | undefined> = {}) => {
      pushParams({ modal: kind, ...extra });
    },
    [pushParams],
  );

  const closeWizardModal = useCallback(() => {
    pushParams({}, [...CAMPAIGN_MODAL_KEYS]);
  }, [pushParams]);


  useEffect(() => {
    if (isOnboardingPreview || isExplicitNew || requestedId || !campaignId) return;
    navigate(`/campaigns/new?id=${campaignId}`, { replace: true });
  }, [campaignId, isExplicitNew, isOnboardingPreview, navigate, requestedId]);

  const id = isOnboardingPreview ? undefined : (existingId || undefined);

  const createMutation = useMutation({
    mutationFn: () => campaignsApi.create({ name: 'Черновик рассылки' }),
    onSuccess: (camp) => {
      setCampaignId(camp.id);
      replaceDraft(camp);
      void queryClient.invalidateQueries({ queryKey: ['campaigns', 'draft'] });
      navigate(`/campaigns/new?id=${camp.id}`, { replace: true });
    },
    onError: (error: Error) => {
      createRequestedRef.current = false;
      message.error(error.message || 'Не удалось создать черновик рассылки');
    },
  });

  useEffect(() => {
    if (isOnboardingPreview) return;

    const requestId = ++loadRequestRef.current;
    hydratedIdRef.current = null;
    companyAutoSetRef.current = null;

    if (!existingId) {
      if (createRequestedRef.current) return;
      createRequestedRef.current = true;
      resetDraftState();
      createMutation.mutate();
      return;
    }

    createRequestedRef.current = false;
    if (campaignId !== existingId) {
      resetDraftState();
    }
    setCampaignId(existingId);

    void campaignsApi
      .get(existingId)
      .then((camp) => {
        if (requestId !== loadRequestRef.current) return;
        if (camp.status !== 'draft') {
          resetDraftState();
          message.warning('Эта рассылка уже запускалась. Создайте её копию для повторной отправки.');
          navigate(`/campaigns/${existingId}`, { replace: true });
          return;
        }
        setCampaignId(existingId);
        replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
      })
      .catch((error: unknown) => {
        if (requestId !== loadRequestRef.current) return;
        resetDraftState();
        message.error(error instanceof Error ? error.message : 'Не удалось загрузить черновик рассылки');
        navigate('/campaigns', { replace: true });
      });
    // createMutation is deliberately guarded by createRequestedRef for React StrictMode.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existingId, isOnboardingPreview]);

  useEffect(() => {
    return () => {
      loadRequestRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (!id || !emailChainIdParam) return;
    void campaignsApi
      .update(id, { send_scenario: 'email_chain', email_chain_id: emailChainIdParam })
      .then((camp) => {
        replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
        basicsForm.setFieldsValue({ email_chain_id: emailChainIdParam, send_scenario: 'email_chain' });
        invalidateCampaignMappingCache(queryClient, id);
        void queryClient.invalidateQueries({ queryKey: campaignValidateQueryKey(id) });
        navigate(`/campaigns/new?id=${id}`, { replace: true });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, emailChainIdParam]);

  const watchedChainId = Form.useWatch('email_chain_id', basicsForm);
  const watchedBasics = Form.useWatch([], basicsForm);
  const draftForValidation = useMemo(
    () => ({ ...draft, ...draftBasicsFields(draft), ...(watchedBasics || {}) }),
    [draft, watchedBasics],
  );

  const linkedChainId = resolveLinkedChainId(watchedChainId, draft.email_chain_id);

  useEffect(() => {
    if (!id) return;
    if (hydratedIdRef.current === id) return;
    if (!draft.id && draft.name === undefined) return;
    basicsForm.setFieldsValue({ ...draft, ...draftBasicsFields(draft) });
    senderForm.setFieldsValue(draft);
    hydratedIdRef.current = id;
    companyAutoSetRef.current = null;
  }, [id, draft.id, draft.name, draft.smtp_mailbox_id, draft, basicsForm, senderForm]);

  const companiesQuery = useQuery({
    queryKey: ['companies'],
    queryFn: () => companiesApi.list(),
  });
  const myCompanyQuery = useQuery({
    queryKey: ['companies', 'me'],
    queryFn: () => companiesApi.getMe(),
    enabled: !isAppAdmin && !user?.company?.id,
  });

  const selectedCompanyId = draftForValidation.company_id;

  const workTypesQuery = useQuery({
    queryKey: ['company-work-types', selectedCompanyId],
    queryFn: () => companiesApi.workTypes.list(selectedCompanyId!),
    enabled: Boolean(selectedCompanyId),
  });

  const mailboxesQuery = useQuery({ queryKey: ['connections'], queryFn: () => connectionsApi.list() });
  const chainsQuery = useQuery({ queryKey: ['chains'], queryFn: () => chainsApi.list({ limit: 100 }) });
  const audiencesQuery = useQuery({ queryKey: ['audiences'], queryFn: () => audiencesApi.list() });
  const recipientsQuery = useQuery({
    queryKey: ['campaign-recipients', id],
    queryFn: () => campaignsApi.recipients(id!, { limit: 100 }),
    enabled: Boolean(id),
  });
  const scheduleQuery = useQuery({
    queryKey: ['campaign-schedule', id],
    queryFn: () => campaignsApi.getSchedule(id!),
    enabled: Boolean(id),
  });

  useEffect(() => {
    if (!id) return;
    const refreshFromExternalEdits = () => {
      if (document.visibilityState !== 'visible') return;
      if (suppressAutosaveRef.current) return;
      invalidateCampaignDerivedData(queryClient, id);
      void queryClient.invalidateQueries({ queryKey: ['chains'] });
      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
      void campaignsApi.get(id).then((camp) => {
        replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
      });
    };
    window.addEventListener('focus', refreshFromExternalEdits);
    document.addEventListener('visibilitychange', refreshFromExternalEdits);
    return () => {
      window.removeEventListener('focus', refreshFromExternalEdits);
      document.removeEventListener('visibilitychange', refreshFromExternalEdits);
    };
  }, [id, queryClient, replaceDraft]);

  const recipientCount = recipientsQuery.data?.total || 0;

  const templateIds = useMemo(
    () => ({
      email: draft.email_template_id,
      kp: draft.kp_template_id,
      contract: draft.contract_template_id,
    }),
    [draft.contract_template_id, draft.email_template_id, draft.kp_template_id],
  );

  const draftMappingConfirmed = Boolean(
    (draft.draft_payload as Record<string, unknown> | undefined)?.mapping_confirmed,
  );
  const draftMappingConfirmedAt = String(
    (draft.draft_payload as Record<string, unknown> | undefined)?.mapping_confirmed_at || '',
  );

  const mappingInputsSignature = useMemo(
    () =>
      buildMappingInputsSignature({
        recipientCount,
        emailChainId: draft.email_chain_id,
        mappingConfirmed: draftMappingConfirmed,
        templateIds,
      }),
    [draft.email_chain_id, draftMappingConfirmed, recipientCount, templateIds],
  );

  const validationSignature = useMemo(
    () =>
      buildValidationSignature({
        recipientCount,
        emailChainId: draft.email_chain_id,
        mappingConfirmed: draftMappingConfirmed,
        mappingConfirmedAt: draftMappingConfirmedAt,
        companyId: draftForValidation.company_id,
        companyWorkTypeId: draftForValidation.company_work_type_id,
        smtpMailboxId: draft.smtp_mailbox_id,
        audienceId: draft.audience_id,
        templateIds,
      }),
    [
      draft.audience_id,
      draft.email_chain_id,
      draft.smtp_mailbox_id,
      draftMappingConfirmed,
      draftMappingConfirmedAt,
      recipientCount,
      draftForValidation.company_id,
      draftForValidation.company_work_type_id,
      templateIds,
    ],
  );

  const invalidateMappingAndValidation = useCallback(
    (campaignId: string) => {
      invalidateCampaignDerivedData(queryClient, campaignId, {
        includeMapping: true,
        includeValidation: true,
        includeEmailValidation: true,
      });
    },
    [queryClient],
  );
  const chainOptions = (chainsQuery.data?.items ?? []).map((chain) => ({
    label: chain.name,
    value: chain.id,
  }));

  const companyOptions = useMemo(() => {
    // The company list is common to all users (read-only): everyone can pick
    // any company here, not just their own.
    if (companiesQuery.data?.items?.length) {
      return companiesQuery.data.items.map((company) => ({
        label: company.name,
        value: company.id,
      }));
    }
    const company = user?.company || myCompanyQuery.data;
    return company ? [{ label: company.name, value: company.id }] : [];
  }, [companiesQuery.data?.items, user?.company, myCompanyQuery.data]);

  const workTypeOptions = (workTypesQuery.data ?? []).map((item) => ({
    label: item.name,
    value: item.id,
  }));

  const selectedWorkTypeId = draftBasicsFields(draftForValidation).company_work_type_id;
  const selectedCompanyLabel =
    companyOptions.find((item) => item.value === selectedCompanyId)?.label || 'не выбрана';
  const selectedWorkTypeLabel =
    (selectedWorkTypeId &&
      (workTypeOptions.find((item) => item.value === selectedWorkTypeId)?.label ||
        draftBasicsFields(draftForValidation).work_type_name)) ||
    'не выбран';

  const persist = useCallback(
    async (patch: Record<string, unknown>) => {
      if (!id) return;
      setSaveState('saving');
      const requestId = ++persistRequestRef.current;
      try {
        const request = persistQueueRef.current.then(() =>
          campaignsApi.update(id, buildCampaignAutosavePayload(patch)),
        );
        persistQueueRef.current = request.then(() => undefined, () => undefined);
        const updated = await request;
        if (useCampaignDraftStore.getState().campaignId !== id) return;
        acknowledgeDraftPatch(patch);
        if (requestId === persistRequestRef.current) {
          replaceDraft({ ...updated, ...(updated.draft_payload || {}) });
          const hasPending = Object.keys(useCampaignDraftStore.getState().pendingPatch).length > 0;
          setSaveState(hasPending ? 'idle' : 'saved');
        }
      } catch (error) {
        if (
          requestId === persistRequestRef.current
          && useCampaignDraftStore.getState().campaignId === id
        ) {
          setSaveState('error');
        }
        throw error;
      }
    },
    [acknowledgeDraftPatch, id, replaceDraft, setSaveState],
  );

  const flushPendingChanges = useCallback(async () => {
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    await persistQueueRef.current;
    const patch = useCampaignDraftStore.getState().pendingPatch;
    if (Object.keys(patch).length > 0) {
      await persist(patch);
    }
  }, [persist]);

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      const state = useCampaignDraftStore.getState();
      if (Object.keys(state.pendingPatch).length === 0 && state.saveState !== 'saving') return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      void flushPendingChanges().catch(() => undefined);
    };
  }, [flushPendingChanges]);

  const navigateAfterDraftSave = useCallback(
    async (to: string, options?: { replace?: boolean; state?: unknown }) => {
      try {
        await flushPendingChanges();
        navigate(to, options);
      } catch {
        message.error('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f. \u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u0435 \u043f\u0435\u0440\u0435\u0445\u043e\u0434 \u043f\u043e\u0441\u043b\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f.');
      }
    },
    [flushPendingChanges, message, navigate],
  );

  const launchValidation = useCampaignLaunchValidation({
    campaignId: id ?? undefined,
    step,
    validationSignature,
    flushPendingChanges,
    queryClient,
  });

  const handleMappingConfirmed = useCallback(async () => {
    if (!id) return;
    await queryClient.cancelQueries({ queryKey: campaignValidateQueryKey(id) });
    queryClient.removeQueries({ queryKey: campaignValidateQueryKey(id) });
    invalidateCampaignMappingCache(queryClient, id);
    const camp = await campaignsApi.get(id);
    replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
    message.success('Сопоставление переменных сохранено. Перепроверяем рассылку');
  }, [id, message, queryClient, replaceDraft]);

  const autosave = (patch: Record<string, unknown>) => {
    if (suppressAutosaveRef.current) return;
    queueDraftPatch(patch);
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      const pendingPatch = useCampaignDraftStore.getState().pendingPatch;
      if (Object.keys(pendingPatch).length > 0) {
        void persist(pendingPatch).catch(() => undefined);
      }
    }, 700);
  };

  useEffect(() => {
    if (!id) return;
    const restoredPatch = useCampaignDraftStore.getState().pendingPatch;
    if (Object.keys(restoredPatch).length === 0) return;
    const timeout = window.setTimeout(() => {
      const pendingPatch = useCampaignDraftStore.getState().pendingPatch;
      if (Object.keys(pendingPatch).length > 0) {
        void persist(pendingPatch).catch(() => undefined);
      }
    }, 50);
    return () => window.clearTimeout(timeout);
  }, [id, persist]);


  useEffect(() => {
    if (!id || hydratedIdRef.current !== id) return;
    if (draftBasicsFields(draft).company_id) return;
    if (companyAutoSetRef.current === id) return;
    const defaultCompanyId = isAppAdmin
      ? undefined
      : user?.company_id || user?.company?.id || myCompanyQuery.data?.id;
    if (!defaultCompanyId) return;
    companyAutoSetRef.current = id;
    basicsForm.setFieldValue('company_id', defaultCompanyId);
    autosave({ company_id: defaultCompanyId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, isAppAdmin, user, myCompanyQuery.data, draft.company_id, draft.draft_payload]);

  const schedule = scheduleQuery.data;
  const scheduleSyncedIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!schedule || !id) return;
    if (scheduleSyncedIdRef.current && scheduleSyncedIdRef.current !== id) {
      scheduleSyncedIdRef.current = null;
    }
    const formValues = scheduleToFormValues(schedule);
    scheduleForm.setFieldsValue(formValues);
    const payload = formValuesToSchedulePayload(formValues);
    if (!payload) return;
    const needsSync =
      schedule.send_immediately ||
      !schedule.start_at ||
      schedule.interval_seconds !== payload.interval_seconds;
    if (!needsSync) {
      scheduleSyncedIdRef.current = id;
      return;
    }
    if (scheduleSyncedIdRef.current === id) return;
    scheduleSyncedIdRef.current = id;
    void campaignsApi.putSchedule(id, payload).then(() => {
      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
    });
  }, [schedule, scheduleForm, id, queryClient]);

  const watchedSchedule = Form.useWatch([], scheduleForm);
  const schedulePreview = useMemo(() => {
    const payload = formValuesToSchedulePayload(watchedSchedule || scheduleToFormValues(schedule));
    return computeLocalSchedulePreview({
      recipientCount: recipientsQuery.data?.total || 0,
      batchSize: payload?.batch_size || schedule?.batch_size || 25,
      intervalSeconds: payload?.interval_seconds || schedule?.interval_seconds || 3600,
    });
  }, [watchedSchedule, schedule, recipientsQuery.data?.total]);
  const batchCountPreview = schedulePreview.batchCount;
  const scheduleInitialValues = scheduleToFormValues(schedule);

  const stepValidation = useMemo(
    () =>
      buildCampaignStepValidation({
        draft: draftForValidation,
        validate: launchValidation.hasChecked ? launchValidation.data : undefined,
        scheduleValues: watchedSchedule || scheduleInitialValues,
      }),
    [draftForValidation, launchValidation.hasChecked, launchValidation.data, watchedSchedule, scheduleInitialValues],
  );

  const autoFixMutation = useMutation({
    mutationFn: () => campaignsApi.autoFixValidation(id!),
    onSuccess: (result) => {
      if (id) {
        queryClient.setQueryData(campaignValidateQueryKey(id), result.validation);
        invalidateCampaignDerivedData(queryClient, id);
        void campaignsApi.get(id).then((camp) => {
          replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
        });
      }
      showAutoFixResultMessage(result, message);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось выполнить автоисправление');
    },
  });

  const resetMutation = useMutation({
    mutationFn: () => campaignsApi.reset(id!),
    onSuccess: async (camp) => {
      if (!id) return;
      replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
      replaceParams({}, ['step', ...CAMPAIGN_MODAL_KEYS]);
      hydratedIdRef.current = null;
      companyAutoSetRef.current = null;
      scheduleSyncedIdRef.current = null;
      basicsForm.resetFields();
      senderForm.resetFields();
      scheduleForm.resetFields();
      const scheduleData = await campaignsApi.getSchedule(id);
      scheduleForm.setFieldsValue(scheduleToFormValues(scheduleData));
      basicsForm.setFieldsValue({ ...camp, ...draftBasicsFields(camp) });
      senderForm.setFieldsValue(camp);
      queryClient.removeQueries({ queryKey: campaignValidateQueryKey(id) });
      invalidateCampaignDerivedData(queryClient, id, { includeMapping: true });
      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
      message.success('Все поля очищены');
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось очистить рассылку');
    },
  });

  const runReset = async () => {
    if (!id) return;
    clearPendingPatch();
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    suppressAutosaveRef.current = true;
    try {
      await resetMutation.mutateAsync();
    } finally {
      suppressAutosaveRef.current = false;
    }
  };

  const handleClearCampaign = () => {
    if (!id) return;
    modal.confirm({
      title: 'Очистить все поля и начать заново?',
      okText: 'Очистить',
      cancelText: 'Отмена',
      okButtonProps: { danger: true },
      onOk: () => runReset(),
    });
  };

  const handleWizardStepClick = useCallback(
    (nextStep: number) => {
      const validation = stepValidation[nextStep as CampaignWizardStepIndex];
      if (validation?.status === 'error' || validation?.status === 'warning') {
        openWizardModal('fix', { fix_step: String(nextStep) });
        return;
      }
      setWizardStep(nextStep);
    },
    [openWizardModal, setWizardStep, stepValidation],
  );

  const handleFixModalSave = async () => {
    if (fixModalStep === null) return;
    setFixModalSaving(true);
    try {
      if (fixModalStep === 0) {
        await basicsForm.validateFields();
        await persist(basicsForm.getFieldsValue());
      } else if (fixModalStep === 1) {
        await senderForm.validateFields();
        await persist(senderForm.getFieldsValue());
      } else if (fixModalStep === 3) {
        await scheduleForm.validateFields();
        const payload = formValuesToSchedulePayload(scheduleForm.getFieldsValue());
        if (id && payload) {
          await campaignsApi.putSchedule(id, payload);
          void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
        }
      }
      closeWizardModal();
      message.success('Изменения сохранены');
    } catch {
      message.error('Заполните обязательные поля');
    } finally {
      setFixModalSaving(false);
    }
  };

  const readinessErrors = [
    ...validateCampaignBasics(draftForValidation),
    ...(launchValidation.hasChecked ? launchValidation.data?.errors || [] : []),
  ];
  const readinessWarnings = launchValidation.hasChecked ? launchValidation.data?.warnings || [] : [];
  const mappingConfirmed = Boolean(
    launchValidation.hasChecked
      ? launchValidation.data?.mapping_confirmed
      : (draft.draft_payload as Record<string, unknown> | undefined)?.mapping_confirmed,
  );

  const refreshDraft = useCallback(async () => {
    if (!id) return;
    const camp = await campaignsApi.get(id);
    replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
  }, [id, replaceDraft]);

  const handleMappingAutoSaved = useCallback(() => {
    message.success('Сопоставление заполнено автоматически');
  }, [message]);

  const handleMappingNeedsReview = useCallback(() => {
    if (mappingModalOpen) return;
    openWizardModal('mapping');
  }, [mappingModalOpen, openWizardModal]);

  const mappingAutoSuggest = useCampaignMappingAutoSuggest({
    campaignId: id ?? undefined,
    step,
    recipientCount,
    mappingConfirmed,
    mappingInputsSignature,
    queryClient,
    onDraftRefresh: refreshDraft,
    onAutoSaved: handleMappingAutoSaved,
    onNeedsReview: handleMappingNeedsReview,
  });

  const mappingStep4PromptedRef = useRef(false);
  const launchMappingErrors = useMemo(
    () => (launchValidation.data?.errors || []).filter(isMappingRelatedMessage),
    [launchValidation.data?.errors],
  );

  useEffect(() => {
    if (mappingConfirmed) {
      mappingStep4PromptedRef.current = false;
    }
  }, [mappingConfirmed]);

  useEffect(() => {
    if (
      step !== 4 ||
      !id ||
      mappingConfirmed ||
      !launchValidation.hasChecked ||
      launchMappingErrors.length === 0 ||
      mappingModalOpen
    ) {
      return;
    }
    if (mappingStep4PromptedRef.current) {
      return;
    }
    mappingStep4PromptedRef.current = true;
    openWizardModal('mapping');
  }, [
    id,
    launchMappingErrors.length,
    launchValidation.hasChecked,
    mappingConfirmed,
    mappingModalOpen,
    openWizardModal,
    step,
  ]);

  const launchBlocked =
    readinessErrors.length > 0 ||
    launchValidation.isChecking ||
    (step === 4 && !launchValidation.hasChecked);
  const showAiFixButton = launchValidation.hasChecked && readinessErrors.length > 0;
  const wizardLocked = launchBusy.active || launchValidation.isChecking;

  const formatLaunchValidationError = (err: unknown): string => {
    if (err instanceof ApiError) {
      return [err.detail, err.payload.hint].filter(Boolean).join(' ');
    }
    if (err instanceof TypeError) {
      return 'Не удалось подключиться к серверу для проверки рассылки. Проверьте соединение и повторите попытку.';
    }
    if (err instanceof Error && err.message) {
      return err.message;
    }
    return 'Не удалось выполнить проверку';
  };

  const runLaunchAction = async (label: string, action: () => Promise<void>) => {
    setLaunchBusy({ active: true, label, progress: 50 });
    try {
      await action();
    } catch (err) {
      const detail =
        err instanceof ApiError
          ? [err.detail, err.payload.hint].filter(Boolean).join(' ')
          : err instanceof Error
            ? err.message
            : 'Не удалось выполнить действие';
      message.error(detail);
      if (
        err instanceof ApiError &&
        err.payload.code === 'campaign_not_draft' &&
        err.payload.campaign_id
      ) {
        resetDraftState();
        navigate(`/campaigns/${err.payload.campaign_id}`, { replace: true });
      }
    } finally {
      setLaunchBusy({ active: false, label: '', progress: 0 });
    }
  };

  const navigateAfterLaunch = async (campaignId: string, successMessage: string) => {
    message.success(successMessage);
    resetDraftState();
    try {
      const batches = await campaignsApi.batches(campaignId);
      const hasErrors = (batches || []).some(
        (batch) =>
          batch.status === 'completed_with_errors' ||
          batch.status === 'failed' ||
          (batch.error_count ?? 0) > 0,
      );
      navigate(hasErrors ? `/campaigns/${campaignId}?tab=errors` : `/campaigns/${campaignId}`);
    } catch {
      navigate(`/campaigns/${campaignId}`);
    }
  };
  const campaignReturnState = id
    ? buildCampaignReturnState(id, location.pathname, location.search)
    : undefined;


  return (
    <div style={{ position: 'relative' }}>
      <Row gutter={16} className={wizardLocked ? 'campaign-wizard--locked' : undefined}>
      <Col xs={24} xl={16}>
        <ProCard
          bordered
          title="Создание рассылки"
          extra={
            <Space>
              {id ? (
                <Button
                  danger
                  loading={resetMutation.isPending}
                  disabled={wizardLocked}
                  onClick={handleClearCampaign}
                >
                  Очистить
                </Button>
              ) : null}
              {showAiFixButton && id ? (
                <ValidationAutoFixButton
                  type="primary"
                  ghost
                  loading={autoFixMutation.isPending}
                  onClick={() => autoFixMutation.mutate()}
                />
              ) : null}
              <Tag>{saveState === 'saving' ? 'Сохранение…' : saveState === 'saved' ? 'Сохранено' : 'Черновик'}</Tag>
              {saveState === 'error' ? (
                <Button
                  type="link"
                  danger
                  onClick={() => {
                    void flushPendingChanges().catch(() => message.error('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f'));
                  }}
                >
                  {'\u041d\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e \u2014 \u043f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u044c'}
                </Button>
              ) : null}
            </Space>
          }
        >
          <Steps
            className="campaign-wizard-steps"
            current={step}
            onChange={wizardLocked ? undefined : handleWizardStepClick}
            items={CAMPAIGN_WIZARD_STEP_TITLES.map((title, index) => {
              const validation = stepValidation[index];
              const antStatus =
                validation.status === 'error'
                  ? 'error'
                  : validation.status === 'warning'
                    ? 'finish'
                    : index < step
                      ? 'finish'
                      : index === step
                        ? 'process'
                        : 'wait';
              return {
                title,
                status: antStatus as 'error' | 'finish' | 'process' | 'wait',
                className: [
                  validation.status === 'warning' ? 'campaign-step--warning' : '',
                  validation.status !== 'ok' ? 'campaign-step--clickable' : '',
                ]
                  .filter(Boolean)
                  .join(' '),
              };
            })}
            style={{ marginBottom: 24 }}
          />

          <Collapse
            data-onboarding-id="campaign-wizard"
            accordion
            activeKey={String(step)}
            collapsible={wizardLocked ? 'disabled' : undefined}
            onChange={(key) => {
              if (wizardLocked) return;
              const nextKey = Array.isArray(key) ? key[0] : key;
              if (nextKey !== undefined && nextKey !== '') {
                setWizardStep(Number(nextKey));
              }
            }}
            items={[
              {
                key: '0',
                label: (
                  <span data-onboarding-id="campaign-step-basics-label">
                    Основная информация
                  </span>
                ),
                children: fixModalStep === 0 ? null : (
                  <div data-onboarding-id="campaign-step-basics">
                    <CampaignWizardBasicsStep
                    form={basicsForm}
                    draft={draft}
                    chainOptions={chainOptions}
                    companyOptions={companyOptions}
                    workTypeOptions={workTypeOptions}
                    selectedCompanyId={selectedCompanyId}
                    linkedChainId={linkedChainId ?? undefined}
                    campaignId={id ?? undefined}
                    chainsLoading={chainsQuery.isLoading}
                    companiesLoading={companiesQuery.isLoading}
                    workTypesLoading={workTypesQuery.isLoading}
                    isAppAdmin={isAppAdmin}
                    isCompanyAdmin={isCompanyAdmin}
                    onAutosave={autosave}
                    onNavigateChain={() => {
                      if (linkedChainId) {
                        void navigateAfterDraftSave(`/chains/${linkedChainId}`, {
                          state: campaignReturnState,
                        });
                      }
                    }}
                    onNavigateChainsList={() => {
                      void navigateAfterDraftSave('/chains', { state: campaignReturnState });
                    }}
                    onNavigateCompanies={() => {
                      void navigateAfterDraftSave('/companies', { state: campaignReturnState });
                    }}
                    />
                  </div>
                ),
              },
              {
                key: '1',
                label: (
                  <span data-onboarding-id="campaign-step-sender-label">
                    Отправитель
                  </span>
                ),
                children: fixModalStep === 1 ? null : (
                  <div data-onboarding-id="campaign-step-sender">
                    <CampaignWizardSenderStep
                    form={senderForm}
                    draft={draft}
                    mailboxes={mailboxesQuery.data || []}
                    onAutosave={autosave}
                    onNavigateConnections={() => {
                      void navigateAfterDraftSave('/connections', { state: campaignReturnState });
                    }}
                    />
                  </div>
                ),
              },
              {
                key: '2',
                label: (
                  <span data-onboarding-id="campaign-step-recipients-label">
                    Получатели
                  </span>
                ),
                children: (
                  <div data-onboarding-id="campaign-step-recipients">
                    <CampaignWizardRecipientsStep
                    campaignId={id ?? undefined}
                    draft={draft}
                    audiences={audiencesQuery.data || []}
                    recipients={recipientsQuery.data?.items || []}
                    recipientsTotal={recipientsQuery.data?.total || 0}
                    recipientsLoading={recipientsQuery.isFetching}
                    onAudienceSelect={async (audienceId) => {
                      if (!id) return;
                      await audiencesApi.useInCampaign(audienceId, id);
                      await persist({ audience_id: audienceId });
                      invalidateMappingAndValidation(id);
                      message.success('Аудитория загружена');
                    }}
                    onImportRecipients={async (file) => {
                      if (!id) return;
                      await campaignsApi.importRecipients(id, file);
                      invalidateMappingAndValidation(id);
                      message.success('Импорт выполнен');
                    }}
                    onOpenGenerate={() => openWizardModal('generate')}
                    onOpenTopup={() => openWizardModal('topup')}
                    />
                  </div>
                ),
              },
              {
                key: '3',
                label: (
                  <span data-onboarding-id="campaign-step-schedule-label">
                    Расписание
                  </span>
                ),
                children: fixModalStep === 3 ? null : (
                  <div data-onboarding-id="campaign-step-schedule">
                    <CampaignWizardScheduleStep
                    form={scheduleForm}
                    initialValues={scheduleInitialValues}
                    batchCountPreview={batchCountPreview}
                    estimatedDurationHours={
                      schedulePreview.estimatedDurationSeconds > 0
                        ? Math.round(schedulePreview.estimatedDurationSeconds / 3600)
                        : undefined
                    }
                    onValuesChange={async (values) => {
                      if (!id) return;
                      const payload = formValuesToSchedulePayload(values);
                      if (!payload) return;
                      await campaignsApi.putSchedule(id, payload);
                      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
                    }}
                    />
                  </div>
                ),
              },
              {
                key: '4',
                label: (
                  <span data-onboarding-id="campaign-step-launch-label">
                    Проверка и запуск
                  </span>
                ),
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} data-onboarding-id="campaign-step-launch">
                    {isOnboardingPreview ? (
                      <>
                        <Alert
                          data-onboarding-id="campaign-launch-checks"
                          type="info"
                          showIcon
                          message="Демонстрационный режим"
                          description="Здесь появятся результаты проверки, предупреждения и ошибки. Во время обучения проверка и запуск не выполняются."
                        />
                        <Space wrap data-onboarding-id="campaign-launch-actions">
                          <Button disabled data-onboarding-id="campaign-test-email">Тестовое письмо</Button>
                          <Button disabled>Подтвердить сопоставление</Button>
                          <Button type="primary" disabled data-onboarding-id="campaign-start">Старт</Button>
                        </Space>
                      </>
                    ) : launchValidation.isChecking ? (
                      <Spin tip="Проверка…" />
                    ) : launchValidation.error ? (
                      <Alert
                        type="error"
                        showIcon
                        message="Не удалось выполнить проверку"
                        description={formatLaunchValidationError(launchValidation.error)}
                        action={
                          <Button
                            size="small"
                            icon={<ReloadOutlined />}
                            onClick={() => launchValidation.retry()}
                          >
                            Проверить снова
                          </Button>
                        }
                      />
                    ) : launchValidation.hasChecked ? (
                      <>
                        {readinessWarnings.length > 0 ? (
                          <Alert
                            type="warning"
                            showIcon
                            message="Предупреждения"
                            description={
                              <ul style={{ margin: 0, paddingLeft: 20 }}>
                                {readinessWarnings.map((warning) => (
                                  <li key={warning}>{warning}</li>
                                ))}
                              </ul>
                            }
                          />
                        ) : null}
                        {readinessErrors.length > 0 ? (
                          <Alert
                            type="error"
                            showIcon
                            message="Исправьте ошибки перед отправкой"
                            description={
                              <ul style={{ margin: 0, paddingLeft: 20 }}>
                                {readinessErrors.map((error) => (
                                  <li key={error}>{error}</li>
                                ))}
                              </ul>
                            }
                          />
                        ) : null}
                        <Space wrap>
                          <Button
                            icon={<ReloadOutlined />}
                            loading={launchValidation.isChecking}
                            disabled={wizardLocked}
                            onClick={() => launchValidation.retry()}
                          >
                            Проверить снова
                          </Button>
                          <Button
                            disabled={launchBlocked || wizardLocked}
                            title={readinessErrors.join('; ') || undefined}
                            onClick={async () => {
                              if (!id) return;
                              const to = window.prompt('Email для теста');
                              if (!to) return;
                              await runLaunchAction('Отправка тестового письма…', async () => {
                                try {
                                  const result = await campaignsApi.testEmail(id, to);
                                  if (
                                    result &&
                                    typeof result === 'object' &&
                                    'mode' in result &&
                                    (result as { mode?: string }).mode === 'chain_test'
                                  ) {
                                    message.success(
                                      `Тестовая цепочка запущена. Первое письмо отправлено на ${to}. Переходите по кнопкам в письме, чтобы получить следующие.`,
                                    );
                                  } else {
                                    message.success('Тестовое письмо отправлено');
                                  }
                                } catch (error) {
                                  message.error(
                                    error instanceof Error ? error.message : 'Не удалось отправить тестовое письмо',
                                  );
                                  throw error;
                                }
                              });
                            }}
                          >
                            Тестовое письмо
                          </Button>
                          {linkedChainId ? (
                            <Button
                              disabled={recipientCount === 0 || wizardLocked}
                              onClick={() => openWizardModal('preview')}
                            >
                              Предпросмотр цепочки
                            </Button>
                          ) : null}
                          <Button
                            icon={<SwapOutlined />}
                            disabled={recipientCount === 0 || wizardLocked}
                            onClick={() => openWizardModal('layout')}
                          >
                            Проверить вёрстку документов
                          </Button>
                          <Button
                            type="primary"
                            disabled={launchBlocked || wizardLocked}
                            title={readinessErrors.join('; ') || undefined}
                            onClick={async () => {
                              if (!id) return;
                              await runLaunchAction('Запуск рассылки…', async () => {
                                await campaignsApi.launch(id);
                                await queryClient.invalidateQueries({ queryKey: ['campaigns'] });
                                await navigateAfterLaunch(id, 'Рассылка запущена');
                              });
                            }}
                          >
                            Старт
                          </Button>
                        </Space>
                      </>
                    ) : null}
                  </Space>
                ),
              },
            ]}
          />
        </ProCard>
      </Col>
      <Col xs={24} xl={8}>
        <ProCard
          title="Готовность"
          bordered
          style={{ position: 'sticky', top: 72 }}
        >
          <Space direction="vertical">
            <Typography.Text>Получателей: {recipientsQuery.data?.total || 0}</Typography.Text>
            <Typography.Text>Исключено: {launchValidation.data?.excluded_recipients ?? '—'}</Typography.Text>
            <Typography.Text>Пакетов (прогноз): {batchCountPreview}</Typography.Text>
            <Typography.Text>
              Отправитель:{' '}
              {(mailboxesQuery.data || []).find((item) => item.id === draft.smtp_mailbox_id)?.email ||
                'не выбран'}
            </Typography.Text>
            <Typography.Text>Компания: {selectedCompanyLabel}</Typography.Text>
            <Typography.Text>Вид работ: {selectedWorkTypeLabel}</Typography.Text>
            <Typography.Text>
              Сопоставление переменных:{' '}
              {mappingAutoSuggest.isRunning ? (
                <Tag color="processing">автозаполнение…</Tag>
              ) : mappingConfirmed ? (
                <Tag color="green">подтверждено</Tag>
              ) : (
                <Tag
                  color="gold"
                  style={{ cursor: 'pointer' }}
                  onClick={() => openWizardModal('mapping')}
                >
                  требуется
                </Tag>
              )}
            </Typography.Text>
            {launchValidation.isChecking ? (
              <Typography.Text type="secondary">Проверка…</Typography.Text>
            ) : launchValidation.hasChecked ? (
              readinessErrors.length === 0 ? (
                <Tag color="green">Готово к запуску</Tag>
              ) : (
                <Tag color="red">Есть критические ошибки</Tag>
              )
            ) : step === 4 ? (
              <Typography.Text type="secondary">Ожидание проверки…</Typography.Text>
            ) : null}
          </Space>
        </ProCard>
      </Col>
      </Row>
      {id && draft.job_id ? (
        <RecipientGenerateModal
          open={generateModalOpen}
          mode={modalKind === 'topup' ? 'topup' : 'generate'}
          campaignId={id}
          jobId={draft.job_id}
          onClose={closeWizardModal}
          onImported={() => {
            if (id) invalidateMappingAndValidation(id);
          }}
        />
      ) : null}
      {id ? (
        <VariableMappingModal
          open={mappingModalOpen}
          campaignId={id}
          mappingInputsSignature={mappingInputsSignature}
          onClose={closeWizardModal}
          onConfirmed={handleMappingConfirmed}
        />
      ) : null}
      {id && linkedChainId ? (
        <ChainEmailPreviewModal
          open={chainPreviewOpen}
          campaignId={id}
          activeNodeId={previewNodeId}
          onActiveNodeChange={(nodeId) => pushParams({ preview_node: nodeId })}
          onClose={closeWizardModal}
        />
      ) : null}
      {id ? (
        <CampaignDocumentLayoutReview
          open={layoutReviewOpen}
          campaignId={id}
          onClose={closeWizardModal}
          onApplied={() => {
            void queryClient.invalidateQueries({ queryKey: campaignValidateQueryKey(id) });
          }}
        />
      ) : null}
      {fixModalStep !== null ? (
        <CampaignStepFixModal
          open={fixModalStep !== null}
          step={fixModalStep}
          validation={stepValidation[fixModalStep]}
          campaignId={id ?? undefined}
          draft={draft}
          linkedChainId={linkedChainId ?? undefined}
          basicsForm={basicsForm}
          senderForm={senderForm}
          scheduleForm={scheduleForm}
          chainOptions={chainOptions}
          companyOptions={companyOptions}
          workTypeOptions={workTypeOptions}
          selectedCompanyId={selectedCompanyId}
          chainsLoading={chainsQuery.isLoading}
          companiesLoading={companiesQuery.isLoading}
          workTypesLoading={workTypesQuery.isLoading}
          isAppAdmin={isAppAdmin}
          isCompanyAdmin={isCompanyAdmin}
          mailboxes={mailboxesQuery.data || []}
          audiences={audiencesQuery.data || []}
          recipients={recipientsQuery.data?.items || []}
          recipientsLoading={recipientsQuery.isFetching}
          scheduleInitialValues={scheduleInitialValues}
          batchCountPreview={batchCountPreview}
          estimatedDurationHours={
            schedulePreview.estimatedDurationSeconds > 0
              ? Math.round(schedulePreview.estimatedDurationSeconds / 3600)
              : undefined
          }
          saving={fixModalSaving}
          onClose={closeWizardModal}
          onSave={handleFixModalSave}
          onAutosave={autosave}
          onAudienceSelect={async (audienceId) => {
            if (!id) return;
            await audiencesApi.useInCampaign(audienceId, id);
            await persist({ audience_id: audienceId });
            invalidateMappingAndValidation(id);
            message.success('Аудитория загружена');
          }}
          onImportRecipients={async (file) => {
            if (!id) return;
            await campaignsApi.importRecipients(id, file);
            invalidateMappingAndValidation(id);
            message.success('Импорт выполнен');
          }}
          onOpenGenerate={() => openWizardModal('generate')}
          onOpenTopup={() => openWizardModal('topup')}
          onScheduleChange={async (values) => {
            if (!id) return;
            const payload = formValuesToSchedulePayload(values);
            if (!payload) return;
            await campaignsApi.putSchedule(id, payload);
            void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
          }}
          onOpenChainPreview={() => openWizardModal('preview')}
        />
      ) : null}
    </div>
  );
}
