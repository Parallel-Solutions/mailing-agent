import dayjs, { type Dayjs } from 'dayjs';
import { formatLocalDateTime } from '@/utils/dateTime';

export type IntervalUnit = 'hours' | 'days';

export type ScheduleFormValues = {
  batch_size: number;
  start_at: Dayjs;
  interval_value: number;
  interval_unit: IntervalUnit;
};

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
  if (values.start_at == null || values.start_at === '') return null;
  const start = dayjs(values.start_at);
  if (!start.isValid()) return null;
  const effective = start.isBefore(dayjs()) ? dayjs() : start;
  return {
    batch_size: Math.max(1, Math.floor(Number(values.batch_size) || 25)),
    start_at: effective.toISOString(),
    send_immediately: false,
    interval_seconds: intervalToSeconds(
      Number(values.interval_value) || 1,
      values.interval_unit === 'days' ? 'days' : 'hours',
    ),
  };
}
