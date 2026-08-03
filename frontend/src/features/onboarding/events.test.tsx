import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import {
  getActiveOnboardingStep,
  setActiveOnboardingStep,
  useActiveOnboardingStep,
} from './events';

function ActiveStepProbe() {
  const activeStep = useActiveOnboardingStep();
  return <div data-testid="active-step">{activeStep ?? 'none'}</div>;
}

afterEach(() => act(() => setActiveOnboardingStep(null)));

describe('onboarding active step store', () => {
  it('delivers the current step to a page that mounts later', () => {
    setActiveOnboardingStep('campaign-launch');

    render(<ActiveStepProbe />);

    expect(screen.getByTestId('active-step')).toHaveTextContent('campaign-launch');
    expect(getActiveOnboardingStep()).toBe('campaign-launch');
  });

  it('updates mounted subscribers and clears the retained step', () => {
    render(<ActiveStepProbe />);

    act(() => setActiveOnboardingStep('template-source'));
    expect(screen.getByTestId('active-step')).toHaveTextContent('template-source');

    act(() => setActiveOnboardingStep(null));
    expect(screen.getByTestId('active-step')).toHaveTextContent('none');
  });
});
