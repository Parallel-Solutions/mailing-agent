import { api } from './client';
import type { OnboardingState, OnboardingUpdate } from './types';

export const onboardingApi = {
  get: () => api.get<OnboardingState>('/api/v1/onboarding'),
  update: (payload: OnboardingUpdate) =>
    api.patch<OnboardingState>('/api/v1/onboarding', payload),
  restart: () => api.post<OnboardingState>('/api/v1/onboarding/restart'),
};
