import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState } from '@/api/types';
import type { OnboardingChapterId } from './steps';
import { OnboardingTour } from './OnboardingTour';

vi.mock('@/api/onboarding', () => ({
  onboardingApi: {
    get: vi.fn(),
    update: vi.fn(),
    restart: vi.fn(),
  },
  onboardingQueryKey: (username?: string | null) => ['onboarding', username || 'anonymous'],
  onboardingChapterStorageKey: (username?: string | null) => `campaignflow:onboarding-chapter:${username || 'anonymous'}`,
}));

const activeState: OnboardingState = {
  version: 8,
  status: 'active',
  current_step: 0,
  completed_steps: [],
  step_count: 85,
  available: true,
  paused_at: null,
  dismissed_at: null,
  completed_at: null,
  updated_at: null,
};

function renderTour({
  chapterId,
  initialEntry = '/',
}: {
  chapterId?: OnboardingChapterId;
  initialEntry?: string;
} = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <OnboardingTour chapterId={chapterId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('OnboardingTour', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
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
    renderTour();

    const closeButton = await screen.findByRole('button', { name: 'Закрыть обучение' });
    fireEvent.click(closeButton);

    expect(screen.queryByRole('button', { name: 'Закрыть обучение' })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(onboardingApi.update).toHaveBeenCalledWith({
        status: 'paused',
        current_step: 0,
        completed_steps: [],
      });
    });
  });

  it('closes with Escape and preserves the current page state', async () => {
    renderTour();

    await screen.findByRole('button', { name: 'Закрыть обучение' });
    fireEvent.keyDown(window, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(onboardingApi.update).toHaveBeenCalledWith({
        status: 'paused',
        current_step: 0,
        completed_steps: [],
      });
    });
  });

  it('keeps navigation and square pagination fixed outside the hint panel', async () => {
    const { container } = renderTour();

    await screen.findByRole('heading', { name: 'Добро пожаловать в ai offer' });
    const navigation = screen.getByRole('navigation', { name: 'Шаг 1 из 12' });
    expect(navigation).toContainElement(screen.getByRole('button', { name: 'Назад' }));
    expect(navigation).toContainElement(screen.getByRole('button', { name: 'Далее' }));
    expect(screen.getByRole('button', { name: 'Назад' })).toBeDisabled();
    await waitFor(() => {
      expect(container.querySelector('.campaignflow-onboarding__next')).toBeEnabled();
    });
    expect(container.querySelectorAll('.campaignflow-onboarding__page')).toHaveLength(12);
    expect(container.querySelectorAll('.campaignflow-onboarding__page--active')).toHaveLength(1);
    expect(screen.getByRole('dialog')).not.toContainElement(navigation);
  });

  it('renders only the selected page-local tutorial', async () => {
    const { container } = renderTour({
      chapterId: 'templates',
      initialEntry: '/templates',
    });

    await screen.findByRole('heading', { name: 'Шаблоны' });
    expect(screen.getByText('Шаблоны', { selector: '.campaignflow-onboarding__eyebrow' }))
      .toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Шаг 1 из 17' })).toBeInTheDocument();
    expect(container.querySelectorAll('.campaignflow-onboarding__page')).toHaveLength(17);
  });

  it('uses the darker overlay and a separate curved connector layer', async () => {
    const { container } = renderTour();

    await screen.findByRole('dialog');
    expect(container.querySelector('.campaignflow-onboarding__blocker')).toBeInTheDocument();
    expect(container.querySelector('.campaignflow-onboarding__connector')).toBeInTheDocument();
    expect(container.querySelector('.campaignflow-onboarding__panel')).toBeInTheDocument();
  });

  it('keeps a targetless intro usable when animation frames are stalled', async () => {
    vi.stubGlobal('requestAnimationFrame', () => 1);
    const { container } = renderTour();

    await screen.findByRole('dialog');
    await waitFor(() => {
      expect(container.querySelector('.campaignflow-onboarding'))
        .toHaveClass('campaignflow-onboarding--ready');
      expect(container.querySelector('.campaignflow-onboarding__next')).toBeEnabled();
    });
  });
});
