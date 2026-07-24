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

export const previewApi = {
  archive: (jobId: string, params?: { offset?: number; limit?: number; q?: string }) =>
    api.get<{ entries: Array<Record<string, unknown>>; total: number }>(
      withQuery('/api/preview/archive', { kind: 'output', job_id: jobId, ...params }),
    ),

  fileUrl: (jobId: string, path: string) =>
    withQuery('/api/preview/file', { kind: 'output', job_id: jobId, path }),
};
