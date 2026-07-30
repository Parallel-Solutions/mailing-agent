import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiRequest } from './client';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiRequest structured errors', () => {
  it('preserves the user-facing conversion error payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: 'document_conversion_failed',
              title: 'Не удалось преобразовать документ',
              message: 'Документ не удалось преобразовать в PDF.',
              hint: 'Проверьте файл и повторите загрузку.',
            },
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    );

    const error = await apiRequest('/api/v1/templates/upload').catch((reason) => reason);

    expect(error).toBeInstanceOf(ApiError);
    if (!(error instanceof ApiError)) {
      throw new Error('Expected apiRequest to reject with ApiError');
    }
    expect(error.status).toBe(422);
    expect(error.detail).toBe('Документ не удалось преобразовать в PDF.');
    expect(error.payload).toEqual({
      code: 'document_conversion_failed',
      title: 'Не удалось преобразовать документ',
      message: 'Документ не удалось преобразовать в PDF.',
      hint: 'Проверьте файл и повторите загрузку.',
    });
  });
});
