import type { Campaign, CampaignValidateResponse, TemplateValidationIssue } from '@/api/types';
import { validateCampaignBasics } from '@/utils/validators';
import { formValuesToSchedulePayload, type ScheduleFormValues } from '@/utils/scheduleForm';

export type StepValidationStatus = 'ok' | 'warning' | 'error';

export type StepValidationState = {
  status: StepValidationStatus;
  errors: string[];
  warnings: string[];
  aiFixable: boolean;
  templateIssues: TemplateValidationIssue[];
};

export const CAMPAIGN_WIZARD_STEP_TITLES = [
  'Основное',
  'Отправитель',
  'Получатели',
  'Расписание',
  'Запуск',
] as const;

export type CampaignWizardStepIndex = 0 | 1 | 2 | 3 | 4;

export const PLACEHOLDER_ISSUE_KINDS = new Set(['artifact', 'malformed', 'unresolved']);
const LANGUAGE_ISSUE_KINDS = new Set(['punctuation', 'grammar', 'case']);

export function isPlaceholderIssue(issue: Pick<TemplateValidationIssue, 'kind'>): boolean {
  return PLACEHOLDER_ISSUE_KINDS.has(issue.kind);
}

export function isBlockingPlaceholderIssue(
  issue: Pick<TemplateValidationIssue, 'kind' | 'blocking'>,
): boolean {
  if (!isPlaceholderIssue(issue)) {
    return false;
  }
  return issue.blocking !== false;
}

export function isLanguageIssue(issue: Pick<TemplateValidationIssue, 'kind'>): boolean {
  return LANGUAGE_ISSUE_KINDS.has(issue.kind);
}

function emptyStep(): StepValidationState {
  return { status: 'ok', errors: [], warnings: [], aiFixable: false, templateIssues: [] };
}

function finalizeStep(state: StepValidationState): StepValidationState {
  const status: StepValidationStatus =
    state.errors.length > 0 ? 'error' : state.warnings.length > 0 ? 'warning' : 'ok';
  return { ...state, status };
}

function isBasicsError(message: string): boolean {
  const lower = message.toLowerCase();
  if (
    lower.includes('название') ||
    lower.includes('компан') ||
    lower.includes('вид работ') ||
    lower.includes('цепочк')
  ) {
    return true;
  }
  return (
    lower.includes('блок') ||
    lower.includes('root_node') ||
    lower.includes('начальн') ||
    (lower.includes('цепоч') && !lower.includes('шаблон «'))
  );
}

function isSenderError(message: string): boolean {
  const lower = message.toLowerCase();
  return (
    lower.includes('подключен') ||
    lower.includes('отправител') ||
    lower.includes('smtp') ||
    lower.includes('способ отправки')
  );
}

function isRecipientsError(message: string): boolean {
  const lower = message.toLowerCase();
  return (
    lower.includes('получател') ||
    lower.includes('сопоставлен') ||
    lower.includes('переменн') ||
    lower.includes('mapping')
  );
}

export function isMappingRelatedMessage(message: string): boolean {
  const lower = message.toLowerCase();
  return lower.includes('сопостав') || lower.includes('переменн') || lower.includes('mapping');
}

function isLaunchTemplateMessage(message: string): boolean {
  return message.includes('Шаблон «') || message.toLowerCase().includes('шаблон');
}

function classifyServerError(message: string): CampaignWizardStepIndex {
  if (isBasicsError(message)) return 0;
  if (isSenderError(message)) return 1;
  if (isRecipientsError(message)) return 2;
  if (isLaunchTemplateMessage(message)) return 4;
  return 4;
}

function classifyServerWarning(message: string): CampaignWizardStepIndex {
  if (isBasicsError(message)) return 0;
  if (isSenderError(message)) return 1;
  if (isRecipientsError(message)) return 2;
  if (message.toLowerCase().includes('расписан') || message.toLowerCase().includes('шаблон письма не выбран')) {
    return message.toLowerCase().includes('расписан') ? 3 : 0;
  }
  return 4;
}

