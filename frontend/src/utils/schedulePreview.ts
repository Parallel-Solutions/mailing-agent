export type PreviewInput = {
  recipientCount: number;
  batchSize: number;
  intervalSeconds: number;
  maxPerHour?: number;
  maxPerDay?: number;
};

export type PreviewBatch = {
  batchIndex: number;
  size: number;
  offsetSeconds: number;
};

export function computeLocalSchedulePreview(input: PreviewInput): {
  batchCount: number;
  batches: PreviewBatch[];
  estimatedDurationSeconds: number;
} {
  const count = Math.max(0, input.recipientCount);
  let size = Math.max(1, input.batchSize || 25);
  if (input.maxPerHour && input.maxPerHour > 0) size = Math.min(size, input.maxPerHour);
  if (input.maxPerDay && input.maxPerDay > 0) size = Math.min(size, input.maxPerDay);
  const interval = Math.max(0, input.intervalSeconds || 0);
  const batches: PreviewBatch[] = [];
  let remaining = count;
  let index = 0;
  while (remaining > 0) {
    const take = Math.min(remaining, size);
    batches.push({ batchIndex: index, size: take, offsetSeconds: index * interval });
    remaining -= take;
    index += 1;
  }
  const lastOffset = batches.length ? batches[batches.length - 1].offsetSeconds : 0;
  return {
    batchCount: batches.length,
    batches,
    estimatedDurationSeconds: lastOffset,
  };
}
