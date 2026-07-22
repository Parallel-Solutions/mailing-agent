import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { campaignsApi } from '@/api/campaigns';
import { isMappingAutoSavable, runMappingAutoSuggest } from '@/features/campaigns/mappingAutoSuggestUtils';

vi.mock('@/api/campaigns', () => ({
  campaignsApi: {
    getVariableMapping: vi.fn(),
    suggestVariableMapping: vi.fn(),
    saveVariableMapping: vi.fn(),
  },
}));

describe('isMappingAutoSavable', () => {
  it('returns true when suggest status is complete', () => {
    expect(
      isMappingAutoSavable({
        status: 'complete',
        template_variables: [{ name: 'COMPANY' }],
        recipient_columns: ['company'],
        suggested_mapping: { COMPANY: 'company' },
        unmapped: [],
      }),
    ).toBe(true);
  });

  it('returns false when recipient mapping is incomplete', () => {
    expect(
      isMappingAutoSavable({
        status: 'needs_review',
        template_variables: [{ name: 'COMPANY' }, { name: 'EMAIL' }],
        recipient_columns: ['company', 'email'],
        suggested_mapping: { COMPANY: 'company' },
        unmapped: ['EMAIL'],
      }),
    ).toBe(false);
  });
});

describe('runMappingAutoSuggest', () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const signature = '{"r":1,"c":"","m":false,"t":["","",""]}';

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
  });

  it('returns true without suggest when mapping is already confirmed', async () => {
    vi.mocked(campaignsApi.getVariableMapping).mockResolvedValue({
      mapping_confirmed: true,
      variable_mapping: { COMPANY: 'company' },
      recipient_columns: ['company'],
      template_variables: [{ name: 'COMPANY' }],
    });

    const saved = await runMappingAutoSuggest('camp-1', queryClient, signature);

    expect(saved).toBe(true);
    expect(campaignsApi.suggestVariableMapping).not.toHaveBeenCalled();
    expect(campaignsApi.saveVariableMapping).not.toHaveBeenCalled();
  });

  it('auto-saves mapping when suggest is complete', async () => {
    vi.mocked(campaignsApi.getVariableMapping).mockResolvedValue({
      mapping_confirmed: false,
      variable_mapping: {},
      recipient_columns: ['company'],
      template_variables: [{ name: 'COMPANY' }],
    });
    vi.mocked(campaignsApi.suggestVariableMapping).mockResolvedValue({
      status: 'complete',
      template_variables: [{ name: 'COMPANY' }],
      recipient_columns: ['company'],
      suggested_mapping: { COMPANY: 'company' },
      unmapped: [],
    });
    vi.mocked(campaignsApi.saveVariableMapping).mockResolvedValue({
      mapping_confirmed: true,
      variable_mapping: { COMPANY: 'company' },
    });

    const saved = await runMappingAutoSuggest('camp-1', queryClient, signature);

    expect(saved).toBe(true);
    expect(campaignsApi.suggestVariableMapping).toHaveBeenCalledWith('camp-1');
    expect(campaignsApi.saveVariableMapping).toHaveBeenCalledWith('camp-1', { COMPANY: 'company' });
  });

  it('returns false when mapping cannot be auto-saved', async () => {
    vi.mocked(campaignsApi.getVariableMapping).mockResolvedValue({
      mapping_confirmed: false,
      variable_mapping: {},
      recipient_columns: ['company'],
      template_variables: [{ name: 'COMPANY' }],
    });
    vi.mocked(campaignsApi.suggestVariableMapping).mockResolvedValue({
      status: 'needs_review',
      template_variables: [{ name: 'COMPANY' }],
      recipient_columns: ['company'],
      suggested_mapping: {},
      unmapped: ['COMPANY'],
    });

    const saved = await runMappingAutoSuggest('camp-1', queryClient, signature);

    expect(saved).toBe(false);
    expect(campaignsApi.saveVariableMapping).not.toHaveBeenCalled();
  });

  it('reuses cached suggest for the same signature', async () => {
    vi.mocked(campaignsApi.getVariableMapping).mockResolvedValue({
      mapping_confirmed: false,
      variable_mapping: {},
      recipient_columns: ['company'],
      template_variables: [{ name: 'COMPANY' }],
    });
    vi.mocked(campaignsApi.suggestVariableMapping).mockResolvedValue({
      status: 'needs_review',
      template_variables: [{ name: 'COMPANY' }],
      recipient_columns: ['company'],
      suggested_mapping: {},
      unmapped: ['COMPANY'],
    });

    await runMappingAutoSuggest('camp-1', queryClient, signature);
    await runMappingAutoSuggest('camp-1', queryClient, signature);

    expect(campaignsApi.suggestVariableMapping).toHaveBeenCalledTimes(1);
  });
});
