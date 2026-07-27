import { describe, expect, it } from 'vitest';
import { errorLabel, providerLabel, scenarioLabel, statusLabel } from './presentation';

describe('user-facing presentation', () => {
  it('translates known technical statuses and scenarios', () => {
    expect(statusLabel('retry')).toBe('Повторная попытка');
    expect(scenarioLabel('email_chain')).toBe('Цепочка писем');
    expect(providerLabel('smtp')).toBe('Почтовый ящик');
  });

  it('does not expose an unknown technical error', () => {
    expect(errorLabel('internal_code_9f5c')).toBe('Техническая ошибка отправки');
  });
});
