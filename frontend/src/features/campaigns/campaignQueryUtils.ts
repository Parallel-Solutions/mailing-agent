import type { QueryClient } from '@tanstack/react-query';
import type { MessageInstance } from 'antd/es/message/interface';
import type { CampaignValidateResponse } from '@/api/types';

export const VALIDATION_AUTO_FIX_UI_ENABLED = false;

export type AutoFixValidationResult = {
  applied: Array<{ kind: string; message: string }>;
  skipped: Array<{ kind: string; message: string }>;
  validation: CampaignValidateResponse;
};

export function campaignValidateQueryKey(campaignId: string, validationSignature?: string) {
  return validationSignature
    ? (['campaign-validate', campaignId, validationSignature] as const)
    : (['campaign-validate', campaignId] as const);
}

export function campaignVariableMappingQueryKey(campaignId: string) {
  return ['campaign-variable-mapping', campaignId] as const;
}

export function campaignVariableMappingSuggestQueryKey(campaignId: string, signature: string) {
  return ['campaign-variable-mapping-suggest', campaignId, signature] as const;
}

export type MappingInputsSignatureInput = {
  recipientCount: number;
  emailChainId?: string | null;
  mappingConfirmed?: boolean;
  templateIds?: {
    email?: string | null;
    kp?: string | null;
    contract?: string | null;
  };
};

export type ValidationSignatureInput = {
  recipientCount: number;
  emailChainId?: string | null;
  companyId?: string | null;
  companyWorkTypeId?: string | null;
  mappingConfirmed?: boolean;
  mappingConfirmedAt?: string | null;
  smtpMailboxId?: string | null;
  audienceId?: string | null;
  templateIds?: {
    email?: string | null;
    kp?: string | null;
    contract?: string | null;
  };
};

export function buildCampaignAutosavePayload(patch: Record<string, unknown>) {
  const { draft_payload: _ignoredDraftPayload, ...draftPatch } = patch;
  return {
    ...patch,
    draft_payload: draftPatch,
  };
}

export function buildMappingInputsSignature(input: MappingInputsSignatureInput): string {
  return JSON.stringify({
    r: input.recipientCount,
    c: input.emailChainId || '',
    m: Boolean(input.mappingConfirmed),
    t: [
      input.templateIds?.email || '',
      input.templateIds?.kp || '',
      input.templateIds?.contract || '',
    ],
  });
}

export function buildValidationSignature(input: ValidationSignatureInput): string {
  return JSON.stringify({
    r: input.recipientCount,
    c: input.emailChainId || '',
    co: input.companyId || '',
    w: input.companyWorkTypeId || '',
    m: Boolean(input.mappingConfirmed),
    mv: input.mappingConfirmedAt || '',
    s: input.smtpMailboxId || '',
    a: input.audienceId || '',
    t: [
      input.templateIds?.email || '',
      input.templateIds?.kp || '',
      input.templateIds?.contract || '',
    ],
  });
}

export function invalidateCampaignMappingCache(queryClient: QueryClient, campaignId: string) {
  void queryClient.removeQueries({ queryKey: campaignVariableMappingQueryKey(campaignId) });
  void queryClient.removeQueries({
    queryKey: ['campaign-variable-mapping-suggest', campaignId],
    exact: false,
  });
}

export function invalidateCampaignDerivedData(
  queryClient: QueryClient,
  campaignId: string,
  opts?: { includeValidation?: boolean; includeMapping?: boolean },
) {
  if (opts?.includeValidation) {
    void queryClient.invalidateQueries({ queryKey: campaignValidateQueryKey(campaignId) });
  }
  if (opts?.includeMapping) {
    invalidateCampaignMappingCache(queryClient, campaignId);
  }
  void queryClient.invalidateQueries({ queryKey: ['email-chain-preview', campaignId] });
  void queryClient.invalidateQueries({ queryKey: ['campaign-recipients', campaignId] });
}

export function resolveLinkedChainId(
  formChainId: string | undefined | null,
  draftChainId: string | undefined | null,
): string | undefined {
  const fromForm = typeof formChainId === 'string' ? formChainId.trim() : '';
  if (fromForm) return fromForm;
  const fromDraft = typeof draftChainId === 'string' ? draftChainId.trim() : '';
  return fromDraft || undefined;
}

export function showAutoFixResultMessage(
  result: AutoFixValidationResult,
  messageApi: MessageInstance,
  opts?: { remainingIssues?: number },
) {
  const appliedCount = result.applied.length;
  const skippedCount = result.skipped.length;
  const remainingIssues =
    opts?.remainingIssues ?? (result.validation.template_issues?.length || 0);

  if (appliedCount > 0 && (remainingIssues > 0 || skippedCount > 0)) {
    messageApi.warning(
      `Исправлено проблем: ${appliedCount}. Осталось: ${Math.max(remainingIssues, skippedCount)}`,
    );
    return;
  }
  if (appliedCount > 0) {
    messageApi.success(`Исправлено проблем: ${appliedCount}`);
    return;
  }
  if (skippedCount > 0) {
    const hint = result.skipped[0]?.message;
    messageApi.warning(
      hint ? `Автоисправление не применилось: ${hint}` : 'Автоисправление не применилось',
    );
    return;
  }
  messageApi.info('Исправлений не потребовалось');
}
