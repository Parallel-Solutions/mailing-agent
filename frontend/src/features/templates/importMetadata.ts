import type { ImportRefinementState } from '@/api/types';

const IMPORT_SOURCE_LABELS: Record<string, string> = {
  fixed_layout: 'Fixed layout (PDF24-style)',
  docxjs: 'DOCX preview (docxjs)',
  html: 'HTML-файл',
  txt: 'Текстовый файл',
  vision_iterative: 'AI vision (итеративно)',
};

const STOP_REASON_LABELS: Record<string, string> = {
  target_score: 'Достигнут целевой score — AI не дорабатывал',
  not_run: 'Vision не запускался',
  budget: 'Исчерпан бюджет AI',
  plan_done: 'AI завершил доработку',
  no_improvement: 'Нет улучшений',
  max_rounds: 'Достигнут лимит раундов',
};

export function importSourceLabel(source?: string | null): string {
  if (!source) return '—';
  return IMPORT_SOURCE_LABELS[source] || source;
}

export function importStopReasonLabel(reason?: string | null): string {
  if (!reason) return '—';
  return STOP_REASON_LABELS[reason] || reason;
}

export function formatImportScore(score?: number | null): string {
  if (score === undefined || score === null || Number.isNaN(score)) return '—';
  return `${Math.round(score * 1000) / 10}%`;
}

export function buildImportMetadataDescription(
  importSource?: string,
  refinement?: ImportRefinementState,
): string {
  const lines: string[] = [];
  lines.push(`Источник вёрстки: ${importSourceLabel(importSource)}.`);

  if (refinement?.available) {
    lines.push(`AI vision: ${importStopReasonLabel(refinement.stop_reason)}.`);
    if (refinement.best_score !== undefined) {
      lines.push(`Лучший score: ${formatImportScore(refinement.best_score)}.`);
    }
    if (refinement.rounds !== undefined && refinement.rounds > 0) {
      lines.push(`Раундов AI: ${refinement.rounds}.`);
    }
  } else if (refinement?.stop_reason) {
    lines.push(`AI vision: ${importStopReasonLabel(refinement.stop_reason)}.`);
  }

  const qa = refinement?.qa;
  if (qa?.winner && qa.winner !== importSource) {
    lines.push(
      `QA выбрал ${importSourceLabel(qa.winner)} (score ${formatImportScore(qa.winner_score)}).`,
    );
  }

  return lines.join(' ');
}
