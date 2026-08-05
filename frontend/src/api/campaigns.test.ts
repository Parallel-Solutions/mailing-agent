import { afterEach, describe, expect, it, vi } from 'vitest';

import { campaignsApi } from './campaigns';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('campaignsApi.validate', () => {
  it('does not abort a slow validation request on its own after 30 seconds', async () => {
    vi.useFakeTimers();
    let settled = false;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          settled = true;
          reject(new DOMException('Aborted', 'AbortError'));
        });
        // Never resolves/rejects on its own \u2014 only a caller-provided signal should stop it.
        void resolve;
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    void campaignsApi.validate('campaign-1');

    await vi.advanceTimersByTimeAsync(120_000);
    expect(settled).toBe(false);
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

describe('campaignsApi.list', () => {
  it('passes the requested campaign scope to the API', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok', result: { items: [], total: 0 } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await campaignsApi.list({ scope: 'launched', limit: 100 });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/campaigns?scope=launched&limit=100',
      expect.objectContaining({ credentials: 'include' }),
    );
  });
});

describe('campaignsApi attachment preview', () => {
  it('requests a PDF preview and forwards the abort signal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('%PDF-preview', {
        status: 200,
        headers: { 'Content-Type': 'application/pdf' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    const result = await campaignsApi.fetchPreviewEmailChainAttachment(
      'campaign-1',
      42,
      'template-1',
      { signal: controller.signal },
    );

    expect(result.type).toBe('application/pdf');
    expect(result.size).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/campaigns/campaign-1/email-chain/preview/attachment?recipient_id=42&template_id=template-1&preview=1',
      { credentials: 'include', signal: controller.signal },
    );
  });

  it('keeps downloads in the actual delivery format', () => {
    expect(
      campaignsApi.previewEmailChainAttachmentUrl('campaign-1', 42, 'template-1', { download: true }),
    ).toBe(
      '/api/v1/campaigns/campaign-1/email-chain/preview/attachment?recipient_id=42&template_id=template-1&download=1',
    );
  });
});
