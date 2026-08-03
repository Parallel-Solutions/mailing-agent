import { ApiError, api } from './client';

export type ParserChatResponse = {
  status?: string;
  reply?: string;
  success?: boolean;
  result_file?: string | null;
  downloads?: { url?: string; label?: string }[];
};

export type ParserProgressEvent = {
  kind?: string;
  text?: string;
};

export const parserApi = {
  chat: (message: string, jobId: string, signal?: AbortSignal) =>
    api.post<ParserChatResponse>('/api/parser/chat', { message, job_id: jobId }, signal),

  topup: (message: string, jobId: string, signal?: AbortSignal) =>
    api.post<ParserChatResponse>('/api/parser/topup', { message, job_id: jobId }, signal),

  fillGaps: (jobId: string, verifyEmails: boolean, signal?: AbortSignal) =>
    api.post<ParserChatResponse>('/api/parser/fill', { job_id: jobId, verify_emails: verifyEmails }, signal),

  downloadResult: async (jobId: string): Promise<File> => {
    const response = await fetch(
      `/api/parser/download-result?job_id=${encodeURIComponent(jobId)}`,
      { credentials: 'include' },
    );
    if (!response.ok) {
      throw new ApiError(response.status, 'Не удалось скачать результат парсера');
    }
    const blob = await response.blob();
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = /filename\*?=(?:UTF-8''|")?([^";]+)/i.exec(disposition);
    const filename = match ? decodeURIComponent(match[1].replace(/"/g, '')) : 'parser-result.xlsx';
    return new File([blob], filename.endsWith('.xlsx') ? filename : `${filename}.xlsx`, {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
  },

  openProgressStream: (
    jobId: string,
    onEvent: (event: ParserProgressEvent) => void,
  ): EventSource | null => {
    if (typeof EventSource === 'undefined') return null;
    const source = new EventSource(
      `/api/parser/progress?job_id=${encodeURIComponent(jobId)}`,
    );
    source.onmessage = (message) => {
      try {
        const data = JSON.parse(message.data) as ParserProgressEvent;
        onEvent(data);
        if (data.kind === 'done' || data.kind === 'timeout') {
          source.close();
        }
      } catch {
        // ignore malformed SSE payloads
      }
    };
    source.onerror = () => {
      source.close();
    };
    return source;
  },
};
