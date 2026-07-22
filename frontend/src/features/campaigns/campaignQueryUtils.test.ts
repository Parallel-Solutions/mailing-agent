import { describe, expect, it, vi } from 'vitest';
import {
  campaignValidateQueryKey,
  invalidateCampaignDerivedData,
  resolveLinkedChainId,
} from '@/features/campaigns/campaignQueryUtils';

describe('campaignValidateQueryKey', () => {
  it('builds stable query key', () => {
    expect(campaignValidateQueryKey('camp-1')).toEqual(['campaign-validate', 'camp-1']);
  });
});

describe('invalidateCampaignDerivedData', () => {
  it('invalidates preview and recipients by default', () => {
    const invalidateQueries = vi.fn();
    const queryClient = { invalidateQueries } as never;

    invalidateCampaignDerivedData(queryClient, 'camp-1');

    expect(invalidateQueries).toHaveBeenCalledTimes(2);
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['email-chain-preview', 'camp-1'] });
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['campaign-recipients', 'camp-1'] });
  });

  it('invalidates validate when includeValidation is true', () => {
    const invalidateQueries = vi.fn();
    const queryClient = { invalidateQueries } as never;

    invalidateCampaignDerivedData(queryClient, 'camp-1', { includeValidation: true });

    expect(invalidateQueries).toHaveBeenCalledTimes(3);
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: ['campaign-validate', 'camp-1'] });
  });
});

describe('resolveLinkedChainId', () => {
  it('prefers form value over draft', () => {
    expect(resolveLinkedChainId('form-chain', 'draft-chain')).toBe('form-chain');
  });

  it('falls back to draft when form is empty', () => {
    expect(resolveLinkedChainId(undefined, 'draft-chain')).toBe('draft-chain');
    expect(resolveLinkedChainId('', null)).toBeUndefined();
  });

  it('ignores url param semantics — only form and draft', () => {
    expect(resolveLinkedChainId(undefined, undefined)).toBeUndefined();
  });
});
