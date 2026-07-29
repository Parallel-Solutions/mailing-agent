import { describe, expect, it } from 'vitest';
import {
  buildCampaignStepValidation,
  CAMPAIGN_WIZARD_STEP_TITLES,
  hasAiFixableWizardIssues,
  validateScheduleStep,
} from './campaignStepValidation';

describe('campaignStepValidation', () => {
  it('maps basics errors to step 0', () => {
    const steps = buildCampaignStepValidation({
      draft: {},
      validate: null,
    });
    expect(steps[0].status).toBe('error');
    expect(steps[0].errors).toEqual(
      expect.arrayContaining(['Укажите название рассылки', 'Выберите цепочку писем']),
    );
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

  it('exposes step titles', () => {
    expect(CAMPAIGN_WIZARD_STEP_TITLES).toHaveLength(5);
  });
});
