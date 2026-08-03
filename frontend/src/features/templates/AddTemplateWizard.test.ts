import { describe, expect, it, vi } from 'vitest';
import type { Template } from '@/api/types';
import { finishTemplateCreation } from './AddTemplateWizard';

describe('template creation completion', () => {
  it('closes the wizard before editor navigation', () => {
    const order: string[] = [];
    const onClose = vi.fn(() => order.push('close'));
    const onCreated = vi.fn(() => order.push('created'));

    finishTemplateCreation({
      template: { id: 'template-id' } as Template,
      onClose,
      onCreated,
    });

    expect(order).toEqual(['close', 'created']);
    expect(onClose).toHaveBeenCalledOnce();
    expect(onCreated).toHaveBeenCalledOnce();
  });
});
