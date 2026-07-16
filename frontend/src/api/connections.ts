import { api, apiRequest } from './client';
import type { DeliveryConnection } from './types';

export type SmtpSetupSettings = {
  provider: string;
  host: string;
  port: number;
  use_ssl: boolean;
  use_starttls: boolean;
  auth_method?: string;
  oauth_provider?: string | null;
};

export type SmtpSetupAction = {
  action: string;
  message_ru: string;
  instructions: string[];
  recommended_settings?: SmtpSetupSettings | null;
};

export type SmtpSetupAnalysis = {
  setup_session_id: string;
  email: string;
  domain: string;
  probe?: (SmtpSetupSettings & { reachable: boolean; source?: string; confidence?: string }) | null;
  discoveries: Array<SmtpSetupSettings & { source?: string; confidence?: string }>;
  action: SmtpSetupAction;
  probe_status: string;
  discovery_applied: boolean;
};

export type SmtpSetupVerification = {
  verified: boolean;
  error?: string;
  settings: SmtpSetupSettings;
  analysis?: SmtpSetupAnalysis;
};

export const connectionsApi = {
  list: () => api.get<DeliveryConnection[]>('/api/v1/connections'),
  analyzeSmtp: (email: string) =>
    api.post<SmtpSetupAnalysis>('/api/smtp/setup/analyze', { email }),
  verifySmtp: (body: {
    setup_session_id: string;
    email: string;
    password: string;
    provider: string;
    host: string;
    port: number;
    use_ssl: boolean;
    use_starttls: boolean;
    smtp_username?: string;
  }) => api.post<SmtpSetupVerification>('/api/smtp/setup/verify', body),
  create: (body: Record<string, unknown>) =>
    api.post<DeliveryConnection>('/api/v1/connections', body),
  update: (id: string, body: Record<string, unknown>) =>
    api.patch<DeliveryConnection>(`/api/v1/connections/${id}`, body),
  remove: (id: string) =>
    apiRequest<{ deleted: boolean }>(`/api/v1/connections/${id}`, { method: 'DELETE' }),
  test: (id: string) =>
    api.post<{ status: string; message: string }>(`/api/v1/connections/${id}/test`),
};
