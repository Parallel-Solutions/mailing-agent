import type { OnboardingPlacement } from './steps';

export const DESKTOP_VIEWPORT_INSET = 20;
export const MOBILE_VIEWPORT_INSET = 12;
export const TOP_CONTROL_INSET = 72;
export const MOBILE_TOP_CONTROL_INSET = 60;
export const BOTTOM_CONTROL_INSET = 104;
export const MOBILE_BOTTOM_CONTROL_INSET = 88;
export const PANEL_GAP = 60;
export const TARGET_PADDING = 10;

export type Box = {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
};

export type Size = {
  width: number;
  height: number;
};

export type Position = {
  left: number;
  top: number;
};

export type ViewportBounds = {
  left: number;
  top: number;
  right: number;
  bottom: number;
};

export function toBox(rect: DOMRect): Box {
  return {
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
    width: rect.width,
    height: rect.height,
  };
}

export function sameBox(previous: Box | null, next: Box, tolerance = 0.5) {
  if (!previous) return false;
  return (
    Math.abs(previous.left - next.left) < tolerance
    && Math.abs(previous.top - next.top) < tolerance
    && Math.abs(previous.width - next.width) < tolerance
    && Math.abs(previous.height - next.height) < tolerance
  );
}

export function sameSize(previous: Size, next: Size, tolerance = 0.5) {
  return (
    Math.abs(previous.width - next.width) < tolerance
    && Math.abs(previous.height - next.height) < tolerance
  );
}

export function expandBox(box: Box, padding = TARGET_PADDING): Box {
  return {
    left: box.left - padding,
    top: box.top - padding,
    right: box.right + padding,
    bottom: box.bottom + padding,
    width: box.width + padding * 2,
    height: box.height + padding * 2,
  };
}

function clamp(value: number, minimum: number, maximum: number) {
  if (maximum < minimum) return minimum;
  return Math.min(Math.max(value, minimum), maximum);
}

function oppositePlacement(placement: OnboardingPlacement): OnboardingPlacement {
  if (placement === 'top') return 'bottom';
  if (placement === 'bottom') return 'top';
  if (placement === 'left') return 'right';
  return 'left';
}

function candidatePosition(
  target: Box,
  panel: Size,
  placement: OnboardingPlacement,
): Position {
  const targetCenterX = target.left + target.width / 2;
  const targetCenterY = target.top + target.height / 2;

  if (placement === 'top') {
    return {
      left: targetCenterX - panel.width / 2,
      top: target.top - panel.height - PANEL_GAP,
    };
  }
  if (placement === 'bottom') {
    return {
      left: targetCenterX - panel.width / 2,
      top: target.bottom + PANEL_GAP,
    };
  }
  if (placement === 'left') {
    return {
      left: target.left - panel.width - PANEL_GAP,
      top: targetCenterY - panel.height / 2,
    };
  }
  return {
    left: target.right + PANEL_GAP,
    top: targetCenterY - panel.height / 2,
  };
}

function positionToBox(position: Position, size: Size): Box {
  return {
    left: position.left,
    top: position.top,
    right: position.left + size.width,
    bottom: position.top + size.height,
    width: size.width,
    height: size.height,
  };
}

function overlapArea(left: Box, right: Box) {
  return (
    Math.max(0, Math.min(left.right, right.right) - Math.max(left.left, right.left))
    * Math.max(0, Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top))
  );
}

function overflowDistance(box: Box, bounds: ViewportBounds) {
  return (
    Math.max(0, bounds.left - box.left)
    + Math.max(0, box.right - bounds.right)
    + Math.max(0, bounds.top - box.top)
    + Math.max(0, box.bottom - bounds.bottom)
  );
}

export function getViewportBounds(
  viewport: Size,
  navigation?: Box | null,
): ViewportBounds {
  const mobile = viewport.width <= 640;
  const inset = mobile ? MOBILE_VIEWPORT_INSET : DESKTOP_VIEWPORT_INSET;
  const top = mobile ? MOBILE_TOP_CONTROL_INSET : TOP_CONTROL_INSET;
  const fallbackBottom =
    viewport.height - (mobile ? MOBILE_BOTTOM_CONTROL_INSET : BOTTOM_CONTROL_INSET);
  const navigationBottom = navigation ? navigation.top - 16 : fallbackBottom;

  return {
    left: inset,
    top,
    right: viewport.width - inset,
    bottom: Math.min(fallbackBottom, navigationBottom),
  };
}

