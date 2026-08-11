import { api, apiRequest } from './client';
import type { ConnectionWarmupProgram, DeliveryConnection } from './types';

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
  oauth_provider?: string | null;
  recommended_settings?: SmtpSetupSettings | null;
  ai_used?: boolean;
};

export type SmtpSetupAnalysis = {
  setup_session_id: string;
  email: string;
  domain: string;
  probe?: (SmtpSetupSettings & { reachable: boolean; source?: string; confidence?: string }) | null;
  discoveries: Array<SmtpSetupSettings & { source?: string; confidence?: string }>;
  action: SmtpSetupAction;
  oauth_available?: Record<string, boolean>;
  probe_status: string;
  discovery_applied: boolean;
};

export type SmtpSetupVerification = {
  verified: boolean;
  error?: string;
  settings: SmtpSetupSettings;
  analysis?: SmtpSetupAnalysis;
};

export type OAuthTokensPayload = {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  expires_in?: number;
  scope?: string;
};

export type OAuthPopupResult = {
  provider: string;
  email: string;
  setup_session_id: string;
  tokens: OAuthTokensPayload;
};

type OAuthMessagePayload = {
  type?: string;
  success?: boolean;
  message?: string;
  payload?: {
    provider?: string;
    email?: string;
    setup_session_id?: string;
    tokens?: OAuthTokensPayload;
  };
};

function waitForOAuthPopup(popup: Window): Promise<OAuthPopupResult> {
  return new Promise((resolve, reject) => {
    const timeoutMs = 5 * 60 * 1000;
    let settled = false;

    const cleanup = () => {
      window.removeEventListener('message', onMessage);
      window.clearInterval(closedTimer);
      window.clearTimeout(timeoutTimer);
    };

    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn();
    };

    const onMessage = (event: MessageEvent<OAuthMessagePayload>) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data;
      if (!data || typeof data !== 'object') return;
      if (data.type === 'smtp-oauth-success') {
        const payload = data.payload || {};
        const tokens = payload.tokens;
        if (!tokens?.access_token || !payload.provider) {
          finish(() => reject(new Error('OAuth завершён без токенов.')));
          return;
        }
        finish(() =>
          resolve({
            provider: String(payload.provider),
            email: String(payload.email || ''),
            setup_session_id: String(payload.setup_session_id || ''),
            tokens,
          }),
        );
        return;
      }
      if (data.type === 'smtp-oauth-error') {
        finish(() => reject(new Error(data.message || 'OAuth не удался.')));
      }
    };

    const closedTimer = window.setInterval(() => {
      if (popup.closed) {
        finish(() => reject(new Error('Окно OAuth закрыто до завершения входа.')));
      }
    }, 500);

    const timeoutTimer = window.setTimeout(() => {
      try {
        popup.close();
      } catch {
        // ignore
      }
      finish(() => reject(new Error('Истекло время ожидания OAuth.')));
    }, timeoutMs);

    window.addEventListener('message', onMessage);
  });
}

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
  startOAuth: (params: { provider: string; email: string; setup_session_id: string }) => {
    const query = new URLSearchParams({
      provider: params.provider,
      email: params.email,
      setup_session_id: params.setup_session_id,
    });
    return api.get<{ authorize_url: string; state: string }>(
      `/api/smtp/oauth/start?${query.toString()}`,
    );
  },
  runOAuthPopup: async (params: {
    provider: string;
    email: string;
    setup_session_id: string;
  }): Promise<OAuthPopupResult> => {
    const popup = window.open(
      'about:blank',
      'smtp-oauth',
      'width=520,height=720,menubar=no,toolbar=no,status=no',
    );
    if (!popup) {
      throw new Error('Не удалось открыть окно OAuth. Разрешите всплывающие окна для этого сайта.');
    }
    const resultPromise = waitForOAuthPopup(popup);
    try {
      const started = await connectionsApi.startOAuth(params);
      popup.location.href = started.authorize_url;
    } catch (error) {
      try {
        popup.close();
      } catch {
        // ignore
      }
      void resultPromise.catch(() => undefined);
      throw error;
    }
    return resultPromise;
  },
  create: (body: Record<string, unknown>) =>
    api.post<DeliveryConnection>('/api/v1/connections', body),
  update: (id: string, body: Record<string, unknown>) =>
    api.patch<DeliveryConnection>(`/api/v1/connections/${id}`, body),
  remove: (id: string) =>
    apiRequest<{ deleted: boolean }>(`/api/v1/connections/${id}`, { method: 'DELETE' }),
  test: (id: string) =>
    api.post<{ status: string; message: string }>(`/api/v1/connections/${id}/test`),
  resetGuard: (id: string) =>
    api.post<DeliveryConnection>(`/api/v1/connections/${id}/guard/reset`),
  getWarmup: (id: string) =>
    api.get<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup`),
  updateWarmup: (id: string, body: Record<string, unknown>) =>
    api.patch<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup`, body),
  diagnoseWarmup: (id: string, headers = '') =>
    api.post<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup/diagnostics`, { headers }),
  addWarmupRecipients: (id: string, recipients: Array<{ email: string; messages_per_day: number }>) =>
    api.post<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup/recipients`, { recipients }),
  setWarmupRecipientStatus: (id: string, recipientId: string, status: 'active' | 'disabled') =>
    api.patch<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup/recipients/${recipientId}`, { status }),
  setWarmupRecipientDailyCount: (id: string, recipientId: string, messagesPerDay: number) =>
    api.patch<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup/recipients/${recipientId}`, { messages_per_day: messagesPerDay }),
  removeWarmupRecipient: (id: string, recipientId: string) =>
    api.delete<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup/recipients/${recipientId}`),
  changeWarmupStatus: (id: string, action: 'start' | 'pause' | 'resume' | 'stop') =>
    api.post<ConnectionWarmupProgram>(`/api/v1/connections/${id}/sender-warmup/${action}`),
};
