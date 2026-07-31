import { describe, expect, it } from 'vitest';
import { DRILLDOWN_CONFIG } from './drilldownConfig';

describe('statistics drilldown presentation', () => {
  it('renders consent details as cards instead of a wide table', () => {
    const config = DRILLDOWN_CONFIG.consents;

    expect(config.layout).toBe('cards');
    expect(config.columns.map(([title]) => title)).toEqual([
      'Компания',
      'Контакт',
      'Email',
      'Статус согласия',
      'Материалы',
      'Последнее действие',
      'Дата',
      'Интерес',
      'Следующее действие',
    ]);
  });

  it('uses cards with resend actions for both error drilldowns', () => {
    expect(DRILLDOWN_CONFIG.problems.layout).toBe('error-cards');
    expect(DRILLDOWN_CONFIG.errors.layout).toBe('error-cards');
  });

  it('uses the same readable consent layout for the materials drilldown', () => {
    expect(DRILLDOWN_CONFIG.materials.columns).toBe(DRILLDOWN_CONFIG.consents.columns);
    expect(DRILLDOWN_CONFIG.materials.layout).toBe('cards');
  });
});
