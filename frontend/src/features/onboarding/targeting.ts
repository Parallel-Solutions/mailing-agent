import type { OnboardingStepDefinition } from './steps';

const OVERLAY_LAYER_SELECTOR = '.ant-modal-wrap, .ant-drawer-content-wrapper';

function isRendered(element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);

  return (
    rect.width > 0
    && rect.height > 0
    && style.display !== 'none'
    && style.visibility !== 'hidden'
  );
}

function activeOverlayLayers() {
  return Array.from(document.querySelectorAll<HTMLElement>(OVERLAY_LAYER_SELECTOR))
    .filter(isRendered);
}

export function findVisibleOnboardingTarget(selector?: string) {
  if (!selector) return undefined;

  const overlayLayers = activeOverlayLayers();
  return Array.from(document.querySelectorAll<HTMLElement>(selector)).find((element) => {
    if (!isRendered(element)) return false;
    return overlayLayers.length === 0 || overlayLayers.some((layer) => layer.contains(element));
  });
}

export function resolveAvailableOnboardingStep(
  steps: OnboardingStepDefinition[],
  index: number,
  current: number,
) {
  const direction = index >= current ? 1 : -1;
  let candidate = index;

  while (candidate >= 0 && candidate < steps.length) {
    const step = steps[candidate];
    if (!step.skipIfTargetMissing || !step.target || findVisibleOnboardingTarget(step.target)) {
      return candidate;
    }
    candidate += direction;
  }

  return Math.max(0, Math.min(index, steps.length - 1));
}

export function findVisibleOnboardingSuccessor(
  steps: OnboardingStepDefinition[],
  current: number,
) {
  let candidate = current + 1;

  while (candidate < steps.length) {
    const step = steps[candidate];
    if (!step.target) return undefined;
    if (findVisibleOnboardingTarget(step.target)) return candidate;
    if (!step.skipIfTargetMissing) return undefined;
    candidate += 1;
  }

  return undefined;
}

export function isOnboardingRouteActive(
  route: string,
  location: { pathname: string; search: string },
) {
  const [expectedPathname, expectedSearch = ''] = route.split('?');
  if (location.pathname !== expectedPathname) return false;
  if (!expectedSearch) {
    // Root query params switch whole dashboard sections. On feature pages, query
    // params only describe an open modal, drawer, or wizard step and must not
    // make the onboarding target stale.
    return expectedPathname === '/' ? location.search === '' : true;
  }

  const expectedParams = new URLSearchParams(expectedSearch);
  const currentParams = new URLSearchParams(location.search);

  for (const [key, value] of expectedParams) {
    if (currentParams.get(key) !== value) {
      return false;
    }
  }

  return true;
}
