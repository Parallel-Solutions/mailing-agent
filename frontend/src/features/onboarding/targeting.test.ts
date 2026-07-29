import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  findVisibleOnboardingSuccessor,
  findVisibleOnboardingTarget,
  isOnboardingRouteActive,
  resolveAvailableOnboardingStep,
} from './targeting';
import type { OnboardingStepDefinition } from './steps';

function renderedRect() {
  return {
    width: 120,
    height: 40,
    top: 10,
    right: 130,
    bottom: 50,
    left: 10,
    x: 10,
    y: 10,
    toJSON: () => ({}),
  } as DOMRect;
}

function markRendered(element: HTMLElement) {
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue(renderedRect());
  return element;
}

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

describe('onboarding target selection', () => {
  it('ignores a page target covered by an open modal', () => {
    const pageButton = markRendered(document.createElement('button'));
    pageButton.dataset.onboardingId = 'open';
    const modal = markRendered(document.createElement('div'));
    modal.className = 'ant-modal-wrap';
    const modalButton = markRendered(document.createElement('button'));
    modalButton.dataset.onboardingId = 'method';
    modal.append(modalButton);
    document.body.append(pageButton, modal);

    expect(findVisibleOnboardingTarget('[data-onboarding-id="open"]')).toBeUndefined();
    expect(findVisibleOnboardingTarget('[data-onboarding-id="method"]')).toBe(modalButton);
  });

  it('ignores targets that are not rendered', () => {
    const hiddenButton = document.createElement('button');
    hiddenButton.dataset.onboardingId = 'hidden';
    document.body.append(hiddenButton);

    expect(findVisibleOnboardingTarget('[data-onboarding-id="hidden"]')).toBeUndefined();
  });

  it('skips an unavailable optional branch in the requested direction', () => {
    const steps: OnboardingStepDefinition[] = [
      { id: 'first', route: '/', title: '', description: '' },
      {
        id: 'optional',
        route: '/',
        title: '',
        description: '',
        target: '[data-onboarding-id="missing"]',
        skipIfTargetMissing: true,
      },
      { id: 'last', route: '/', title: '', description: '' },
    ];

    expect(resolveAvailableOnboardingStep(steps, 1, 0)).toBe(2);
  });

  it('recovers to the next visible target after a modal replaces the page target', () => {
    const pageButton = markRendered(document.createElement('button'));
    pageButton.dataset.onboardingId = 'open';
    const modal = markRendered(document.createElement('div'));
    modal.className = 'ant-modal-wrap';
    const modalTarget = markRendered(document.createElement('div'));
    modalTarget.dataset.onboardingId = 'method';
    modal.append(modalTarget);
    document.body.append(pageButton, modal);

    const steps: OnboardingStepDefinition[] = [
      {
        id: 'open',
        route: '/connections',
        title: '',
        description: '',
        target: '[data-onboarding-id="open"]',
      },
      {
        id: 'method',
        route: '/connections',
        title: '',
        description: '',
        target: '[data-onboarding-id="method"]',
      },
    ];

    expect(findVisibleOnboardingSuccessor(steps, 0)).toBe(1);
  });

  it('does not jump over a required missing target', () => {
    const visibleTarget = markRendered(document.createElement('div'));
    visibleTarget.dataset.onboardingId = 'later';
    document.body.append(visibleTarget);

    const steps: OnboardingStepDefinition[] = [
      { id: 'current', route: '/', title: '', description: '' },
      {
        id: 'required',
        route: '/',
        title: '',
        description: '',
        target: '[data-onboarding-id="missing"]',
      },
      {
        id: 'later',
        route: '/',
        title: '',
        description: '',
        target: '[data-onboarding-id="later"]',
      },
    ];

    expect(findVisibleOnboardingSuccessor(steps, 0)).toBeUndefined();
  });
});

describe('onboarding route matching', () => {
  it('accepts required query params together with dynamic drawer params', () => {
    expect(isOnboardingRouteActive('/?tab=audiences', {
      pathname: '/',
      search: '?tab=audiences&audience=audience-id',
    })).toBe(true);
  });

  it('does not mistake another statistics tab for the requested route', () => {
    expect(isOnboardingRouteActive('/?tab=campaign-list', {
      pathname: '/',
      search: '?tab=audiences',
    })).toBe(false);
  });

  it('requires a clean root route for the statistics overview', () => {
    expect(isOnboardingRouteActive('/', { pathname: '/', search: '?tab=audiences' })).toBe(false);
  });

  it('keeps feature-page steps active while their modal query params change', () => {
    expect(isOnboardingRouteActive('/connections', {
      pathname: '/connections',
      search: '?add=1&smtp_stage=email',
    })).toBe(true);
  });
});
