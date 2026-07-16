import { api } from './client';
import type { Audience, Recipient } from './types';

export const audiencesApi = {
  list: () => api.get<Audience[]>('/api/v1/audiences'),
  create: (name: string, source = 'manual') =>
    api.post<Audience>('/api/v1/audiences', { name, source }),
  get: (id: string) => api.get<Audience>(`/api/v1/audiences/${id}`),
  update: (id: string, name: string) => api.patch<Audience>(`/api/v1/audiences/${id}`, { name }),
  duplicate: (id: string) => api.post<Audience>(`/api/v1/audiences/${id}/duplicate`),
  members: (id: string, params?: { limit?: number; offset?: number; q?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set('limit', String(params.limit));
    if (params?.offset) q.set('offset', String(params.offset));
    if (params?.q) q.set('q', params.q);
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<{ items: Recipient[]; total: number }>(`/api/v1/audiences/${id}/members${suffix}`);
  },
  replaceMembers: (id: string, members: Partial<Recipient>[]) =>
    api.put(`/api/v1/audiences/${id}/members`, { members }),
  importFile: (id: string, file: File) => api.upload(`/api/v1/audiences/${id}/import`, file),
  useInCampaign: (audienceId: string, campaignId: string) =>
    api.post(`/api/v1/audiences/${audienceId}/use-in-campaign/${campaignId}`),
};
