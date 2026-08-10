import dayjs, { type Dayjs } from 'dayjs';
import customParseFormat from 'dayjs/plugin/customParseFormat';
import { formatLocalDateTime } from '@/utils/dateTime';

dayjs.extend(customParseFormat);

export type IntervalUnit = 'hours' | 'days';
export const SCHEDULE_DATE_TIME_FORMAT = 'DD.MM.YYYY HH:mm';

const ISO_DATE_TIME_PATTERN = /^\d{4}-\d{2}-\d{2}T/;

export type ScheduleFormValues = {
  batch_size: number;
  start_at: Dayjs;
  interval_value: number;
  interval_unit: IntervalUnit;
};

export function isPositiveInteger(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return false;
  const number = Number(value);
  return Number.isInteger(number) && number > 0;
}

export function parseScheduleDateTime(value: Dayjs | string | null | undefined): Dayjs | null {
  if (dayjs.isDayjs(value)) return value.isValid() ? value : null;
  if (typeof value !== 'string') return null;

  const normalized = value.trim();
  if (!normalized) return null;
  const parsed = ISO_DATE_TIME_PATTERN.test(normalized)
    ? dayjs(normalized)
    : dayjs(normalized, SCHEDULE_DATE_TIME_FORMAT, true);
  return parsed.isValid() ? parsed : null;
}

export function intervalFromSeconds(seconds: number): {
  interval_value: number;
  interval_unit: IntervalUnit;
} {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  if (s > 0 && s % 86400 === 0) {
    return { interval_value: s / 86400, interval_unit: 'days' };
  }
  if (s > 0 && s % 3600 === 0) {
    return { interval_value: s / 3600, interval_unit: 'hours' };
  }
  return { interval_value: Math.max(1, Math.round(s / 3600) || 1), interval_unit: 'hours' };
}

export function intervalToSeconds(value: number, unit: IntervalUnit): number {
  const n = Math.max(1, Math.floor(Number(value) || 1));
  return unit === 'days' ? n * 86400 : n * 3600;
}

export function formatScheduleDateTime(iso?: string | null, _timezone?: string): string {
  return formatLocalDateTime(iso);
}

export function scheduleToFormValues(schedule?: {
  batch_size?: number;
  start_at?: string | null;
  interval_seconds?: number;
} | null): ScheduleFormValues {
  const { interval_value, interval_unit } = intervalFromSeconds(schedule?.interval_seconds ?? 3600);
  return {
    batch_size: schedule?.batch_size ?? 25,
    start_at: schedule?.start_at ? dayjs(schedule.start_at) : dayjs(),
    interval_value,
    interval_unit,
  };
}

export function formValuesToSchedulePayload(values: {
  batch_size?: number;
  start_at?: Dayjs | string | null;
  interval_value?: number;
  interval_unit?: IntervalUnit;
}): {
  batch_size: number;
  start_at: string;
  send_immediately: false;
  interval_seconds: number;
} | null {
  if (!isPositiveInteger(values.batch_size)) return null;
  const start = parseScheduleDateTime(values.start_at);
  if (!start) return null;
  const effective = start.isBefore(dayjs()) ? dayjs() : start;
  return {
    batch_size: Number(values.batch_size),
    start_at: effective.toISOString(),
    send_immediately: false,
    interval_seconds: intervalToSeconds(
      Number(values.interval_value) || 1,
      values.interval_unit === 'days' ? 'days' : 'hours',
    ),
  };
}
