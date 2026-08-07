import { describe, expect, it } from 'vitest';
import { emailValidationRefetchInterval } from './emailValidationPolling';

describe('emailValidationRefetchInterval', () => {
  it.each(['queued', 'running', 'RUNNING'])('polls active status %s', (status) => {
    expect(emailValidationRefetchInterval(status)).toBe(3000);
  });

  it.each([undefined, 'not_started', 'completed', 'failed', 'stale'])(
    'stops polling terminal status %s',
    (status) => {
      expect(emailValidationRefetchInterval(status)).toBe(false);
    },
  );
});
