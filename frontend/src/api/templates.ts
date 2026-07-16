import { api } from './client';
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
