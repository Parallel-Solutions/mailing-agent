import { describe, expect, it } from 'vitest';
import {
  formValuesToSchedulePayload,
  intervalFromSeconds,
  intervalToSeconds,
  scheduleToFormValues,
} from './scheduleForm';
import dayjs from 'dayjs';

describe('scheduleForm', () => {
  it('converts hours and days to seconds', () => {
    expect(intervalToSeconds(2, 'hours')).toBe(7200);
    expect(intervalToSeconds(1, 'days')).toBe(86400);
  });

  it('restores unit from seconds', () => {
    expect(intervalFromSeconds(86400)).toEqual({ interval_value: 1, interval_unit: 'days' });
    expect(intervalFromSeconds(7200)).toEqual({ interval_value: 2, interval_unit: 'hours' });
    expect(intervalFromSeconds(300)).toEqual({ interval_value: 1, interval_unit: 'hours' });
  });

  it('builds API payload without UI-only fields', () => {
    const start = dayjs('2026-07-16T12:00:00.000Z');
    const payload = formValuesToSchedulePayload({
      batch_size: 25,
      start_at: start,
      interval_value: 1,
      interval_unit: 'days',
    });
    expect(payload).toEqual({
      batch_size: 25,
      start_at: start.toISOString(),
      send_immediately: false,
      interval_seconds: 86400,
    });
  });

  it('maps schedule to form values', () => {
    const values = scheduleToFormValues({
      batch_size: 10,
      start_at: '2026-07-16T12:00:00.000Z',
      interval_seconds: 3600,
    });
    expect(values.batch_size).toBe(10);
    expect(values.interval_value).toBe(1);
    expect(values.interval_unit).toBe('hours');
    expect(values.start_at.toISOString()).toBe('2026-07-16T12:00:00.000Z');
  });
});
