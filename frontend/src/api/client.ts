export type ApiErrorDetail = {
  code?: string;
  title?: string;
  message: string;
  hint?: string;
  campaign_id?: string;
  campaign_status?: string;
};

export class ApiError extends Error {
  status: number;
  detail: string;
  payload: ApiErrorDetail;

  constructor(status: number, detail: string | ApiErrorDetail) {
    const payload = typeof detail === 'string' ? { message: detail } : detail;
    super(payload.message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = payload.message;
    this.payload = payload;
  }
}

type ApiEnvelope<T> = {
  status?: string;
  result?: T;
  detail?: string | ApiErrorDetail | { msg?: string }[];
};

async function parseDetail(response: Response): Promise<ApiErrorDetail> {
  try {
    const data = (await response.json()) as ApiEnvelope<unknown>;
    if (typeof data.detail === 'string') return { message: data.detail };
    if (Array.isArray(data.detail)) {
      return {
        message: data.detail.map((item) => item.msg || JSON.stringify(item)).join('; '),
      };
    }
    if (data.detail && typeof data.detail === 'object' && 'message' in data.detail) {
      return {
        code: typeof data.detail.code === 'string' ? data.detail.code : undefined,
        title: typeof data.detail.title === 'string' ? data.detail.title : undefined,
        message: String(data.detail.message || response.statusText || 'Ошибка запроса'),
        hint: typeof data.detail.hint === 'string' ? data.detail.hint : undefined,
        campaign_id: typeof data.detail.campaign_id === 'string' ? data.detail.campaign_id : undefined,
        campaign_status: typeof data.detail.campaign_status === 'string' ? data.detail.campaign_status : undefined,
      };
    }
    return { message: response.statusText || 'Ошибка запроса' };
  } catch {
    return { message: response.statusText || 'Ошибка запроса' };
  }
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: 'include',
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseDetail(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const data = (await response.json()) as ApiEnvelope<T> | T;
  if (data && typeof data === 'object' && 'result' in data) {
    return (data as ApiEnvelope<T>).result as T;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, options: RequestInit = {}) => apiRequest<T>(path, options),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    apiRequest<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    }),
  patch: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, {
      method: 'PATCH',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T>(path: string, body?: unknown) =>
    apiRequest<T>(path, {
      method: 'PUT',
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  upload: <T>(path: string, file: File, fieldName = 'file') => {
    const form = new FormData();
    form.append(fieldName, file);
    return apiRequest<T>(path, { method: 'POST', body: form });
  },
  delete: <T>(path: string) => apiRequest<T>(path, { method: 'DELETE' }),
};
