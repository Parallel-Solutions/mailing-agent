import { describe, expect, it } from 'vitest';
import {
  TARGET_PADDING,
  buildConnectorPath,
  calculatePanelPosition,
  expandBox,
  getViewportBounds,
  type Box,
  type Size,
} from './geometry';

function box(left: number, top: number, width: number, height: number): Box {
  return {
    left,
    top,
    right: left + width,
    bottom: top + height,
    width,
    height,
  };
}

function overlapArea(left: Box, right: Box) {
  return (
    Math.max(0, Math.min(left.right, right.right) - Math.max(left.left, right.left))
    * Math.max(0, Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top))
  );
}

function positionedBox(
  position: { left: number; top: number },
  size: Size,
): Box {
  return box(position.left, position.top, size.width, size.height);
}

describe('onboarding geometry', () => {
  it('keeps the panel inside the safe viewport and away from the target', () => {
    const viewport = { width: 1280, height: 720 };
    const navigation = box(330, 640, 620, 60);
    const target = box(362, 436, 121, 19);
    const panel = { width: 380, height: 207 };

    const position = calculatePanelPosition(
      target,
      panel,
      viewport,
      'right',
      navigation,
    );
    const panelBox = positionedBox(position, panel);
    const bounds = getViewportBounds(viewport, navigation);

    expect(panelBox.left).toBeGreaterThanOrEqual(bounds.left);
    expect(panelBox.right).toBeLessThanOrEqual(bounds.right);
    expect(panelBox.top).toBeGreaterThanOrEqual(bounds.top);
    expect(panelBox.bottom).toBeLessThanOrEqual(bounds.bottom);
    expect(overlapArea(panelBox, expandBox(target))).toBe(0);
  });

  it('uses the mobile safe zone when a side placement cannot fit', () => {
    const viewport = { width: 375, height: 844 };
    const navigation = box(8, 780, 359, 54);
    const target = box(106, 726, 121, 19);
    const panel = { width: 351, height: 198 };

    const position = calculatePanelPosition(
      target,
      panel,
      viewport,
      'right',
      navigation,
    );
    const panelBox = positionedBox(position, panel);
    const bounds = getViewportBounds(viewport, navigation);

    expect(panelBox.left).toBeGreaterThanOrEqual(bounds.left);
    expect(panelBox.right).toBeLessThanOrEqual(bounds.right);
    expect(panelBox.bottom).toBeLessThanOrEqual(bounds.bottom);
    expect(overlapArea(panelBox, expandBox(target))).toBe(0);
  });

  it('draws a short connector as a straight segment without a loop', () => {
    const target = box(362, 436, 121, 19);
    const panel = { width: 380, height: 207 };
    const position = {
      left: target.right + TARGET_PADDING + 60,
      top: 342,
    };

    const path = buildConnectorPath(target, position, panel);

    expect(path).toContain(' L ');
    expect(path).not.toContain(' C ');
  });

  it('returns no connector when the panel has to overlap the spotlight', () => {
    const target = box(100, 100, 200, 100);
    const panel = { width: 200, height: 100 };

    expect(buildConnectorPath(target, { left: 150, top: 110 }, panel)).toBe('');
  });
});
