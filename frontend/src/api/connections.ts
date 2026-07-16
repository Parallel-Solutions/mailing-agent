import { api } from './client';
import type { SmtpMailbox } from './types';

export const connectionsApi = {
  list: async () => {
    const data = await api.get<
      { mailboxes?: SmtpMailbox[]; items?: SmtpMailbox[] } | SmtpMailbox[]
    >('/api/smtp/mailboxes');
    if (Array.isArray(data)) return data;
    return data.mailboxes || data.items || [];
  },
  create: (body: Record<string, unknown>) => api.post<SmtpMailbox>('/api/smtp/mailboxes', body),
  update: (id: string, body: Record<string, unknown>) =>
    api.patch<SmtpMailbox>(`/api/smtp/mailboxes/${id}`, body),
  remove: (id: string) => apiRequestDelete(`/api/smtp/mailboxes/${id}`),
  test: (id: string) => api.post(`/api/smtp/mailboxes/${id}/test`),
  providers: () => api.get('/api/smtp/providers'),
};

async function apiRequestDelete(path: string) {
  const response = await fetch(path, { method: 'DELETE', credentials: 'include' });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json().catch(() => ({ status: 'ok' }));
}
