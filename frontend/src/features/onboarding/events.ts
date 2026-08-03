import { useSyncExternalStore } from 'react';

type Listener = () => void;

let activeOnboardingStep: string | null = null;
const listeners = new Set<Listener>();

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getActiveOnboardingStep() {
  return activeOnboardingStep;
}

export function setActiveOnboardingStep(stepId: string | null) {
  if (activeOnboardingStep === stepId) return;
  activeOnboardingStep = stepId;
  listeners.forEach((listener) => listener());
}

export function useActiveOnboardingStep() {
  return useSyncExternalStore(
    subscribe,
    getActiveOnboardingStep,
    () => null,
  );
}
