import { describe, expect, it } from 'vitest';
import { emailValidationReason } from './emailValidation';

describe('emailValidationReason', () => {
  it('returns the reason for the recipient status', () => {
    expect(emailValidationReason({
      validation_status: 'unknown',
      extra: {
        email_validation: {
          candidates: [
            { status: 'valid', reason: '' },
            { status: 'unknown', reason: 'SMTP.BZ timed out' },
          ],
        },
      },
    })).toBe('SMTP.BZ timed out');
  });

  it('returns an empty string when no reason is stored', () => {
    expect(emailValidationReason({
      validation_status: 'pending',
      extra: { email_validation: { candidates: [] } },
    })).toBe('');
  });
});
