import { describe, expect, it } from 'vitest';
import { findDuplicateEmails, isValidEmail, validateCampaignBasics } from './validators';

describe('validators', () => {
  it('validates emails', () => {
    expect(isValidEmail('a@b.co')).toBe(true);
    expect(isValidEmail('bad')).toBe(false);
  });

  it('validates campaign basics', () => {
    expect(validateCampaignBasics({})).toContain('Укажите название рассылки');
    expect(validateCampaignBasics({ name: 'A' })).toContain('Выберите цепочку писем');
    expect(validateCampaignBasics({ name: 'A', email_chain_id: 'chain-1' })).toEqual([]);
  });

  it('finds duplicates', () => {
    expect(findDuplicateEmails(['a@b.co', 'A@b.co', 'c@d.co'])).toEqual(['a@b.co']);
  });
});
