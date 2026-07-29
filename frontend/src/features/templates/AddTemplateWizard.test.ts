import { describe, expect, it, vi } from 'vitest';
import type { Template } from '@/api/types';
import {
  ONBOARDING_ADVANCE_EVENT,
  type OnboardingAdvanceDetail,
} from '@/features/onboarding/events';
import { finishTemplateCreation } from './AddTemplateWizard';

describe('template creation completion', () => {
  it('closes the wizard before editor and onboarding navigation', () => {
    const order: string[] = [];
    const onClose = vi.fn(() => order.push('close'));
    const onCreated = vi.fn(() => order.push('created'));
    let advanceDetail: OnboardingAdvanceDetail | undefined;
    const handleAdvance = (event: Event) => {
      order.push('advance');
      advanceDetail = (event as CustomEvent<OnboardingAdvanceDetail>).detail;
    };
    window.addEventListener(ONBOARDING_ADVANCE_EVENT, handleAdvance);

    try {
      finishTemplateCreation({
        template: { id: 'template-id' } as Template,
        fromStepId: 'template-custom',
        onClose,
        onCreated,
      });
    } finally {
      window.removeEventListener(ONBOARDING_ADVANCE_EVENT, handleAdvance);
    }

    expect(order).toEqual(['close', 'created', 'advance']);
    expect(onClose).toHaveBeenCalledOnce();
    expect(onCreated).toHaveBeenCalledOnce();
    expect(advanceDetail).toEqual({
      fromId: 'template-custom',
      toId: 'audience-open',
    });
  });
});
