import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Modal, Tour } from 'antd';
import type { TourProps } from 'antd';
import { useLocation, useNavigate } from 'react-router-dom';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState, OnboardingUpdate } from '@/api/types';
import {
  ONBOARDING_ADVANCE_EVENT,
  ONBOARDING_ENTER_EVENT,
  type OnboardingAdvanceDetail,
} from './events';
import { ONBOARDING_STEPS, ONBOARDING_VERSION } from './steps';

const queryKey = ['onboarding'];

type HighlightRect = {
  top: number;
  right: number;
  bottom: number;
  left: number;
};

function OnboardingBlur({
  rect,
  transitioning,
}: {
  rect: HighlightRect | null;
  transitioning: boolean;
}) {
  const clipPath = rect
    ? `polygon(evenodd, 0 0, 100vw 0, 100vw 100vh, 0 100vh, 0 0, ${rect.left}px ${rect.top}px, ${rect.left}px ${rect.bottom}px, ${rect.right}px ${rect.bottom}px, ${rect.right}px ${rect.top}px, ${rect.left}px ${rect.top}px)`
    : undefined;

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1009,
        pointerEvents: transitioning ? 'auto' : 'none',
        clipPath,
        WebkitClipPath: clipPath,
        backgroundColor: transitioning ? 'rgba(15, 23, 42, 0.58)' : 'transparent',
        backdropFilter: 'blur(4px)',
        WebkitBackdropFilter: 'blur(4px)',
        transition: transitioning
          ? 'none'
          : 'clip-path 320ms cubic-bezier(0.22, 1, 0.36, 1), background-color 180ms ease-out',
        willChange: 'clip-path, background-color',
      }}
    />
  );
}

function targetFor(selector?: string) {
  if (!selector) return undefined;
  return (() => document.querySelector<HTMLElement>(selector)) as () => HTMLElement;
}

function resolveAvailableStep(index: number, current: number) {
  const direction = index >= current ? 1 : -1;
  let candidate = index;
  while (candidate >= 0 && candidate < ONBOARDING_STEPS.length) {
    const step = ONBOARDING_STEPS[candidate];
    if (!step.skipIfTargetMissing || !step.target || document.querySelector(step.target)) {
      return candidate;
    }
    candidate += direction;
  }
  return Math.max(0, Math.min(index, ONBOARDING_STEPS.length - 1));
}

