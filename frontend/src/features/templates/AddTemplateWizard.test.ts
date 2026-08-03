import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { createElement, useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';
import {
  AddTemplateWizard,
  finishTemplateCreation,
  type WizardStep,
} from './AddTemplateWizard';

vi.mock('@/api/templates', () => ({
  templatesApi: {
    starters: vi.fn(),
    models: vi.fn(),
  },
}));

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

function ControlledWizard({ open }: { open: boolean }) {
  const [step, setStep] = useState<WizardStep>('format');
  return createElement(AddTemplateWizard, {
    open,
    templateType: 'email',
    step,
    onStepChange: setStep,
    onClose: vi.fn(),
    onCreated: vi.fn(),
  });
}

function renderControlledWizard(open = true) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const tree = (isOpen: boolean) =>
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(AntdApp, null, createElement(ControlledWizard, { open: isOpen })),
    );
  const result = render(tree(open));
  return {
    ...result,
    setOpen: (isOpen: boolean) => result.rerender(tree(isOpen)),
  };
}

describe('template format selection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(templatesApi.starters).mockResolvedValue([]);
    vi.mocked(templatesApi.models).mockResolvedValue([]);
  });

  it('keeps the visual format when the controlled wizard moves to the gallery step', async () => {
    renderControlledWizard();

    const visualCard = screen.getByText(/HTML-письмо с дизайном/).closest('.ant-card');
    expect(visualCard).not.toBeNull();
    fireEvent.click(visualCard!);
    fireEvent.click(screen.getByRole('button', { name: 'Далее' }));

    expect(await screen.findByText('Пустой HTML-шаблон')).toBeInTheDocument();
    expect(screen.queryByText('Добавить')).not.toBeInTheDocument();
  });

  it('resets the visual format when the wizard is opened again', async () => {
    const wizard = renderControlledWizard();

    const visualCard = screen.getByText(/HTML-письмо с дизайном/).closest('.ant-card');
    expect(visualCard).not.toBeNull();
    fireEvent.click(visualCard!);

    wizard.setOpen(false);
    wizard.setOpen(true);
    fireEvent.click(await screen.findByRole('button', { name: 'Далее' }));

    expect(await screen.findByText('Добавить')).toBeInTheDocument();
    expect(screen.queryByText('Пустой HTML-шаблон')).not.toBeInTheDocument();
  });
});
