import { describe, expect, it, vi } from 'vitest';
import {
  buildCampaignAutosavePayload,
  buildValidationSignature,
  campaignValidateQueryKey,
  invalidateCampaignDerivedData,
  resolveLinkedChainId,
} from '@/features/campaigns/campaignQueryUtils';

describe('buildCampaignAutosavePayload', () => {
  it('sends only the current patch to draft_payload', () => {
    expect(buildCampaignAutosavePayload({ name: 'Updated campaign' })).toEqual({
      name: 'Updated campaign',
      draft_payload: { name: 'Updated campaign' },
    });
  });

  it('does not forward a stale nested draft snapshot', () => {
    expect(
      buildCampaignAutosavePayload({
        description: 'Updated',
        draft_payload: {
          mapping_confirmed: false,
          variable_mapping: {},
        },
      }),
    ).toEqual({
      description: 'Updated',
      draft_payload: { description: 'Updated' },
    });
  });
});

describe('campaignValidateQueryKey', () => {
  it('builds stable query key', () => {
    expect(campaignValidateQueryKey('camp-1')).toEqual(['campaign-validate', 'camp-1']);
  });
});

describe('buildValidationSignature', () => {
  it('changes when company work type changes', () => {
    const base = {
      recipientCount: 1,
      emailChainId: 'chain-1',
      companyId: 'company-1',
    };

    expect(
      buildValidationSignature({ ...base, companyWorkTypeId: 'work-1' }),
    ).not.toBe(
      buildValidationSignature({ ...base, companyWorkTypeId: 'work-2' }),
    );
  });

  it('changes when company changes', () => {
    const first = buildValidationSignature({ recipientCount: 1, companyId: 'company-1' });
    const second = buildValidationSignature({ recipientCount: 1, companyId: 'company-2' });
    expect(first).not.toBe(second);
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