export function OnboardingTour() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [current, setCurrent] = useState(0);
  const [routeReady, setRouteReady] = useState(false);
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [locallyHidden, setLocallyHidden] = useState(false);
  const [highlightRect, setHighlightRect] = useState<HighlightRect | null>(null);
  const initialized = useRef(false);
  const versionRestarted = useRef(false);

  const onboardingQuery = useQuery({
    queryKey,
    queryFn: onboardingApi.get,
    staleTime: 30_000,
  });

  const updateMutation = useMutation({
    mutationFn: (payload: OnboardingUpdate) => onboardingApi.update(payload),
    onSuccess: (state) => queryClient.setQueryData<OnboardingState>(queryKey, state),
  });

  const state = onboardingQuery.data;
  const isActive = state?.status === 'active';
  const completedSteps = state?.completed_steps ?? [];
  const overlayVisible = Boolean(isActive && !closeDialogOpen && !locallyHidden);
  const currentStepHasTarget = Boolean(ONBOARDING_STEPS[current]?.target);
  const tourOpen = Boolean(
    overlayVisible && routeReady && (!currentStepHasTarget || highlightRect),
  );

  const goToStep = (index: number, persist = true) => {
    setRouteReady(false);
    setHighlightRect(null);
    const bounded = Math.max(0, Math.min(index, ONBOARDING_STEPS.length - 1));
    const step = ONBOARDING_STEPS[bounded];
    const routeChanged = location.pathname !== step.route;
    const enterStep = () => {
      window.dispatchEvent(
        new CustomEvent(ONBOARDING_ENTER_EVENT, { detail: { stepId: step.id } }),
      );
      window.setTimeout(() => {
        setCurrent(bounded);
        setRouteReady(true);
      }, routeChanged ? 60 : 40);
    };

    if (routeChanged) {
      setRouteReady(false);
      navigate(step.route);
      window.setTimeout(enterStep, 260);
    } else if (!persist) {
      window.setTimeout(enterStep, 120);
    } else {
      enterStep();
    }

    if (persist) {
      const previous = ONBOARDING_STEPS[current]?.id;
      const nextCompleted = index > current && previous
        ? Array.from(new Set([...completedSteps, previous]))
        : completedSteps;
      updateMutation.mutate({
        status: 'active',
        current_step: bounded,
        completed_steps: nextCompleted,
      });
    }
  };
  useEffect(() => {
    if (!state || state.version === ONBOARDING_VERSION || versionRestarted.current) return;
    versionRestarted.current = true;
    void onboardingApi.restart().then((nextState) => {
      queryClient.setQueryData<OnboardingState>(queryKey, nextState);
      initialized.current = true;
      setLocallyHidden(false);
      goToStep(nextState.current_step, false);
    });
  }, [queryClient, state]);

  useEffect(() => {
    const handleAdvance = (event: Event) => {
      const detail = (event as CustomEvent<OnboardingAdvanceDetail>).detail;
      if (!detail || ONBOARDING_STEPS[current]?.id !== detail.fromId) return;
      const next = detail.toId
        ? ONBOARDING_STEPS.findIndex((step) => step.id === detail.toId)
        : current + 1;
      if (next >= 0) goToStep(next);
    };
    window.addEventListener(ONBOARDING_ADVANCE_EVENT, handleAdvance);
    return () => window.removeEventListener(ONBOARDING_ADVANCE_EVENT, handleAdvance);
  }, [current, completedSteps, location.pathname]);


  useEffect(() => {
    if (!isActive || initialized.current) return;
    initialized.current = true;
    goToStep(state?.current_step ?? 0, false);
  }, [isActive, state?.current_step]);

  useEffect(() => {
    if (!isActive) {
      initialized.current = false;
      setLocallyHidden(false);
    }
  }, [isActive]);

  useEffect(() => {
    if (!overlayVisible) {
      setHighlightRect(null);
      return;
    }
    if (!routeReady) return;

    let animationFrame = 0;
    let settleTimer = 0;
    let recoveryTimer = 0;
    let targetObserver: ResizeObserver | null = null;
    const updateHighlightRect = () => {
      const selector = ONBOARDING_STEPS[current]?.target;
      const element = selector ? document.querySelector<HTMLElement>(selector) : null;
      if (!element) {
        setHighlightRect(null);
        const stepId = ONBOARDING_STEPS[current]?.id;
        const recoverable = [
          'connection-details',
          'connection-auth',
          'connection-api-provider',
          'connection-credentials',
          'connection-limits',
          'connection-submit',
          'template-format',
          'template-source',
          'template-custom',
        ].includes(stepId || '');
        if (recoverable && !recoveryTimer) {
          recoveryTimer = window.setTimeout(() => {
            if (selector && document.querySelector(selector)) return;
            const retryCurrentStep = [
              'connection-api-provider',
              'connection-limits',
              'template-format',
              'template-source',
              'template-custom',
            ].includes(stepId || '');
            const recoveryStepId = retryCurrentStep
              ? stepId
              : 'connection-details';
            const recoveryIndex = ONBOARDING_STEPS.findIndex(
              (step) => step.id === recoveryStepId,
            );
            if (recoveryIndex >= 0) {
              setRouteReady(false);
              goToStep(recoveryIndex);
            }
          }, 200);
        }
        return;
      }

      const rect = element.getBoundingClientRect();
      const gap = 6;
      setHighlightRect({
        top: Math.max(0, rect.top - gap),
        right: Math.min(window.innerWidth, rect.right + gap),
        bottom: Math.min(window.innerHeight, rect.bottom + gap),
        left: Math.max(0, rect.left - gap),
      });
    };

    animationFrame = window.requestAnimationFrame(updateHighlightRect);
    settleTimer = window.setTimeout(() => {
      updateHighlightRect();
      window.dispatchEvent(new Event('resize'));
    }, 320);
    const selector = ONBOARDING_STEPS[current]?.target;
    const targetElement = selector ? document.querySelector<HTMLElement>(selector) : null;
    if (targetElement && typeof ResizeObserver !== 'undefined') {
      targetObserver = new ResizeObserver(() => {
        updateHighlightRect();
        window.dispatchEvent(new Event('resize'));
      });
      targetObserver.observe(targetElement);
    }
    window.addEventListener('resize', updateHighlightRect);
    window.addEventListener('scroll', updateHighlightRect, true);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      targetObserver?.disconnect();
      window.clearTimeout(settleTimer);
      window.clearTimeout(recoveryTimer);
      window.removeEventListener('resize', updateHighlightRect);
      window.removeEventListener('scroll', updateHighlightRect, true);
    };
  }, [current, location.pathname, overlayVisible, routeReady]);

  const finish = () => {
    setLocallyHidden(true);
    updateMutation.mutate({
      status: 'completed',
      current_step: ONBOARDING_STEPS.length - 1,
      completed_steps: ONBOARDING_STEPS.map((step) => step.id),
    }, { onError: () => setLocallyHidden(false) });
  };

  const close = (status: 'paused' | 'dismissed') => {
    setLocallyHidden(true);
    updateMutation.mutate({
      status,
      current_step: current,
      completed_steps: completedSteps,
    }, { onError: () => setLocallyHidden(false) });
    setCloseDialogOpen(false);
  };

  const steps = useMemo<TourProps['steps']>(
    () => ONBOARDING_STEPS.map((step) => ({
      title: step.title,
      description: step.description,
      target: targetFor(step.target),
      nextButtonProps: {
        children: step.nextLabel || (step.id === 'finish' ? 'Завершить' : 'Далее'),
        disabled: step.requiresAction,
      },
      prevButtonProps: { children: 'Назад' },
    })),
    [],
  );

  return (
    <>
      {overlayVisible ? (
        <OnboardingBlur rect={highlightRect} transitioning={!routeReady} />
      ) : null}
      <Tour
        open={tourOpen}
        current={current}
        steps={steps}
        onChange={(next) => goToStep(resolveAvailableStep(next, current))}
        onClose={() => setCloseDialogOpen(true)}
        onFinish={finish}
        gap={{ offset: 6, radius: 12 }}
        animated={{ placeholder: true }}
        mask={{ color: 'rgba(15, 23, 42, 0.58)' }}
        zIndex={1010}
        disabledInteraction={false}
      />
      <Modal
        title="Закрыть обучение?"
        open={closeDialogOpen}
        onCancel={() => setCloseDialogOpen(false)}
        footer={[
          <Button key="return" onClick={() => setCloseDialogOpen(false)}>
            Вернуться к обучению
          </Button>,
          <Button key="dismiss" danger onClick={() => close('dismissed')}>
            Больше не показывать
          </Button>,
          <Button key="pause" type="primary" onClick={() => close('paused')}>
            Продолжить позже
          </Button>,
        ]}
      >
        Прогресс сохранится. Обучение можно в любой момент запустить снова по кнопке «?» в верхней панели.
      </Modal>
    </>
  );
}
