import { describe, expect, it } from 'vitest';
import { formatElapsedTime, operationStageIndex } from './operationProgressUtils';

describe('OperationProgress helpers', () => {
  it('formats elapsed time as minutes and seconds', () => {
    expect(formatElapsedTime(0)).toBe('00:00');
    expect(formatElapsedTime(65)).toBe('01:05');
  });

  it('advances estimated stages without exceeding the last stage', () => {
    expect(operationStageIndex(0, 4, 20)).toBe(0);
    expect(operationStageIndex(6, 4, 20)).toBe(1);
    expect(operationStageIndex(120, 4, 20)).toBe(3);
  });
});
