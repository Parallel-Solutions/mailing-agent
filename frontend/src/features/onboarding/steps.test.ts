import { describe, expect, it } from 'vitest';
import {
  ONBOARDING_CHAPTERS,
  ONBOARDING_STEPS,
  ONBOARDING_VERSION,
  getOnboardingChapterSteps,
} from './steps';

const ORIGINAL_STEP_IDS = [
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
  'campaign-basics',
  'campaign-sender',
  'campaign-recipients',
  'campaign-schedule',
  'campaign-launch',
  'statistics-overview',
  'campaigns-overview',
  'chains-overview',
  'finish',
];

describe('onboarding steps', () => {
  it('keeps the version and original persisted indexes stable', () => {
    expect(ONBOARDING_VERSION).toBe(8);
    expect(ONBOARDING_STEPS.slice(0, ORIGINAL_STEP_IDS.length).map((step) => step.id))
      .toEqual(ORIGINAL_STEP_IDS);
  });

  it('keeps stable unique identifiers and valid routes', () => {
    const ids = ONBOARDING_STEPS.map((step) => step.id);

    expect(new Set(ids).size).toBe(ids.length);
    expect(ONBOARDING_STEPS).toHaveLength(85);
    expect(ONBOARDING_STEPS.every((step) => step.route.startsWith('/'))).toBe(true);
  });

  it('defines one general tutorial and page-local tutorials', () => {
    expect(ONBOARDING_CHAPTERS.map((chapter) => chapter.id)).toEqual([
      'general',
      'companies',
      'connections',
      'templates',
      'chains',
      'campaign',
      'analytics',
    ]);
    expect(getOnboardingChapterSteps('general')).toHaveLength(12);
    expect(getOnboardingChapterSteps('companies')).toHaveLength(3);
    expect(getOnboardingChapterSteps('connections')).toHaveLength(9);
    expect(getOnboardingChapterSteps('templates')).toHaveLength(17);
    expect(getOnboardingChapterSteps('chains')).toHaveLength(11);
    expect(getOnboardingChapterSteps('campaign')).toHaveLength(21);
    expect(getOnboardingChapterSteps('analytics')).toHaveLength(13);
  });

  it('references every chapter step exactly once inside that chapter', () => {
    const knownIds = new Set(ONBOARDING_STEPS.map((step) => step.id));

    for (const chapter of ONBOARDING_CHAPTERS) {
      expect(new Set(chapter.stepIds).size).toBe(chapter.stepIds.length);
      expect(chapter.stepIds.every((stepId) => knownIds.has(stepId))).toBe(true);
    }
  });

  it('keeps every local tutorial on its entry page', () => {
    for (const chapter of ONBOARDING_CHAPTERS.filter(({ scope }) => scope === 'local')) {
      const entryPath = chapter.entryRoute.split('?')[0];
      const stepPaths = getOnboardingChapterSteps(chapter.id)
        .map((step) => step.route.split('?')[0]);

      expect(new Set(stepPaths)).toEqual(new Set([entryPath]));
    }
  });

  it('covers companies and connection settings without profile training', () => {
    const companyIds = getOnboardingChapterSteps('companies').map((step) => step.id);
    const connectionIds = getOnboardingChapterSteps('connections').map((step) => step.id);
    const chapterIds = ONBOARDING_CHAPTERS.flatMap((chapter) => chapter.stepIds);

    expect(companyIds).toEqual([
      'company-overview',
      'company-details',
      'company-work-types',
    ]);
    expect(connectionIds).toEqual(expect.arrayContaining([
      'connection-method',
      'connection-limits',
      'connection-delivery-guard',
    ]));
    expect(chapterIds).not.toContain('profile-defaults');
    expect(chapterIds).not.toContain('profile-notifications');
  });

  it('covers content, campaign execution and analytics', () => {
    const templateIds = getOnboardingChapterSteps('templates').map((step) => step.id);
    const chainIds = getOnboardingChapterSteps('chains').map((step) => step.id);
    const campaignIds = getOnboardingChapterSteps('campaign').map((step) => step.id);
    const analyticsIds = getOnboardingChapterSteps('analytics').map((step) => step.id);

    expect(templateIds).toEqual(expect.arrayContaining([
      'template-format',
      'template-document',
      'document-upload',
      'document-fields',
      'document-preview',
      'document-chain-use',
    ]));
    expect(chainIds).toEqual(expect.arrayContaining([
      'chain-builder',
      'chain-add-nodes',
      'chain-email-template',
      'chain-documents',
      'chain-link-purpose',
      'chain-publish-button',
    ]));
    expect(campaignIds).toEqual(expect.arrayContaining([
      'campaign-basics',
      'campaign-name',
      'campaign-chain',
      'campaign-company',
      'campaign-sender',
      'campaign-sender-connection',
      'campaign-recipients',
      'campaign-recipient-sources',
      'campaign-schedule',
      'campaign-batch-size',
      'campaign-interval',
      'campaign-launch',
      'campaign-test-email',
      'campaign-start',
    ]));
    expect(campaignIds).not.toContain('campaign-finish');
    expect(analyticsIds).toEqual(expect.arrayContaining([
      'campaigns-overview',
      'analytics-summary',
      'analytics-campaign',
      'analytics-campaign-status',
      'analytics-rates',
      'analytics-consents',
    ]));
  });

  it('keeps every explanation short and focused', () => {
    expect(ONBOARDING_STEPS.every((step) => step.description.length <= 220)).toBe(true);
    expect(ONBOARDING_STEPS.every((step) => !step.description.includes('\n'))).toBe(true);
    expect(ONBOARDING_STEPS.every((step) => (step.details || []).length <= 4)).toBe(true);
    expect(ONBOARDING_STEPS.every((step) => (step.details || []).every((detail) => detail.length <= 140)))
      .toBe(true);
  });

  it('contains only passive explanatory steps with positioned targets', () => {
    expect(ONBOARDING_STEPS.every((step) => !('requiresAction' in step))).toBe(true);
    expect(ONBOARDING_STEPS.every((step) => !('skipIfTargetMissing' in step))).toBe(true);
    expect(ONBOARDING_STEPS.every((step) => !step.description.includes('Нажмите'))).toBe(true);
    expect(ONBOARDING_STEPS.filter((step) => step.target).every((step) => step.placement)).toBe(true);
    expect(ONBOARDING_STEPS.find((step) => step.id === 'connection-submit')?.description)
      .toContain('запрос не выполняется');
  });
});
