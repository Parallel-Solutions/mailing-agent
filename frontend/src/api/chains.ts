import { api } from './client';
import type { EmailChain, EmailChainState } from './types';

export type ChainListItem = {
  id: string;
  name: string;
  published: boolean;
  updated_at?: string | null;
};

export type ChainList = {
  items: ChainListItem[];
  total: number;
};

export type ChainRecord = EmailChainState & {
  id: string;
  name: string;
  created_at?: string | null;
  updated_at?: string | null;
};

export const chainsApi = {
  list: (params?: { limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set('limit', String(params.limit));
    if (params?.offset) q.set('offset', String(params.offset));
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<ChainList>(`/api/v1/chains${suffix}`);
  },
  create: (body?: { name?: string }) => api.post<ChainRecord>('/api/v1/chains', body ?? {}),
  get: (id: string) => api.get<ChainRecord>(`/api/v1/chains/${id}`),
  save: (id: string, chain: EmailChain) => api.put<ChainRecord>(`/api/v1/chains/${id}`, chain),
  publish: (id: string) => api.post<ChainRecord>(`/api/v1/chains/${id}/publish`),
};
