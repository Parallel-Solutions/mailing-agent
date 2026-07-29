export function formatElapsedTime(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safeSeconds / 60);
  const seconds = safeSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function operationStageIndex(
  elapsedSeconds: number,
  stagesCount: number,
  estimatedMaxSeconds: number,
): number {
  if (stagesCount <= 1) return 0;
  const stageDuration = Math.max(2, estimatedMaxSeconds / stagesCount);
  return Math.min(stagesCount - 1, Math.floor(elapsedSeconds / stageDuration));
}
