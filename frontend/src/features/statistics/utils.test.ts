import { describe, expect, it } from 'vitest';
import { asRecordArray, companyEmailsText, companyField, fmt } from './utils';

describe('statistics utils', () => {
  it('formats numbers', () => {
    expect(fmt(1200)).toMatch(/1/);
    expect(fmt(null)).toBe('0');
  });

  it('reads company fields and emails', () => {
    const item = {
      company: { fields: { region: { display: 'Москва' } } },
      emails: [{ email: 'a@example.com' }, { email: 'b@example.com' }],
    };
    expect(companyField(item, 'region')).toBe('Москва');
    expect(companyEmailsText(item)).toBe('a@example.com, b@example.com');
  });

  it('normalizes arrays', () => {
    expect(asRecordArray([{ a: 1 }])).toHaveLength(1);
    expect(asRecordArray(null)).toEqual([]);
  });
});
