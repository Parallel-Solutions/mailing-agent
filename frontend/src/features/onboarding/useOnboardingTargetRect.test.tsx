import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useOnboardingTargetRect } from './useOnboardingTargetRect';

let nextFrameId = 0;
let frameQueue = new Map<number, FrameRequestCallback>();

class ResizeObserverMock {
  static instances: ResizeObserverMock[] = [];

  readonly observed = new Set<Element>();

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverMock.instances.push(this);
  }

  observe = (element: Element) => this.observed.add(element);
  unobserve = (element: Element) => this.observed.delete(element);
  disconnect = () => this.observed.clear();
}

function flushFrame() {
  const callbacks = Array.from(frameQueue.values());
  frameQueue = new Map();
  act(() => callbacks.forEach((callback) => callback(performance.now())));
}

function geometryRect(top: number, left = 20, width = 160, height = 48) {
  return {
    x: left,
    y: top,
    top,
    left,
    right: left + width,
    bottom: top + height,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect;
}

function GeometryProbe({ selector, epoch = 1 }: { selector: string; epoch?: number }) {
  const geometry = useOnboardingTargetRect(selector, { enabled: true, epoch });
  return (
    <output data-testid="geometry">
      {JSON.stringify({
        epoch: geometry.epoch,
        stable: geometry.stable,
        top: geometry.rect?.top ?? null,
        revision: geometry.revision,
      })}
    </output>
  );
}

function readGeometry() {
  return JSON.parse(screen.getByTestId('geometry').textContent || '{}') as {
    epoch: number;
    stable: boolean;
    top: number | null;
    revision: number;
  };
}

describe('useOnboardingTargetRect', () => {
  let visualViewport: EventTarget;

  beforeEach(() => {
    nextFrameId = 0;
    frameQueue = new Map();
    ResizeObserverMock.instances = [];
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const id = ++nextFrameId;
      frameQueue.set(id, callback);
      return id;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => frameQueue.delete(id));
    visualViewport = new EventTarget();
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: visualViewport,
    });
  });

  afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('publishes a rect only after two equal animation frames', () => {
    const root = document.createElement('main');
    const target = document.createElement('button');
    target.dataset.onboardingId = 'stable';
    vi.spyOn(target, 'getBoundingClientRect').mockReturnValue(geometryRect(40));
    root.append(target);
    document.body.append(root);

    render(<GeometryProbe selector="[data-onboarding-id='stable']" />);
    expect(readGeometry()).toMatchObject({ stable: false, top: null });

    flushFrame();
    expect(readGeometry()).toMatchObject({ stable: false, top: null });

    flushFrame();
    expect(readGeometry()).toMatchObject({ stable: true, top: 40, revision: 1 });
    expect(ResizeObserverMock.instances.at(-1)?.observed).toEqual(new Set([target, root]));
  });

  it('coalesces scroll events and updates both position and revision after stability', () => {
    const root = document.createElement('main');
    const scroller = document.createElement('div');
    scroller.style.overflowY = 'auto';
    const target = document.createElement('button');
    target.dataset.onboardingId = 'scrolling';
    let rect = geometryRect(20);
    vi.spyOn(target, 'getBoundingClientRect').mockImplementation(() => rect);
    scroller.append(target);
    root.append(scroller);
    document.body.append(root);

    render(<GeometryProbe selector="[data-onboarding-id='scrolling']" />);
    flushFrame();
    flushFrame();
    expect(readGeometry()).toMatchObject({ stable: true, top: 20, revision: 1 });

    rect = geometryRect(96);
    scroller.dispatchEvent(new Event('scroll'));
    scroller.dispatchEvent(new Event('scroll'));
    window.dispatchEvent(new Event('scroll'));
    expect(frameQueue).toHaveLength(1);

    flushFrame();
    expect(readGeometry()).toMatchObject({ top: 20, revision: 1 });
    flushFrame();
    expect(readGeometry()).toMatchObject({ stable: true, top: 96, revision: 2 });
  });

  it('reacts to mutation and visual viewport events and ignores the previous epoch', async () => {
    const root = document.createElement('main');
    const first = document.createElement('button');
    first.dataset.onboardingId = 'first';
    vi.spyOn(first, 'getBoundingClientRect').mockReturnValue(geometryRect(10));
    const second = document.createElement('button');
    second.dataset.onboardingId = 'second';
    let secondRect = geometryRect(70);
    vi.spyOn(second, 'getBoundingClientRect').mockImplementation(() => secondRect);
    root.append(first, second);
    document.body.append(root);

    const view = render(<GeometryProbe selector="[data-onboarding-id='first']" epoch={1} />);
    flushFrame();
    view.rerender(<GeometryProbe selector="[data-onboarding-id='second']" epoch={2} />);
    flushFrame();
    flushFrame();
    expect(readGeometry()).toMatchObject({ epoch: 2, stable: true, top: 70, revision: 1 });

    secondRect = geometryRect(130);
    await act(async () => {
      root.append(document.createElement('div'));
      await Promise.resolve();
    });
    visualViewport.dispatchEvent(new Event('resize'));
    expect(frameQueue).toHaveLength(1);
    flushFrame();
    flushFrame();
    expect(readGeometry()).toMatchObject({ epoch: 2, stable: true, top: 130, revision: 2 });
  });
});
