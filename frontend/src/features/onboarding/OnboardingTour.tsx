import { useEffect, useMemo, useRef, useState } from 'react';
import { Tour } from 'antd';
import type { TourProps } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState, OnboardingUpdate } from '@/api/types';
import {
  ONBOARDING_ADVANCE_EVENT,
  ONBOARDING_ENTER_EVENT,
  type OnboardingAdvanceDetail,
} from './events';
import { ONBOARDING_STEPS, ONBOARDING_VERSION } from './steps';
import {
  findVisibleOnboardingSuccessor,
  findVisibleOnboardingTarget,
  isOnboardingRouteActive,
  resolveAvailableOnboardingStep,
} from './targeting';
import './OnboardingTour.css';

const queryKey = ['onboarding'];
const TARGET_WAIT_TIMEOUT_MS = 900;

function targetFor(selector?: string) {
  if (!selector) return undefined;
  return (() => findVisibleOnboardingTarget(selector)) as () => HTMLElement;
}

function OnboardingProgress({ current, total }: { current: number; total: number }) {
  const completedPercent = Math.round(((current + 1) / total) * 100);

  return (
    <div
      className="campaignflow-onboarding__progress"
      aria-label={`Шаг ${current + 1} из ${total}`}
    >
      <div className="campaignflow-onboarding__progress-label">
        <span>Шаг {current + 1} из {total}</span>
        <span>{completedPercent}%</span>
      </div>
      <div className="campaignflow-onboarding__progress-track" aria-hidden="true">
        <span style={{ width: `${completedPercent}%` }} />
      </div>
    </div>
  );
}

export function OnboardingTour() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [current, setCurrent] = useState(0);
  const [routeReady, setRouteReady] = useState(false);
  const [locallyHidden, setLocallyHidden] = useState(false);
  const initialized = useRef(false);
  const versionRestarted = useRef(false);

  const onboardingQuery = useQuery({
    queryKey,
    queryFn: onboardingApi.get,
    staleTime: 30_000,
  });

  const updateMutation = useMutation({
    mutationFn: (payload: OnboardingUpdate) => onboardingApi.update(payload),
    onSuccess: (nextState) => queryClient.setQueryData<OnboardingState>(queryKey, nextState),
  });

  const state = onboardingQuery.data;
  const isActive = state?.status === 'active';
  const completedSteps = state?.completed_steps ?? [];
  const overlayVisible = Boolean(isActive && !locallyHidden);
  const tourOpen = Boolean(overlayVisible && routeReady);

  const goToStep = (index: number, persist = true) => {
    const bounded = Math.max(0, Math.min(index, ONBOARDING_STEPS.length - 1));
    const step = ONBOARDING_STEPS[bounded];

    setRouteReady(false);
    setCurrent(bounded);
    if (!isOnboardingRouteActive(step.route, location)) {
      navigate(step.route);
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
  }, [current, completedSteps, location.pathname, location.search]);

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
    if (!overlayVisible) return;
    document.body.classList.add('campaignflow-onboarding-active');
    return () => document.body.classList.remove('campaignflow-onboarding-active');
  }, [overlayVisible]);

  useEffect(() => {
    if (!overlayVisible) {
      setRouteReady(false);
      return;
    }

    const step = ONBOARDING_STEPS[current];
    if (!step || !isOnboardingRouteActive(step.route, location)) {
      setRouteReady(false);
      return;
    }

    let enterFrame = 0;
    let settleFrame = 0;
    let fallbackTimer = 0;
    let enterRetryTimer = 0;
    let targetObserver: MutationObserver | null = null;
    let resolved = false;

    const reveal = () => {
      if (resolved) return;
      resolved = true;
      targetObserver?.disconnect();
      window.clearTimeout(fallbackTimer);
      window.clearInterval(enterRetryTimer);
      settleFrame = window.requestAnimationFrame(() => setRouteReady(true));
    };

    const announceStep = () => {
      window.dispatchEvent(
        new CustomEvent(ONBOARDING_ENTER_EVENT, { detail: { stepId: step.id } }),
      );
    };

    const resolveTarget = () => {
      if (!step.target || findVisibleOnboardingTarget(step.target)) {
        reveal();
        return true;
      }

      const successor = findVisibleOnboardingSuccessor(ONBOARDING_STEPS, current);
      if (successor === undefined) return false;

      resolved = true;
      targetObserver?.disconnect();
      window.clearTimeout(fallbackTimer);
      window.clearInterval(enterRetryTimer);
      goToStep(successor);
      return true;
    };

    const waitForTarget = () => {
      if (resolveTarget()) return;
      setRouteReady(false);

      targetObserver = new MutationObserver(() => {
        resolveTarget();
      });
      targetObserver.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
      });
      enterRetryTimer = window.setInterval(announceStep, 160);

      fallbackTimer = window.setTimeout(() => {
        window.clearInterval(enterRetryTimer);
        if (resolveTarget() || step.requiresAction) return;

        // Explanatory steps remain navigable even if an optional target failed
        // to mount. Action steps never show a blocking mask without a target.
        reveal();
      }, TARGET_WAIT_TIMEOUT_MS);
    };

    enterFrame = window.requestAnimationFrame(() => {
      if (resolveTarget()) return;
      announceStep();
      settleFrame = window.requestAnimationFrame(waitForTarget);
    });

    return () => {
      resolved = true;
      targetObserver?.disconnect();
      window.cancelAnimationFrame(enterFrame);
      window.cancelAnimationFrame(settleFrame);
      window.clearTimeout(fallbackTimer);
      window.clearInterval(enterRetryTimer);
    };
  }, [current, location.pathname, location.search, overlayVisible]);

  const finish = () => {
    setLocallyHidden(true);
    updateMutation.mutate({
      status: 'completed',
      current_step: ONBOARDING_STEPS.length - 1,
      completed_steps: ONBOARDING_STEPS.map((step) => step.id),
    }, { onError: () => setLocallyHidden(false) });
  };

  const close = () => {
    setLocallyHidden(true);
    updateMutation.mutate({
      status: 'paused',
      current_step: current,
      completed_steps: completedSteps,
    }, { onError: () => setLocallyHidden(false) });
  };

  const steps = useMemo<TourProps['steps']>(
    () => ONBOARDING_STEPS.map((step) => ({
      title: step.title,
      description: step.description,
      target: targetFor(step.target),
      className: `campaignflow-onboarding-step campaignflow-onboarding-step--${step.id}`,
      nextButtonProps: {
        children: step.nextLabel || (step.id === 'finish' ? 'Готово' : 'Далее'),
        disabled: step.requiresAction,
      },
      prevButtonProps: { children: 'Назад' },
      scrollIntoViewOptions: { block: 'nearest', inline: 'nearest', behavior: 'auto' },
    })),
    [],
  );

  return (
    <Tour
      rootClassName="campaignflow-onboarding"
      open={tourOpen}
      current={current}
      steps={steps}
      onChange={(next) => goToStep(
        resolveAvailableOnboardingStep(ONBOARDING_STEPS, next, current),
      )}
      onClose={close}
      onFinish={finish}
      indicatorsRender={(stepIndex, total) => (
        <OnboardingProgress current={stepIndex} total={total} />
      )}
      gap={{ offset: 10, radius: 14 }}
      animated={false}
      mask={{ color: 'rgba(15, 23, 42, 0.18)' }}
      zIndex={1010}
      disabledInteraction={false}
      closable={{ 'aria-label': 'Закрыть обучение' }}
    />
  );
}
