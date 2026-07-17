import { api } from './client';
import type {
  ActiveSending,
  Batch,
  Campaign,
  CampaignGeneration,
  CampaignList,
  DocumentTemplatePreview,
  EmailChain,
  EmailChainPreviewResponse,
  EmailChainState,
  EmailChainStats,

  Recipient,
  Schedule,
  SchedulePreview,
} from './types';

export type TemplateVariableItem = {
  name: string;
  label?: string;
  source?: string;
};

export type VariableMappingSuggestResult = {
  status: 'complete' | 'needs_review';
  template_variables: TemplateVariableItem[];
  recipient_columns: string[];
  suggested_mapping: Record<string, string>;
  unmapped: string[];
};

export type VariableMappingState = {
  mapping_confirmed: boolean;
  mapping_confirmed_at?: string | null;
  variable_mapping: Record<string, string>;
  recipient_columns: string[];
  template_variables: TemplateVariableItem[];
};

export const campaignsApi = {
  list: (params?: { status?: string; q?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.status) q.set('status', params.status);
    if (params?.q) q.set('q', params.q);
    if (params?.limit) q.set('limit', String(params.limit));
    if (params?.offset) q.set('offset', String(params.offset));
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<CampaignList>(`/api/v1/campaigns${suffix}`);
  },
  get: (id: string) => api.get<Campaign>(`/api/v1/campaigns/${id}`),
  create: (body: Partial<Campaign>) => api.post<Campaign>('/api/v1/campaigns', body),
  update: (id: string, body: Partial<Campaign>) => api.patch<Campaign>(`/api/v1/campaigns/${id}`, body),
  duplicate: (id: string) => api.post<Campaign>(`/api/v1/campaigns/${id}/duplicate`),
  archive: (id: string) => api.post<Campaign>(`/api/v1/campaigns/${id}/archive`),
  activeSending: () => api.get<ActiveSending>('/api/v1/campaigns/active-sending'),
  recipients: (id: string, params?: { limit?: number; offset?: number; q?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set('limit', String(params.limit));
    if (params?.offset) q.set('offset', String(params.offset));
    if (params?.q) q.set('q', params.q);
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<{ items: Recipient[]; total: number }>(`/api/v1/campaigns/${id}/recipients${suffix}`);
  },
  replaceRecipients: (id: string, recipients: Partial<Recipient>[]) =>
    api.put<{ total: number; duplicates_skipped: number; invalid: number }>(
      `/api/v1/campaigns/${id}/recipients`,
      { recipients },
    ),
  updateRecipient: (id: string, rid: number, body: Partial<Recipient>) =>
    api.patch(`/api/v1/campaigns/${id}/recipients/${rid}`, body),
  deleteRecipients: (id: string, ids: number[]) =>
    api.post<{ deleted: number }>(`/api/v1/campaigns/${id}/recipients/delete`, { ids }),
  importRecipients: (id: string, file: File) =>
    api.upload<{ import: { total: number }; preview: Recipient[] }>(
      `/api/v1/campaigns/${id}/recipients/import`,
      file,
    ),
  getSchedule: (id: string) => api.get<Schedule>(`/api/v1/campaigns/${id}/schedule`),
  putSchedule: (id: string, body: Partial<Schedule>) =>
    api.put<Schedule>(`/api/v1/campaigns/${id}/schedule`, body),
  previewSchedule: (body: Record<string, unknown>) =>
    api.post<SchedulePreview>('/api/v1/schedule/preview', body),
  generation: (id: string) =>
    api.get<CampaignGeneration>(`/api/v1/campaigns/${id}/generation`),
  prepareGeneration: (id: string) =>
    api.post<CampaignGeneration>(`/api/v1/campaigns/${id}/generation/prepare`),
  previewDocuments: (body: { job_id: string; document_mode: string; work_type?: string }) =>
    api.post<DocumentTemplatePreview>('/api/documents/template-preview', body),
  startDocuments: (body: {
    job_id: string;
    document_mode: string;
    work_type?: string;
    template_analysis_confirmed: boolean;
    mode?: string;
  }) => api.post<Record<string, unknown>>('/api/documents/start', body),
  validate: (id: string) =>
    api.get<{
      ok: boolean;
      errors: string[];
      warnings: string[];
      active_recipients: number;
      excluded_recipients: number;
      mapping_confirmed?: boolean;
    }>(`/api/v1/campaigns/${id}/validate`),
  launch: (id: string, forceNow = false) =>
    api.post(`/api/v1/campaigns/${id}/launch?force_now=${forceNow}`),
  pause: (id: string) => api.post<Campaign>(`/api/v1/campaigns/${id}/pause`),
  resume: (id: string) => api.post<Campaign>(`/api/v1/campaigns/${id}/resume`),
  cancel: (id: string) => api.post<Campaign>(`/api/v1/campaigns/${id}/cancel`),
  batches: (id: string) => api.get<Batch[]>(`/api/v1/campaigns/${id}/batches`),
  cancelBatch: (id: string, batchId: string) =>
    api.post(`/api/v1/campaigns/${id}/batches/${batchId}/cancel`),
  testEmail: (id: string, to_email: string, smtp_mailbox_id?: string) =>
    api.post(`/api/v1/campaigns/${id}/test-email`, { to_email, smtp_mailbox_id }),
  suggestVariableMapping: (id: string, model?: string) =>
    api.post<VariableMappingSuggestResult>(`/api/v1/campaigns/${id}/variable-mapping/suggest`, {
      model,
    }),
  saveVariableMapping: (id: string, mapping: Record<string, string>) =>
    api.put<{ mapping_confirmed: boolean; variable_mapping: Record<string, string> }>(
      `/api/v1/campaigns/${id}/variable-mapping`,
      { mapping },
    ),
  getVariableMapping: (id: string) =>
    api.get<VariableMappingState>(`/api/v1/campaigns/${id}/variable-mapping`),
  getEmailChain: (id: string) => api.get<EmailChainState>(`/api/v1/campaigns/${id}/email-chain`),
  putEmailChain: (id: string, chain: EmailChain) =>
    api.put<EmailChainState>(`/api/v1/campaigns/${id}/email-chain`, chain),
  publishEmailChain: (id: string) =>
    api.post<EmailChainState & { campaign_id: string }>(`/api/v1/campaigns/${id}/email-chain/publish`),
  getEmailChainStats: (id: string) =>
    api.get<EmailChainStats>(`/api/v1/campaigns/${id}/email-chain/stats`),
  previewEmailChain: (id: string) =>
    api.post<EmailChainPreviewResponse>(`/api/v1/campaigns/${id}/email-chain/preview`),
  previewEmailChainAttachmentUrl: (id: string, recipientId: number, templateId: string) =>
    `/api/v1/campaigns/${id}/email-chain/preview/attachment?recipient_id=${recipientId}&template_id=${encodeURIComponent(templateId)}`,
};
