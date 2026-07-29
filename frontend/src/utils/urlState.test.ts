import { describe, expect, it } from 'vitest';
import {
  buildSearchParams,
  clearModalParams,
  readBoolParam,
  readEnumParam,
  readIntParam,
  sameSearchParams,
} from './urlState';

describe('urlState', () => {
  it('buildSearchParams merges and removes keys', () => {
    const current = new URLSearchParams('tab=dashboard&modal=filters');
    const next = buildSearchParams(current, { tab: 'recipients', modal: null }, ['modal_id']);
    expect(next.get('tab')).toBe('recipients');
    expect(next.has('modal')).toBe(false);
  });

  it('readEnumParam falls back to default', () => {
    const params = new URLSearchParams('tab=unknown');
    expect(readEnumParam(params, 'tab', ['dashboard', 'recipients'] as const, 'dashboard')).toBe('dashboard');
  });

  it('readIntParam clamps values', () => {
    const params = new URLSearchParams('step=9');
    expect(readIntParam(params, 'step', 0, 0, 3)).toBe(3);
  });

  it('readBoolParam accepts 1 and true', () => {
    expect(readBoolParam(new URLSearchParams('preview=1'), 'preview')).toBe(true);
    expect(readBoolParam(new URLSearchParams('preview=true'), 'preview')).toBe(true);
    expect(readBoolParam(new URLSearchParams(), 'preview')).toBe(false);
  });

  it('sameSearchParams compares serialized params', () => {
    const left = new URLSearchParams('a=1&b=2');
    const right = new URLSearchParams('b=2&a=1');
    expect(sameSearchParams(left, right)).toBe(false);
    expect(sameSearchParams(left, new URLSearchParams('a=1&b=2'))).toBe(true);
  });

  it('clearModalParams removes overlay keys', () => {
    const current = new URLSearchParams('tab=dashboard&modal=export&export_type=csv&preview=1');
    const next = clearModalParams(current);
    expect(next.get('tab')).toBe('dashboard');
    expect(next.has('modal')).toBe(false);
    expect(next.has('export_type')).toBe(false);
    expect(next.has('preview')).toBe(false);
  });
});
