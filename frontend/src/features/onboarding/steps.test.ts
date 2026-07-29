import { describe, expect, it } from 'vitest';
import { ONBOARDING_STEPS } from './steps';

describe('onboarding steps', () => {
  it('keeps stable unique identifiers and valid routes', () => {
    const ids = ONBOARDING_STEPS.map((step) => step.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(ONBOARDING_STEPS).toHaveLength(24);
    expect(ONBOARDING_STEPS.every((step) => step.route.startsWith('/'))).toBe(true);
  });

  it('covers the required first-run workflow', () => {
    expect(ONBOARDING_STEPS.map((step) => step.id)).toEqual([
      'welcome',
      'connection-open',
      'connection-method',
      'connection-details',
      'connection-auth',
      'connection-api-provider',
      'connection-credentials',
      'connection-submit',
      'connection-limits',
      'template-open',
      'template-format',
      'template-source',
      'template-custom',
      'audience-open',
      'audience-import',
      'campaign-basics',
      'campaign-sender',
      'campaign-recipients',
      'campaign-schedule',
      'campaign-launch',
      'statistics-overview',
      'campaigns-overview',
      'chains-overview',
      'finish',
    ]);
  });

  it('does not include profile fields in onboarding', () => {
    expect(ONBOARDING_STEPS.some((step) => step.route === '/profile')).toBe(false);
  });

  it('explains an automatically selected campaign sender and names the next section', () => {
    const senderStep = ONBOARDING_STEPS.find((step) => step.id === 'campaign-sender');

    expect(senderStep).toMatchObject({
      title: 'Проверьте отправителя',
      nextLabel: 'К получателям',
    });
    expect(senderStep?.description).toContain('уже может быть выбран');
    expect(senderStep?.description).toContain('раскройте список');
  });

  it('keeps every explanation short and focused', () => {
    expect(ONBOARDING_STEPS.every((step) => step.description.length <= 140)).toBe(true);
    expect(ONBOARDING_STEPS.every((step) => !step.description.includes('\n'))).toBe(true);
  });

  it('lets the tour continue after choosing an email format', () => {
    const formatStep = ONBOARDING_STEPS.find((step) => step.id === 'template-format');

    expect(formatStep?.requiresAction).toBeUndefined();
    expect(formatStep?.target).toContain('.ant-modal-content:has(');
  });
});
