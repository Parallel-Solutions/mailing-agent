import { describe, expect, it } from 'vitest';
import { emailValidationReason, localEmailValidationStatusLabel } from './emailValidation';

describe('emailValidationReason', () => {
  it('returns the reason for the recipient status', () => {
    expect(emailValidationReason({
      validation_status: 'unknown',
      extra: {
        email_validation: {
          candidates: [
            { status: 'valid', reason: '' },
            { status: 'unknown', reason: 'DNS timed out' },
          ],
        },
      },
    })).toBe('DNS timed out');
  });

  it('returns an empty string when no reason is stored', () => {
    expect(emailValidationReason({
      validation_status: 'pending',
      extra: { email_validation: { candidates: [] } },
    })).toBe('');
  });

  it('uses labels for the local format and DNS/MX check', () => {
    expect(localEmailValidationStatusLabel('valid')).toBe('Формат и DNS/MX корректны');
    expect(localEmailValidationStatusLabel('unknown')).toBe('Не удалось проверить DNS/MX');
  });
});
