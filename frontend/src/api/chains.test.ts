import { afterEach, describe, expect, it, vi } from 'vitest';
import { chainsApi } from './chains';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('chainsApi.remove', () => {
  it('sends DELETE for the selected chain', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ status: 'ok', result: { deleted: true, id: 'chain-1' } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(chainsApi.remove('chain-1')).resolves.toEqual({ deleted: true, id: 'chain-1' });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/chains/chain-1',
      expect.objectContaining({ method: 'DELETE', credentials: 'include' }),
    );
  });
});