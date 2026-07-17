import { api, apiRequest } from './client';
import type { EmailEditorState, PdfEditorField, PdfEditorState, Template } from './types';

export type OfficeEditorConfig = {
  editor_url: string;
  document_key: string;
  config: Record<string, unknown>;
};

export type TemplateStarter = {
  id: string;
  name: string;
  template_type: string;
  preview_html: string;
  subject?: string | null;
  email_format?: 'simple' | 'visual';
};

export type TemplateAiModel = {
  id: string;
  label: string;
  default?: boolean;
};

export const templatesApi = {
  list: (params?: { template_type?: string; q?: string }) => {
    const q = new URLSearchParams();
    if (params?.template_type) q.set('template_type', params.template_type);
    if (params?.q) q.set('q', params.q);
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<Template[]>(`/api/v1/templates${suffix}`);
  },
  get: (id: string) => api.get<Template>(`/api/v1/templates/${id}`),
  create: (body: {
    name: string;
    template_type: string;
    subject?: string;
    body_html?: string;
    body_text?: string;
    tags?: string[];
    editor_state?: EmailEditorState;
  }) => api.post<Template>('/api/v1/templates', body),
  uploadFile: (
    file: File,
    template_type: 'document',
    options?: { name?: string; template_id?: string },
  ) => {
    const form = new FormData();
    form.append('file', file);
    form.append('template_type', template_type);
    if (options?.name) form.append('name', options.name);
    if (options?.template_id) form.append('template_id', options.template_id);
    return apiRequest<Template>('/api/v1/templates/upload', { method: 'POST', body: form });
  },
  starters: (template_type?: string) => {
    const q = new URLSearchParams();
    if (template_type) q.set('template_type', template_type);
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<TemplateStarter[]>(`/api/v1/templates/starters${suffix}`);
  },
  useStarter: (starterId: string) =>
    api.post<Template>(`/api/v1/templates/starters/${starterId}/use`),
  models: () => api.get<TemplateAiModel[]>('/api/v1/templates/models'),
  generate: (body: {
    template_type: 'email' | 'document';
    prompt?: string;
    model?: string;
    files?: File[];
  }) => {
    const form = new FormData();
    form.append('template_type', body.template_type);
    form.append('prompt', body.prompt || '');
    form.append('model', body.model || '');
    for (const file of body.files || []) {
      form.append('files', file);
    }
    return apiRequest<Template>('/api/v1/templates/generate', { method: 'POST', body: form });
  },
  fileUrl: (id: string) => `/api/v1/templates/${id}/file`,
  deliveryFileUrl: (id: string) => `/api/v1/templates/${id}/delivery-file`,
  previewFileUrl: (id: string) => `/api/v1/templates/${id}/preview-file`,
  officeConfig: (id: string) => api.get<OfficeEditorConfig>(`/api/v1/templates/${id}/office-config`),
  forceSaveOffice: (id: string, versionId: string, documentKey: string) =>
    api.post<{ accepted: boolean; key: string }>(`/api/v1/templates/${id}/office-save`, {
      version_id: versionId,
      document_key: documentKey,
    }),
  pdfEditor: (id: string) => api.get<PdfEditorState>(`/api/v1/templates/${id}/pdf-editor`),
  pdfEditorPageUrl: (id: string, page: number) => `/api/v1/templates/${id}/pdf-editor/pages/${page}`,
  savePdfEditor: (id: string, fields: Pick<PdfEditorField, 'id' | 'value' | 'font_size'>[]) =>
    api.patch<Template>(`/api/v1/templates/${id}/pdf-editor`, { fields }),
  previewKpPdf: async (id: string, body_html: string) => {
    const response = await fetch(`/api/v1/templates/${id}/kp-preview-file`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body_html }),
    });
    if (!response.ok) {
      let detail = 'Не удалось собрать PDF';
      try {
        const payload = (await response.json()) as { detail?: string };
        detail = payload.detail || detail;
      } catch {
        // Keep the generic message for non-JSON errors.
      }
      throw new Error(detail);
    }
    return response.blob();
  },
  save: (
    id: string,
    body: {
      name?: string;
      subject?: string;
      body_html?: string;
      body_text?: string;
      variables?: { name: string; source: string; label: string }[];
      editor_state?: EmailEditorState;
    },
  ) => api.patch<Template>(`/api/v1/templates/${id}`, body),
  uploadAsset: async (templateId: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch(`/api/v1/templates/${templateId}/assets`, {
      method: 'POST',
      credentials: 'include',
      body: form,
    });
    if (!response.ok) {
      let detail = 'Не удалось загрузить изображение';
      try {
        const payload = (await response.json()) as { detail?: string };
        detail = payload.detail || detail;
      } catch {
        // Keep generic message.
      }
      throw new Error(detail);
    }
    const payload = (await response.json()) as { result?: { data?: Array<{ src?: string }> } };
    const src = payload.result?.data?.[0]?.src;
    if (!src) throw new Error('Сервер не вернул URL изображения');
    return src;
  },
  duplicate: (id: string) => api.post<Template>(`/api/v1/templates/${id}/duplicate`),
  archive: (id: string) => api.post<Template>(`/api/v1/templates/${id}/archive`),
  versions: (id: string) => api.get(`/api/v1/templates/${id}/versions`),
  preview: (id: string) => api.post<{ subject: string; body_html: string }>(`/api/v1/templates/${id}/preview`),
};
