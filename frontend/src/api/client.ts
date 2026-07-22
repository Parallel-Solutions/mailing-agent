export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

type ApiEnvelope<T> = {
  status?: string;
  result?: T;
  detail?: string | { msg?: string }[];
};

async function parseDetail(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as ApiEnvelope<unknown>;
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map((item) => item.msg || JSON.stringify(item)).join('; ');
    }
    return response.statusText || 'Ошибка запроса';
  } catch {
    return response.statusText || 'Ошибка запроса';
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
  get: <T>(path: string) => apiRequest<T>(path),
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
