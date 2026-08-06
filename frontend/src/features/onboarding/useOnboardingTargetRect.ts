import { useEffect, useRef, useState, type RefObject } from 'react';
import { findVisibleOnboardingTarget } from './targeting';

export type OnboardingTargetSource =
  | string
  | RefObject<HTMLElement | null>
  | null
  | undefined;

export type OnboardingTargetGeometry = {
  epoch: number;
  target: HTMLElement | null;
  rect: DOMRect | null;
  stable: boolean;
  revision: number;
};

type Options = {
  enabled: boolean;
  epoch: number;
};

const STEP_ROOT_SELECTOR = [
  '.ant-modal-wrap',
  '.ant-drawer-content-wrapper',
  '.ant-pro-page-container',
  'main',
].join(', ');
const LAYOUT_BOUNDARY_SELECTOR = [
  '.ant-modal-wrap',
  '.ant-drawer-content-wrapper',
  '.ant-pro-page-container',
  'main',
].join(', ');
const RECT_EPSILON = 0.5;

function isScrollable(element: Element) {
  const style = window.getComputedStyle(element);
  return [style.overflow, style.overflowX, style.overflowY]
    .some((value) => /^(auto|scroll|overlay)$/.test(value));
}

function collectScrollableParents(element: HTMLElement) {
  const parents: HTMLElement[] = [];
  let parent = element.parentElement;
  while (parent) {
    if (isScrollable(parent)) parents.push(parent);
    parent = parent.parentElement;
  }
  return parents;
}

function collectLayoutElements(element: HTMLElement) {
  const elements: HTMLElement[] = [element];
  let parent = element.parentElement;
  while (parent) {
    elements.push(parent);
    if (parent.matches(LAYOUT_BOUNDARY_SELECTOR) || isScrollable(parent)) break;
    parent = parent.parentElement;
  }
  return elements;
}

function stepRootFor(element: HTMLElement | null) {
  return element?.closest<HTMLElement>(STEP_ROOT_SELECTOR) ?? document.body;
}

function resolveTarget(source: OnboardingTargetSource) {
  if (typeof source === 'string') return findVisibleOnboardingTarget(source) ?? null;
  return source?.current ?? null;
}

function cloneRect(rect: DOMRect) {
  return new DOMRect(rect.left, rect.top, rect.width, rect.height);
}

function rectEquals(left: DOMRect | null, right: DOMRect | null) {
  if (!left || !right) return left === right;
  return (
    Math.abs(left.left - right.left) <= RECT_EPSILON
    && Math.abs(left.top - right.top) <= RECT_EPSILON
    && Math.abs(left.width - right.width) <= RECT_EPSILON
    && Math.abs(left.height - right.height) <= RECT_EPSILON
  );
}
function sameElements(left: Element[], right: Element[]) {
  return left.length === right.length
    && left.every((element, index) => element === right[index]);
}


function renderedRect(element: HTMLElement) {
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);
  if (
    rect.width <= 0
    || rect.height <= 0
    || style.display === 'none'
    || style.visibility === 'hidden'
  ) {
    return null;
  }
  return cloneRect(rect);
}

