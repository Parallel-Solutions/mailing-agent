import { api } from './client';

export const statisticsApi = {
  managerDashboard: (params?: Record<string, string>) => {
    const q = new URLSearchParams(params || {});
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<Record<string, unknown>>(`/api/sender/manager-dashboard${suffix}`);
  },
  campaigns: (params?: Record<string, string>) => {
    const q = new URLSearchParams(params || {});
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<Record<string, unknown>>(`/api/sender/campaigns${suffix}`);
  },
  recipients: (params?: Record<string, string>) => {
    const q = new URLSearchParams(params || {});
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<Record<string, unknown>>(`/api/sender/recipients${suffix}`);
  },
  consents: (params?: Record<string, string>) => {
    const q = new URLSearchParams(params || {});
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<Record<string, unknown>>(`/api/sender/consents${suffix}`);
  },
  problems: (params?: Record<string, string>) => {
    const q = new URLSearchParams(params || {});
    const suffix = q.toString() ? `?${q}` : '';
    return api.get<Record<string, unknown>>(`/api/sender/email-problems${suffix}`);
  },
  reports: () => api.get<Record<string, unknown>>('/api/sender/reports'),
  exportReport: (body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>('/api/sender/reports/export', body),
};
