export const ONBOARDING_ADVANCE_EVENT = 'campaignflow:onboarding-advance';
export const ONBOARDING_ENTER_EVENT = 'campaignflow:onboarding-enter';

export type OnboardingAdvanceDetail = {
  fromId: string;
  toId?: string;
};

export type OnboardingEnterDetail = {
  stepId: string;
};

export function advanceOnboarding(fromId: string, toId?: string) {
  window.dispatchEvent(
    new CustomEvent<OnboardingAdvanceDetail>(ONBOARDING_ADVANCE_EVENT, {
      detail: { fromId, toId },
    }),
  );
}
