import { afterEach, describe, expect, it, vi } from 'vitest';

import { campaignsApi } from './campaigns';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('campaignsApi.validate', () => {
  it('aborts a validation request after 30 seconds', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'));
        });
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const validation = campaignsApi.validate('campaign-1');
    const rejection = expect(validation).rejects.toThrow(
      '\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0437\u0430\u043d\u044f\u043b\u0430 \u0431\u043e\u043b\u044c\u0448\u0435 30 \u0441\u0435\u043a\u0443\u043d\u0434',
    );

    await vi.advanceTimersByTimeAsync(30_000);
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it('uses the caller signal to cancel a stale validation without reporting a timeout', async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('Aborted', 'AbortError'));
        });
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    const validation = campaignsApi.validate('campaign-1', { signal: controller.signal });
    controller.abort();

    await expect(validation).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
