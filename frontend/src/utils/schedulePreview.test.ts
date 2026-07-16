import { describe, expect, it } from 'vitest';
import { computeLocalSchedulePreview } from './schedulePreview';

describe('schedulePreview', () => {
  it('splits recipients into batches', () => {
    const preview = computeLocalSchedulePreview({
      recipientCount: 55,
      batchSize: 25,
      intervalSeconds: 300,
    });
    expect(preview.batchCount).toBe(3);
    expect(preview.batches[2].size).toBe(5);
    expect(preview.estimatedDurationSeconds).toBe(600);
  });
});
