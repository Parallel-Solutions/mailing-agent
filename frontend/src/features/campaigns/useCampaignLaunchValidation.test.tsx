import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { campaignsApi } from '@/api/campaigns';
import { campaignValidateQueryKey } from '@/features/campaigns/campaignQueryUtils';
import { useCampaignLaunchValidation } from '@/features/campaigns/useCampaignLaunchValidation';

vi.mock('@/api/campaigns', () => ({
  campaignsApi: {
    validate: vi.fn(),
  },
}));

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

const validationSignature = '{"r":1,"c":"","m":true,"s":"","a":"","t":["","",""]}';

describe('useCampaignLaunchValidation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('does not fetch when step is not 4', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const flush = vi.fn().mockResolvedValue(undefined);

    renderHook(
      () =>
        useCampaignLaunchValidation({
          campaignId: 'camp-1',
          step: 3,
          validationSignature,
          flushPendingChanges: flush,
          queryClient,
        }),
      { wrapper: createWrapper(queryClient) },
    );

    await waitFor(() => {
      expect(flush).not.toHaveBeenCalled();
      expect(campaignsApi.validate).not.toHaveBeenCalled();
    });
  });

  it('flushes pending changes and fetches validate when entering step 4', async () => {
    vi.mocked(campaignsApi.validate).mockResolvedValue({
      ok: true,
      errors: [],
      warnings: ['warn'],
      template_issues: [],
      active_recipients: 1,
      mapping_confirmed: true,
      excluded_recipients: 0,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const flush = vi.fn().mockResolvedValue(undefined);

    const { result } = renderHook(
      () =>
        useCampaignLaunchValidation({
          campaignId: 'camp-1',
          step: 4,
          validationSignature,
          flushPendingChanges: flush,
          queryClient,
        }),
      { wrapper: createWrapper(queryClient) },
    );

    await waitFor(() => {
      expect(result.current.hasChecked).toBe(true);
    });

    expect(flush).toHaveBeenCalledTimes(1);
    expect(campaignsApi.validate).toHaveBeenCalledWith('camp-1', { deep: true });
    expect(result.current.data?.warnings).toEqual(['warn']);
  });

  it('does not refetch validate when signature is unchanged and cache exists', async () => {
    vi.mocked(campaignsApi.validate).mockResolvedValue({
      ok: true,
      errors: [],
      warnings: ['warn'],
      template_issues: [],
      active_recipients: 1,
      mapping_confirmed: true,
      excluded_recipients: 0,
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    queryClient.setQueryData(campaignValidateQueryKey('camp-1'), {
      ok: true,
      errors: [],
      warnings: ['cached'],
      template_issues: [],
      active_recipients: 1,
      mapping_confirmed: true,
      excluded_recipients: 0,
    });
    const flush = vi.fn().mockResolvedValue(undefined);

    const { result, rerender } = renderHook(
      ({ step }) =>
        useCampaignLaunchValidation({
          campaignId: 'camp-1',
          step,
          validationSignature,
          flushPendingChanges: flush,
          queryClient,
        }),
      {
        wrapper: createWrapper(queryClient),
        initialProps: { step: 3 },
      },
    );

    rerender({ step: 4 });

    await waitFor(() => {
      expect(result.current.hasChecked).toBe(true);
    });

    expect(campaignsApi.validate).not.toHaveBeenCalled();
    expect(result.current.data?.warnings).toEqual(['cached']);

    rerender({ step: 3 });
    rerender({ step: 4 });

    await waitFor(() => {
      expect(result.current.hasChecked).toBe(true);
    });

    expect(campaignsApi.validate).not.toHaveBeenCalled();
  });
});
