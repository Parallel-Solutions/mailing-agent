import { describe, expect, it } from 'vitest';
import {
  formValuesToSchedulePayload,
  intervalFromSeconds,
  intervalToSeconds,
  parseScheduleDateTime,
  resolveScheduleFormValues,
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
    const start = dayjs().add(1, 'day');
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

  it.each([
    ['10.08.2027 13:59', '10.08.2027 13:59'],
    ['08.10.2027 13:59', '08.10.2027 13:59'],
    ['13.08.2027 13:59', '13.08.2027 13:59'],
  ])('parses manual date input as day, month, year: %s', (input, expected) => {
    expect(parseScheduleDateTime(input)?.format('DD.MM.YYYY HH:mm')).toBe(expected);
  });

  it('accepts ISO dates from the API and rejects ambiguous unsupported strings', () => {
    expect(parseScheduleDateTime('2027-08-13T10:59:00.000Z')?.isValid()).toBe(true);
    expect(parseScheduleDateTime('08/13/2027 13:59')).toBeNull();
  });

  it.each([1, 7, 24])('accepts a positive batch size below the old default: %s', (batchSize) => {
    const payload = formValuesToSchedulePayload({
      batch_size: batchSize,
      start_at: dayjs().add(1, 'day'),
      interval_value: 1,
      interval_unit: 'hours',
    });

    expect(payload?.batch_size).toBe(batchSize);
  });

  it.each([undefined, 0, -1, 1.5])('does not build a payload for invalid batch size: %s', (batchSize) => {
    const payload = formValuesToSchedulePayload({
      batch_size: batchSize,
      start_at: dayjs().add(1, 'day'),
      interval_value: 1,
      interval_unit: 'hours',
    });

    expect(payload).toBeNull();
  });

  it('clamps past start_at to now in payload', () => {
    const past = dayjs().subtract(2, 'year');
    const payload = formValuesToSchedulePayload({
      batch_size: 25,
      start_at: past,
      interval_value: 1,
      interval_unit: 'hours',
    });
    expect(payload).not.toBeNull();
    expect(dayjs(payload!.start_at).isBefore(dayjs().subtract(1, 'minute'))).toBe(false);
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

  describe('resolveScheduleFormValues', () => {
    it('falls back to saved schedule values when the watched form is an empty object', () => {
      // Form.useWatch([], form) resolves to {} (not undefined) while the
      // Schedule step's form panel is unmounted — a plain `||` fallback
      // treats {} as truthy and never falls back (the bug this guards).
      const fallback = scheduleToFormValues({
        batch_size: 10,
        start_at: '2026-07-16T12:00:00.000Z',
        interval_seconds: 86400,
      });

      const resolved = resolveScheduleFormValues({}, fallback);

      expect(resolved.batch_size).toBe(10);
      expect(resolved.interval_value).toBe(1);
      expect(resolved.interval_unit).toBe('days');
      expect(resolved.start_at.toISOString()).toBe('2026-07-16T12:00:00.000Z');
    });

    it('fills only the fields missing from a partially mounted form', () => {
      const fallback = scheduleToFormValues(undefined);

      const resolved = resolveScheduleFormValues({ batch_size: 7 }, fallback);

      expect(resolved.batch_size).toBe(7);
      expect(resolved.interval_value).toBe(fallback.interval_value);
      expect(resolved.interval_unit).toBe(fallback.interval_unit);
      expect(resolved.start_at).toBe(fallback.start_at);
    });

    it('keeps a value the user actually cleared', () => {
      const fallback = scheduleToFormValues(undefined);

      const resolved = resolveScheduleFormValues(
        { start_at: null } as unknown as Partial<import('./scheduleForm').ScheduleFormValues>,
        fallback,
      );

      expect(resolved.start_at).toBeNull();
    });

    it('treats an undefined watch value as untouched', () => {
      const fallback = scheduleToFormValues(undefined);

      expect(resolveScheduleFormValues(undefined, fallback)).toEqual(fallback);
    });
  });
});
