import { api } from './client';
import type { OnboardingState, OnboardingUpdate } from './types';

const ANONYMOUS_ONBOARDING_OWNER = 'anonymous';

export function onboardingQueryKey(username?: string | null) {
  return ['onboarding', username || ANONYMOUS_ONBOARDING_OWNER] as const;
}

export function onboardingChapterStorageKey(username?: string | null) {
  return `campaignflow:onboarding-chapter:${encodeURIComponent(
    username || ANONYMOUS_ONBOARDING_OWNER,
  )}`;
}

export const onboardingApi = {
  get: () => api.get<OnboardingState>('/api/v1/onboarding'),
  update: (payload: OnboardingUpdate) =>
    api.patch<OnboardingState>('/api/v1/onboarding', payload),
  restart: () => api.post<OnboardingState>('/api/v1/onboarding/restart'),
};
