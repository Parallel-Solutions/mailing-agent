import { describe, expect, it } from 'vitest';
import { formatLocalDateTime, parseApiDateTime } from './dateTime';

describe('dateTime', () => {
  it('interprets legacy timestamps without offset as Moscow time', () => {
    expect(parseApiDateTime('2026-07-27 12:30:00')?.toISOString()).toBe(
      '2026-07-27T09:30:00.000Z',
    );
  });

  it('returns an em dash for missing or invalid values', () => {
    expect(formatLocalDateTime()).toBe('—');
    expect(formatLocalDateTime('not-a-date')).toBe('—');
  });
});
