import { describe, expect, it } from 'vitest';
import { findDuplicateEmails, isValidEmail, validateCampaignBasics } from './validators';

describe('validators', () => {
  it('validates emails', () => {
    expect(isValidEmail('a@b.co')).toBe(true);
    expect(isValidEmail('bad')).toBe(false);
  });

  it('validates campaign basics', () => {
    expect(validateCampaignBasics({})).toContain('Укажите название рассылки');
    expect(validateCampaignBasics({ name: 'A', mail_subject: 'S' })).toEqual([]);
    expect(
      validateCampaignBasics({ name: 'A', mail_subject: 'S', send_scenario: 'materials_now' }),
    ).toEqual([]);
    expect(
      validateCampaignBasics({ name: 'A', mail_subject: 'S', send_scenario: 'email_chain' }),
    ).toEqual([]);
    expect(
      validateCampaignBasics({ name: 'A', mail_subject: 'S', send_scenario: 'immediate_now' }),
    ).toContain('Некорректный сценарий отправки');
  });

  it('finds duplicates', () => {
    expect(findDuplicateEmails(['a@b.co', 'A@b.co', 'c@d.co'])).toEqual(['a@b.co']);
  });
});