export function calculatePanelPosition(
  target: Box | null,
  panel: Size,
  viewport: Size,
  preferredPlacement: OnboardingPlacement = 'bottom',
  navigation?: Box | null,
): Position {
  const bounds = getViewportBounds(viewport, navigation);
  const maxLeft = bounds.right - panel.width;
  const maxTop = bounds.bottom - panel.height;

  if (!target) {
    return {
      left: clamp((viewport.width - panel.width) / 2, bounds.left, maxLeft),
      top: clamp(
        bounds.top + (bounds.bottom - bounds.top - panel.height) / 2,
        bounds.top,
        maxTop,
      ),
    };
  }

  const perpendicular: OnboardingPlacement[] =
    preferredPlacement === 'top' || preferredPlacement === 'bottom'
      ? ['right', 'left']
      : ['bottom', 'top'];
  const placements = [
    preferredPlacement,
    oppositePlacement(preferredPlacement),
    ...perpendicular,
  ];
  const spotlight = expandBox(target);

  const candidates = placements.map((placement, order) => {
    const raw = candidatePosition(spotlight, panel, placement);
    const position = {
      left: clamp(raw.left, bounds.left, maxLeft),
      top: clamp(raw.top, bounds.top, maxTop),
    };
    const panelBox = positionToBox(position, panel);
    const displacement = Math.abs(position.left - raw.left) + Math.abs(position.top - raw.top);
    const score =
      overlapArea(panelBox, spotlight) * 10_000
      + overflowDistance(panelBox, bounds) * 10_000
      + displacement * 10
      + order;
    return { position, score };
  });

  return candidates.sort((left, right) => left.score - right.score)[0].position;
}

export function buildConnectorPath(
  target: Box,
  panelPosition: Position,
  panel: Size,
) {
  const spotlight = expandBox(target);
  const panelBox = positionToBox(panelPosition, panel);
  const panelCenterX = panelBox.left + panelBox.width / 2;
  const panelCenterY = panelBox.top + panelBox.height / 2;
  const targetCenterX = spotlight.left + spotlight.width / 2;
  const targetCenterY = spotlight.top + spotlight.height / 2;

  let start: { x: number; y: number };
  let end: { x: number; y: number };
  let axis: 'horizontal' | 'vertical';

  if (panelBox.right <= spotlight.left) {
    start = { x: panelBox.right + 3, y: panelCenterY };
    end = { x: spotlight.left - 3, y: targetCenterY };
    axis = 'horizontal';
  } else if (panelBox.left >= spotlight.right) {
    start = { x: panelBox.left - 3, y: panelCenterY };
    end = { x: spotlight.right + 3, y: targetCenterY };
    axis = 'horizontal';
  } else if (panelBox.bottom <= spotlight.top) {
    start = { x: panelCenterX, y: panelBox.bottom + 3 };
    end = { x: targetCenterX, y: spotlight.top - 3 };
    axis = 'vertical';
  } else if (panelBox.top >= spotlight.bottom) {
    start = { x: panelCenterX, y: panelBox.top - 3 };
    end = { x: targetCenterX, y: spotlight.bottom + 3 };
    axis = 'vertical';
  } else {
    return '';
  }

  const distance = Math.hypot(end.x - start.x, end.y - start.y);
  if (distance < 96) {
    return `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
  }

  if (axis === 'horizontal') {
    const controlOffset = (end.x - start.x) * 0.42;
    return `M ${start.x} ${start.y} C ${start.x + controlOffset} ${start.y}, ${
      end.x - controlOffset
    } ${end.y}, ${end.x} ${end.y}`;
  }

  const controlOffset = (end.y - start.y) * 0.42;
  return `M ${start.x} ${start.y} C ${start.x} ${start.y + controlOffset}, ${
    end.x
  } ${end.y - controlOffset}, ${end.x} ${end.y}`;
}
