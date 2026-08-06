import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { onboardingApi } from '@/api/onboarding';
import { AppLayout } from './AppLayout';

const tourTracker = vi.hoisted(() => ({ mounts: 0 }));

vi.mock('@ant-design/pro-components', () => ({
  PageContainer: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  ProLayout: ({
    children,
    actionsRender,
  }: {
    children: React.ReactNode;
    actionsRender?: () => React.ReactNode[];
  }) => (
    <div>
      <div>{actionsRender?.()}</div>
      {children}
    </div>
  ),
}));

vi.mock('@/api/onboarding', () => ({
  onboardingApi: {
    update: vi.fn(),
  },
  onboardingQueryKey: (username?: string | null) => ['onboarding', username || 'anonymous'],
  onboardingChapterStorageKey: (username?: string | null) => `campaignflow:onboarding-chapter:${username || 'anonymous'}`,
}));

vi.mock('@/features/onboarding/OnboardingTour', async () => {
  const React = await import('react');
  return {
    OnboardingTour: () => {
      const [instance] = React.useState(() => ++tourTracker.mounts);
      return <div data-testid="onboarding-tour">tour-{instance}</div>;
    },
  };
});

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isAppAdmin: false }),
}));

vi.mock('@/stores/authStore', () => ({
  useAuthStore: (
    selector: (state: { user: { username: string; company: null }; logout: () => Promise<void> }) => unknown,
  ) => selector({
    user: { username: 'onboarding-user', company: null },
    logout: vi.fn(async () => undefined),
  }),
}));

describe('global onboarding launcher', () => {
  it('opens the chapter menu and remounts the selected tour', async () => {
    tourTracker.mounts = 0;
    vi.mocked(onboardingApi.update).mockResolvedValue({
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
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/']}>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<div>content</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByTestId('onboarding-tour')).toHaveTextContent('tour-1');
    fireEvent.click(screen.getByRole('button', { name: 'Запустить обучение' }));
    fireEvent.click(await screen.findByText('Общее обучение'));

    await waitFor(() => {
      expect(screen.getByTestId('onboarding-tour')).toHaveTextContent('tour-2');
    });
    expect(onboardingApi.update).toHaveBeenCalledWith({
      status: 'active',
      current_step: 0,
      completed_steps: [],
    });
    expect(
      window.sessionStorage.getItem('campaignflow:onboarding-chapter:onboarding-user'),
    ).toBe('general');
  });
});
