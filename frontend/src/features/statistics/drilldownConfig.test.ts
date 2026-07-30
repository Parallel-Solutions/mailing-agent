import { describe, expect, it } from 'vitest';
import { DRILLDOWN_CONFIG } from './drilldownConfig';

describe('statistics drilldown presentation', () => {
  it('gives every consent column enough room and marks status fields', () => {
    const config = DRILLDOWN_CONFIG.consents;
    const optionsByTitle = Object.fromEntries(
      config.columns.map(([title, , options]) => [title, options]),
    );

    expect(config.tableWidth).toBeGreaterThanOrEqual(1500);
    expect(config.columns.every(([, , options]) => Number(options?.width) >= 100)).toBe(true);
    expect(optionsByTitle['Компания']?.ellipsis).toBe(true);
    expect(optionsByTitle['Email']?.ellipsis).toBe(true);
    expect(optionsByTitle['Статус согласия']?.display).toBe('status');
    expect(optionsByTitle['Материалы']?.display).toBe('status');
    expect(optionsByTitle['Следующее действие']?.display).toBe('status');
  });

  it('uses the same readable consent layout for the materials drilldown', () => {
    expect(DRILLDOWN_CONFIG.materials.columns).toBe(DRILLDOWN_CONFIG.consents.columns);
    expect(DRILLDOWN_CONFIG.materials.tableWidth).toBe(DRILLDOWN_CONFIG.consents.tableWidth);
  });
});
