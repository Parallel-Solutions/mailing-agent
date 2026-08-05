import { describe, expect, it } from 'vitest';
import { findDuplicateEmails, isValidEmail, validateCampaignBasics } from './validators';

describe('validators', () => {
  it('validates emails', () => {
    expect(isValidEmail('a@b.co')).toBe(true);
    expect(isValidEmail('bad')).toBe(false);
  });

  it('validates only fields required by the launch API', () => {
    expect(validateCampaignBasics({})).toEqual(['Укажите название рассылки']);
    expect(validateCampaignBasics({ name: 'A' })).toEqual([]);
    expect(
      validateCampaignBasics({ name: 'A', send_scenario: 'email_chain' }),
    ).toEqual(['Выберите цепочку писем']);
    expect(
      validateCampaignBasics({
        name: 'A',
        send_scenario: 'email_chain',
        email_chain_id: 'chain-1',
      }),
    ).toEqual([]);
    expect(
      validateCampaignBasics({
        name: 'A',
        send_scenario: 'email_chain',
        draft_payload: {
          email_chain: {
            nodes: [{ id: 'root' }],
          },
        },
      }),
    ).toEqual([]);
    expect(
      validateCampaignBasics({ name: 'A', send_scenario: 'materials_now' }),
    ).toEqual([]);
  });

  it('finds duplicates', () => {
    expect(findDuplicateEmails(['a@b.co', 'A@b.co', 'c@d.co'])).toEqual(['a@b.co']);
  });
});
