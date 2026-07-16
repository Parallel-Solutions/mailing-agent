import { api, apiRequest } from './client';
import type { Template } from './types';

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
  }) => api.post<Template>('/api/v1/templates', body),
  uploadFile: (
    file: File,
    template_type: 'kp' | 'contract',
    options?: { name?: string; template_id?: string },
  ) => {
    const form = new FormData();
    form.append('file', file);
    form.append('template_type', template_type);
    if (options?.name) form.append('name', options.name);
    if (options?.template_id) form.append('template_id', options.template_id);
    return apiRequest<Template>('/api/v1/templates/upload', { method: 'POST', body: form });
  },
  fileUrl: (id: string) => `/api/v1/templates/${id}/file`,
  previewFileUrl: (id: string) => `/api/v1/templates/${id}/preview-file`,
  save: (
    id: string,
    body: {
      name?: string;
      subject?: string;
      body_html?: string;
      body_text?: string;
      variables?: { name: string; source: string; label: string }[];
    },
  ) => api.patch<Template>(`/api/v1/templates/${id}`, body),
  duplicate: (id: string) => api.post<Template>(`/api/v1/templates/${id}/duplicate`),
  archive: (id: string) => api.post<Template>(`/api/v1/templates/${id}/archive`),
  versions: (id: string) => api.get(`/api/v1/templates/${id}/versions`),
  preview: (id: string) => api.post<{ subject: string; body_html: string }>(`/api/v1/templates/${id}/preview`),
};