export function useOnboardingTargetRect(
  source: OnboardingTargetSource,
  { enabled, epoch }: Options,
): OnboardingTargetGeometry {
  const revisionRef = useRef(0);
  const activeEpochRef = useRef(epoch);
  activeEpochRef.current = epoch;
  const [geometry, setGeometry] = useState<OnboardingTargetGeometry>({
    epoch,
    target: null,
    rect: null,
    stable: false,
    revision: 0,
  });

  useEffect(() => {
    let active = true;
    let frame = 0;
    let observedTarget: HTMLElement | null | undefined;
    let observedRoot: HTMLElement | null = null;
    let observedLayoutElements: HTMLElement[] = [];
    let observedScrollParents: HTMLElement[] = [];
    let candidateRect: DOMRect | null = null;
    let candidateTarget: HTMLElement | null = null;
    let stableFrames = 0;
    let resizeObserver: ResizeObserver | null = null;
    let mutationObserver: MutationObserver | null = null;
    let removeScrollListeners = () => {};
    const generation = epoch;

    const isCurrent = () => active && activeEpochRef.current === generation;

    const publishReset = () => {
      setGeometry((current) => {
        if (
          current.epoch === generation
          && current.target === null
          && current.rect === null
          && !current.stable
        ) {
          return current;
        }
        return {
          epoch: generation,
          target: null,
          rect: null,
          stable: false,
          revision: current.revision,
        };
      });
    };

    const scheduleMeasure = () => {
      if (!isCurrent() || frame) return;
      frame = window.requestAnimationFrame(measure);
    };

    const bindTarget = (target: HTMLElement | null) => {
      const mutationRoot = stepRootFor(target);
      const layoutElements = target ? collectLayoutElements(target) : [];
      const scrollParents = target ? collectScrollableParents(target) : [];
      if (
        observedTarget === target
        && observedRoot === mutationRoot
        && sameElements(observedLayoutElements, layoutElements)
        && sameElements(observedScrollParents, scrollParents)
      ) return;

      observedTarget = target;
      observedRoot = mutationRoot;
      observedLayoutElements = layoutElements;
      observedScrollParents = scrollParents;
      resizeObserver?.disconnect();
      resizeObserver = null;
      mutationObserver?.disconnect();
      mutationObserver = null;
      removeScrollListeners();
      removeScrollListeners = () => {};

      mutationObserver = new MutationObserver(() => {
        if (isCurrent()) scheduleMeasure();
      });
      mutationObserver.observe(mutationRoot, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
      });

      if (!target) return;

      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => {
          if (isCurrent()) scheduleMeasure();
        });
        layoutElements.forEach((element) => resizeObserver?.observe(element));
      }

      const onScroll = () => scheduleMeasure();
      scrollParents.forEach((parent) => parent.addEventListener('scroll', onScroll, { passive: true }));
      removeScrollListeners = () => {
        scrollParents.forEach((parent) => parent.removeEventListener('scroll', onScroll));
      };
    };

    function measure() {
      frame = 0;
      if (!isCurrent()) return;

      if (!source) {
        bindTarget(null);
        if (candidateTarget === null && candidateRect === null) stableFrames += 1;
        else {
          candidateTarget = null;
          candidateRect = null;
          stableFrames = 1;
        }
        if (stableFrames >= 2) {
          setGeometry((current) => current.epoch === generation && current.stable
            ? current
            : {
                epoch: generation,
                target: null,
                rect: null,
                stable: true,
                revision: current.revision,
              });
        } else {
          scheduleMeasure();
        }
        return;
      }

      const target = resolveTarget(source);
      bindTarget(target);
      const nextRect = target ? renderedRect(target) : null;
      if (!target || !nextRect) {
        candidateTarget = null;
        candidateRect = null;
        stableFrames = 0;
        publishReset();
        return;
      }

      if (candidateTarget === target && rectEquals(candidateRect, nextRect)) {
        stableFrames += 1;
      } else {
        candidateTarget = target;
        candidateRect = nextRect;
        stableFrames = 1;
      }

      if (stableFrames < 2) {
        scheduleMeasure();
        return;
      }

      setGeometry((current) => {
        if (
          current.epoch === generation
          && current.target === target
          && current.stable
          && rectEquals(current.rect, nextRect)
        ) {
          return current;
        }
        revisionRef.current += 1;
        return {
          epoch: generation,
          target,
          rect: nextRect,
          stable: true,
          revision: revisionRef.current,
        };
      });
    }

    publishReset();
    if (!enabled) {
      active = false;
      return () => {
        active = false;
      };
    }
    bindTarget(resolveTarget(source));
    scheduleMeasure();

    const onViewportChange = () => scheduleMeasure();
    window.addEventListener('scroll', onViewportChange, { passive: true });
    window.addEventListener('resize', onViewportChange, { passive: true });
    window.addEventListener('orientationchange', onViewportChange, { passive: true });
    window.visualViewport?.addEventListener('resize', onViewportChange, { passive: true });
    window.visualViewport?.addEventListener('scroll', onViewportChange, { passive: true });
    void document.fonts?.ready.then(() => {
      if (isCurrent()) scheduleMeasure();
    });


    return () => {
      active = false;
      if (frame) window.cancelAnimationFrame(frame);
      resizeObserver?.disconnect();
      mutationObserver?.disconnect();
      removeScrollListeners();
      window.removeEventListener('scroll', onViewportChange);
      window.removeEventListener('resize', onViewportChange);
      window.removeEventListener('orientationchange', onViewportChange);
      window.visualViewport?.removeEventListener('resize', onViewportChange);
      window.visualViewport?.removeEventListener('scroll', onViewportChange);
    };
  }, [enabled, epoch, source]);

  return geometry;
}
