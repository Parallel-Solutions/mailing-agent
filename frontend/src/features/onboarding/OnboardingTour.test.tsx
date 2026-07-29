import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState } from '@/api/types';
import { OnboardingTour } from './OnboardingTour';

vi.mock('antd', () => ({
  Tour: ({
    open,
    onClose,
  }: {
    open?: boolean;
    onClose?: () => void;
  }) => open
    ? <button aria-label="Закрыть обучение" onClick={onClose}>Закрыть</button>
    : null,
}));

vi.mock('@/api/onboarding', () => ({
  onboardingApi: {
    get: vi.fn(),
    update: vi.fn(),
    restart: vi.fn(),
  },
}));

const activeState: OnboardingState = {
  version: 5,
  status: 'active',
  current_step: 0,
  completed_steps: [],
  step_count: 24,
  available: true,
  paused_at: null,
  dismissed_at: null,
  completed_at: null,
  updated_at: null,
};

describe('OnboardingTour', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      'requestAnimationFrame',
      (callback: FrameRequestCallback) => window.setTimeout(() => callback(0), 0),
    );
    vi.stubGlobal('cancelAnimationFrame', (handle: number) => window.clearTimeout(handle));
    vi.mocked(onboardingApi.get).mockResolvedValue(activeState);
    vi.mocked(onboardingApi.update).mockResolvedValue({
      ...activeState,
      status: 'paused',
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('closes immediately and saves the current step as paused', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <OnboardingTour />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const closeButton = await screen.findByRole('button', { name: 'Закрыть обучение' });
    fireEvent.click(closeButton);

    expect(screen.queryByRole('button', { name: 'Закрыть обучение' })).not.toBeInTheDocument();
    expect(screen.queryByText('Закрыть обучение?')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(onboardingApi.update).toHaveBeenCalledWith({
        status: 'paused',
        current_step: 0,
        completed_steps: [],
      });
    });
  });
});
