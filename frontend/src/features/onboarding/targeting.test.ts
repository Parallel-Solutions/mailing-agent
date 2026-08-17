import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  findVisibleOnboardingTarget,
  isOnboardingRouteActive,
} from './targeting';

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

  it('still highlights a control that passive onboarding intentionally disables', () => {
    const disabledSubmit = markRendered(document.createElement('button'));
    disabledSubmit.dataset.onboardingId = 'connection-submit';
    disabledSubmit.setAttribute('disabled', '');
    document.body.append(disabledSubmit);

    expect(findVisibleOnboardingTarget('[data-onboarding-id="connection-submit"]'))
      .toBe(disabledSubmit);
  });

});

describe('onboarding route matching', () => {
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