function issueStep(_issue: TemplateValidationIssue): CampaignWizardStepIndex {
  return 4;
}

function isTemplateIssueError(issue: TemplateValidationIssue): boolean {
  if (isLanguageIssue(issue)) {
    return false;
  }
  if (isPlaceholderIssue(issue) && !isBlockingPlaceholderIssue(issue)) {
    return false;
  }
  return issue.severity === 'error' || (!issue.severity && isPlaceholderIssue(issue));
}

function isAiFixableIssue(issue: TemplateValidationIssue): boolean {
  if (issue.suggestion?.trim()) {
    return ['punctuation', 'grammar', 'case'].includes(issue.kind);
  }
  return ['grammar', 'punctuation', 'case'].includes(issue.kind);
}

export function validateScheduleStep(values: Partial<ScheduleFormValues>): string[] {
  const errors: string[] = [];
  const payload = formValuesToSchedulePayload(values);
  if (!payload) {
    errors.push('Укажите дату и время старта');
  }
  if (!values.interval_value || Number(values.interval_value) < 1) {
    errors.push('Укажите интервал между пакетами');
  }
  if (!values.interval_unit) {
    errors.push('Выберите единицу интервала');
  }
  return errors;
}

export type BuildStepValidationInput = {
  draft: Partial<Campaign>;
  validate?: CampaignValidateResponse | null;
  scheduleValues?: Partial<ScheduleFormValues>;
};

export function buildCampaignStepValidation(input: BuildStepValidationInput): StepValidationState[] {
  const steps: StepValidationState[] = [emptyStep(), emptyStep(), emptyStep(), emptyStep(), emptyStep()];
  const { draft, validate, scheduleValues } = input;

  for (const message of validateCampaignBasics(draft)) {
    steps[0].errors.push(message);
  }

  for (const message of validate?.errors || []) {
    steps[classifyServerError(message)].errors.push(message);
  }

  for (const message of validate?.warnings || []) {
    steps[classifyServerWarning(message)].warnings.push(message);
  }

  const seenTemplateMessages = new Set([
    ...(validate?.errors || []),
    ...(validate?.warnings || []),
  ]);

  if (scheduleValues) {
    for (const message of validateScheduleStep(scheduleValues)) {
      steps[3].errors.push(message);
    }
  }

  for (const issue of validate?.template_issues || []) {
    const stepIndex = issueStep(issue);
    const label = issue.template_name ? `Шаблон «${issue.template_name}»` : 'Шаблон';
    const text = issue.message ? `${label}: ${issue.message}` : label;
    if (seenTemplateMessages.has(text)) {
      steps[stepIndex].templateIssues.push(issue);
      if (isAiFixableIssue(issue)) {
        steps[stepIndex].aiFixable = true;
      }
      continue;
    }
    if (isTemplateIssueError(issue)) {
      steps[stepIndex].errors.push(text);
    } else {
      steps[stepIndex].warnings.push(text);
    }
    steps[stepIndex].templateIssues.push(issue);
    if (isAiFixableIssue(issue)) {
      steps[stepIndex].aiFixable = true;
    }
  }

  const mappingErrors = (validate?.errors || []).filter((item) => isRecipientsError(item));
  if (mappingErrors.length > 0) {
    steps[2].aiFixable = true;
  }

  const hasTemplateTextIssues = (validate?.template_issues || []).some((issue) => isAiFixableIssue(issue));
  if (hasTemplateTextIssues) {
    steps[4].aiFixable = true;
  }

  return steps.map(finalizeStep);
}

export function hasWizardValidationProblems(steps: StepValidationState[]): boolean {
  return steps.some((item) => item.status !== 'ok');
}

export function hasAiFixableWizardIssues(steps: StepValidationState[]): boolean {
  return steps.some((item) => item.aiFixable);
}
