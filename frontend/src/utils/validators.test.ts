import { describe, expect, it } from 'vitest';
import { findDuplicateEmails, isValidEmail, validateCampaignBasics } from './validators';

describe('validators', () => {
  it('validates emails', () => {
    expect(isValidEmail('a@b.co')).toBe(true);
    expect(isValidEmail('bad')).toBe(false);
  });

  it('validates campaign basics', () => {
    expect(validateCampaignBasics({})).toEqual(
      expect.arrayContaining([
        'Укажите название рассылки',
        'Выберите цепочку писем',
        'Выберите компанию',
        'Выберите вид работ',
      ]),
    );
    expect(validateCampaignBasics({ name: 'A' })).toEqual(
      expect.arrayContaining(['Выберите цепочку писем', 'Выберите компанию', 'Выберите вид работ']),
    );
    expect(
      validateCampaignBasics({
        name: 'A',
        email_chain_id: 'chain-1',
        company_id: 'company-1',
        company_work_type_id: 'work-1',
      }),
    ).toEqual([]);
    expect(
      validateCampaignBasics({
        name: 'A',
        email_chain_id: 'chain-1',
        draft_payload: { company_id: 'company-1', company_work_type_id: 'work-1' },
      }),
    ).toEqual([]);
  });

  it('finds duplicates', () => {
    expect(findDuplicateEmails(['a@b.co', 'A@b.co', 'c@d.co'])).toEqual(['a@b.co']);
  });
});
