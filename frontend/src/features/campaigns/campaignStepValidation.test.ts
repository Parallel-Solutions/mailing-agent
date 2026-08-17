import { describe, expect, it } from 'vitest';
import dayjs from 'dayjs';
import {
  buildCampaignStepValidation,
  CAMPAIGN_WIZARD_STEP_TITLES,
  hasAiFixableWizardIssues,
  validateScheduleStep,
} from './campaignStepValidation';
import { resolveScheduleFormValues, scheduleToFormValues } from '@/utils/scheduleForm';

describe('campaignStepValidation', () => {
  it('maps basics errors to step 0', () => {
    const steps = buildCampaignStepValidation({
      draft: {},
      validate: null,
    });
    expect(steps[0].status).toBe('error');
    expect(steps[0].errors).toEqual([
      'Укажите название рассылки',
      'Выберите цепочку писем',
    ]);
    expect(steps[1].status).toBe('ok');
  });

  it('maps sender and recipients errors', () => {
    const steps = buildCampaignStepValidation({
      draft: { name: 'A', email_chain_id: 'c1', company_id: 'co', company_work_type_id: 'wt' },
      validate: {
        ok: false,
        errors: ['Выберите подключение отправителя', 'Нет получателей для отправки'],
        warnings: [],
        active_recipients: 0,
        excluded_recipients: 0,
      },
    });
    expect(steps[1].errors).toContain('Выберите подключение отправителя');
    expect(steps[2].errors).toContain('Нет получателей для отправки');
  });

  it('maps template issues to launch step and marks aiFixable', () => {
    const steps = buildCampaignStepValidation({
      draft: { name: 'A', email_chain_id: 'c1', company_id: 'co', company_work_type_id: 'wt' },
      validate: {
        ok: false,
        errors: [],
        warnings: [],
        active_recipients: 1,
        excluded_recipients: 0,
        template_issues: [
          {
            template_name: 'Mail',
            kind: 'grammar',
            severity: 'warning',
            message: 'Ошибка',
            fragment: 'текст',
            suggestion: 'Текст',
            token: 'текст',
          },
        ],
      },
    });
    expect(steps[4].warnings.length).toBeGreaterThan(0);
    expect(steps[4].aiFixable).toBe(true);
    expect(hasAiFixableWizardIssues(steps)).toBe(true);
  });

  it('maps language template issues with error severity to warnings', () => {
    const steps = buildCampaignStepValidation({
      draft: { name: 'A', email_chain_id: 'c1', company_id: 'co', company_work_type_id: 'wt' },
      validate: {
        ok: false,
        errors: [],
        warnings: [],
        active_recipients: 1,
        excluded_recipients: 0,
        template_issues: [
          {
            template_name: 'Mail',
            kind: 'punctuation',
            severity: 'error',
            message: 'Пробел перед точкой',
            fragment: ' .',
            token: ' .',
          },
        ],
      },
    });
    expect(steps[4].errors).toHaveLength(0);
    expect(steps[4].warnings.length).toBeGreaterThan(0);
    expect(steps[4].status).toBe('warning');
  });

  it('keeps placeholder template issues as errors', () => {
    const steps = buildCampaignStepValidation({
      draft: { name: 'A', email_chain_id: 'c1', company_id: 'co', company_work_type_id: 'wt' },
      validate: {
        ok: false,
        errors: [],
        warnings: [],
        active_recipients: 1,
        excluded_recipients: 0,
        template_issues: [
          {
            template_name: 'Mail',
            kind: 'artifact',
            severity: 'error',
            message: 'Артефакт',
            fragment: '{{ стп }}',
            token: '{{ стп }}',
          },
        ],
      },
    });
    expect(steps[4].errors.length).toBeGreaterThan(0);
    expect(steps[4].status).toBe('error');
  });

  it('deduplicates template issues already present in server errors', () => {
    const duplicate = 'Шаблон «Mail»: Артефакт';
    const steps = buildCampaignStepValidation({
      draft: { name: 'A', email_chain_id: 'c1', company_id: 'co', company_work_type_id: 'wt' },
      validate: {
        ok: false,
        errors: [duplicate],
        warnings: [],
        active_recipients: 1,
        excluded_recipients: 0,
        template_issues: [
          {
            template_name: 'Mail',
            kind: 'artifact',
            severity: 'error',
            message: 'Артефакт',
            fragment: '{{ стп }}',
            token: '{{ стп }}',
          },
        ],
      },
    });
    expect(steps[4].errors.filter((item) => item === duplicate)).toHaveLength(1);
    expect(steps[4].templateIssues).toHaveLength(1);
  });

  it('validates schedule fields', () => {
    expect(validateScheduleStep({})).toEqual(
      expect.arrayContaining(['Укажите дату и время старта', 'Укажите интервал между пакетами']),
    );
  });

  it('does not mark the schedule step as error on a brand-new draft whose schedule form is unmounted', () => {
    // Form.useWatch([], scheduleForm) yields {} until the Schedule step's
    // Collapse panel mounts (e.g. a fresh draft opening on step 0).
    const steps = buildCampaignStepValidation({
      draft: { name: 'Черновик рассылки' },
      validate: undefined,
      scheduleValues: resolveScheduleFormValues({}, scheduleToFormValues(undefined)),
    });
    expect(steps[3].errors).toEqual([]);
    expect(steps[3].status).not.toBe('error');
  });

  it('still reports schedule errors when the mounted form has a cleared start date', () => {
    const steps = buildCampaignStepValidation({
      draft: { name: 'A', email_chain_id: 'c1' },
      validate: undefined,
      scheduleValues: resolveScheduleFormValues(
        {
          batch_size: 25,
          start_at: null,
          interval_value: 1,
          interval_unit: 'hours',
        } as unknown as Partial<import('@/utils/scheduleForm').ScheduleFormValues>,
        scheduleToFormValues(undefined),
      ),
    });
    expect(steps[3].errors).toContain('Укажите дату и время старта');
    expect(steps[3].status).toBe('error');
  });

  it('accepts batch size 1 and rejects zero', () => {
    const valid = validateScheduleStep({
      batch_size: 1,
      start_at: dayjs().add(1, 'day'),
      interval_value: 1,
      interval_unit: 'hours',
    });
    expect(valid).not.toContain('Размер пакета должен быть целым числом больше нуля');

    expect(
      validateScheduleStep({
        batch_size: 0,
        start_at: dayjs().add(1, 'day'),
        interval_value: 1,
        interval_unit: 'hours',
      }),
    ).toContain('Размер пакета должен быть целым числом больше нуля');
  });

  it('exposes step titles', () => {
    expect(CAMPAIGN_WIZARD_STEP_TITLES).toHaveLength(5);
  });
});
