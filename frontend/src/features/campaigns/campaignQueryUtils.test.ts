import { describe, expect, it, vi } from 'vitest';
import {
  buildCampaignAutosavePayload,
  buildCampaignChainSelectionPatch,
  buildValidationSignature,
  campaignEmailValidationQueryKey,
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

describe('buildCampaignChainSelectionPatch', () => {
  it('switches to the chain scenario when a chain is selected', () => {
    expect(buildCampaignChainSelectionPatch(' chain-1 ')).toEqual({
      email_chain_id: 'chain-1',
      send_scenario: 'email_chain',
    });
  });

  it('detaches the chain and restores the default scenario when cleared', () => {
    expect(buildCampaignChainSelectionPatch(undefined)).toEqual({
      email_chain_id: null,
      send_scenario: 'consent_then_materials',
    });
    expect(buildCampaignChainSelectionPatch('   ')).toEqual({
      email_chain_id: null,
      send_scenario: 'consent_then_materials',
    });
  });
});

describe('campaignValidateQueryKey', () => {
  it('builds a prefix key for invalidating every campaign validation', () => {
    expect(campaignValidateQueryKey('camp-1')).toEqual(['campaign-validate', 'camp-1']);
  });

  it('isolates cached validation results by state signature', () => {
    expect(campaignValidateQueryKey('camp-1', 'revision-2')).toEqual([
      'campaign-validate',
      'camp-1',
      'revision-2',
    ]);
  });
});

describe('campaignEmailValidationQueryKey', () => {
  it('builds the shared SMTP.BZ progress key', () => {
    expect(campaignEmailValidationQueryKey('camp-1')).toEqual([
      'campaign-email-validation',
      'camp-1',
    ]);
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
  it('changes every time a confirmed mapping is saved', () => {
    const first = buildValidationSignature({
      recipientCount: 1,
      mappingConfirmed: true,
      mappingConfirmedAt: '2026-07-30T12:00:00+00:00',
    });
    const second = buildValidationSignature({
      recipientCount: 1,
      mappingConfirmed: true,
      mappingConfirmedAt: '2026-07-30T12:01:00+00:00',
    });
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

  it('invalidates SMTP.BZ progress after recipient changes', () => {
    const invalidateQueries = vi.fn();
    const queryClient = { invalidateQueries } as never;

    invalidateCampaignDerivedData(queryClient, 'camp-1', {
      includeEmailValidation: true,
    });

    expect(invalidateQueries).toHaveBeenCalledTimes(3);
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['campaign-email-validation', 'camp-1'],
    });
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
