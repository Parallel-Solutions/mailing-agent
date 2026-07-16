import { api } from './client';

function withQuery(path: string, params?: Record<string, string | number | boolean | undefined | null>) {
  const q = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue;
      q.set(key, String(value));
    }
  }
  const suffix = q.toString() ? `?${q}` : '';
  return `${path}${suffix}`;
}

export type StatsParams = Record<string, string | number | boolean | undefined | null>;

export type ManagerActionBody = {
  action_type: string;
  responsible_manager?: string;
  due_at?: string;
  comment?: string;
  priority?: string;
};

export type ExportReportBody = {
  report_type: string;
  period_from?: string;
  period_to?: string;
  job_id?: string;
  fmt?: string;
  options?: Record<string, unknown>;
};

export const statisticsApi = {
  managerDashboard: (params?: StatsParams) =>
    api.get<Record<string, unknown>>(withQuery('/api/sender/manager-dashboard', params)),

  campaigns: (params?: StatsParams) =>
    api.get<Record<string, unknown>>(withQuery('/api/sender/campaigns', params)),

  recipients: (params?: StatsParams) =>
    api.get<Record<string, unknown>>(withQuery('/api/sender/recipients', params)),

  recipientDetail: (rowKey: string) =>
    api.get<Record<string, unknown>>(`/api/sender/recipients/${encodeURIComponent(rowKey)}`),

  saveRecipientAction: (rowKey: string, body: ManagerActionBody) =>
    api.post<Record<string, unknown>>(
      `/api/sender/recipients/${encodeURIComponent(rowKey)}/action`,
      body,
    ),

  consents: (params?: StatsParams) =>
    api.get<Record<string, unknown>>(withQuery('/api/sender/consents', params)),

  problems: (params?: StatsParams) =>
    api.get<Record<string, unknown>>(withQuery('/api/sender/email-problems', params)),

  campaignAnalytics: (jobId: string, params?: StatsParams) =>
    api.get<Record<string, unknown>>(
      withQuery(`/api/sender/campaign-analytics/${encodeURIComponent(jobId)}`, params),
    ),

  reports: (params?: StatsParams) =>
    api.get<Record<string, unknown>>(withQuery('/api/sender/reports', params)),

  exportReport: (body: ExportReportBody) =>
    api.post<{ report_id?: string; download_url?: string } & Record<string, unknown>>(
      '/api/sender/reports/export',
      body,
    ),

  reportDownloadUrl: (reportId: string) =>
    `/api/sender/reports/download/${encodeURIComponent(reportId)}`,

  deliveryReportUrl: (jobId: string) =>
    `/api/download/sender-delivery-report?job_id=${encodeURIComponent(jobId)}`,

  autoCallContactsUrl: (jobId: string) =>
    `/api/download/auto-call-contacts?job_id=${encodeURIComponent(jobId)}`,
};
