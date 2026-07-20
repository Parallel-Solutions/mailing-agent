import { describe, expect, it } from 'vitest';
import { ONBOARDING_STEPS } from './steps';

describe('onboarding steps', () => {
  it('keeps stable unique identifiers and valid routes', () => {
    const ids = ONBOARDING_STEPS.map((step) => step.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(ONBOARDING_STEPS).toHaveLength(27);
    expect(ONBOARDING_STEPS.every((step) => step.route.startsWith('/'))).toBe(true);
  });

  it('covers the required first-run workflow', () => {
    expect(ONBOARDING_STEPS.map((step) => step.id)).toEqual([
      'welcome',
      'profile-sender',
      'profile-email',
      'profile-signature',
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

  it('lets the tour continue after choosing an email format', () => {
    const formatStep = ONBOARDING_STEPS.find((step) => step.id === 'template-format');

    expect(formatStep?.requiresAction).toBeUndefined();
    expect(formatStep?.target).toContain('.ant-modal-content:has(');
  });
});
