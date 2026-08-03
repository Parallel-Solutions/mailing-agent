import { CloseOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState, OnboardingUpdate } from '@/api/types';
import {
  setActiveOnboardingStep,
} from './events';
import {
  BOTTOM_CONTROL_INSET,
  MOBILE_BOTTOM_CONTROL_INSET,
  MOBILE_TOP_CONTROL_INSET,
  TARGET_PADDING,
  TOP_CONTROL_INSET,
  buildConnectorPath,
  calculatePanelPosition,
  sameBox,
  sameSize,
  toBox,
  type Box,
  type Size,
} from './geometry';
import {
  ONBOARDING_CHAPTERS,
  ONBOARDING_STEPS,
  ONBOARDING_VERSION,
  findOnboardingChapterForStep,
  getOnboardingChapter,
  getOnboardingChapterSteps,
  type OnboardingChapterId,
} from './steps';
import {
  findVisibleOnboardingTarget,
  isOnboardingRouteActive,
} from './targeting';
import './OnboardingTour.css';

const queryKey = ['onboarding'];
const TARGET_WAIT_TIMEOUT_MS = 3_000;
const TARGET_STABLE_FRAME_COUNT = 4;
const ONBOARDING_CHAPTER_STORAGE_KEY = 'campaignflow:onboarding-chapter';

function readViewportSize(): Size {
  const visualViewport = window.visualViewport;
  return {
    width:
      visualViewport?.width
      || document.documentElement.clientWidth
      || window.innerWidth,
    height:
      visualViewport?.height
      || document.documentElement.clientHeight
      || window.innerHeight,
  };
}

function readStoredChapter(): OnboardingChapterId | undefined {
  const stored = window.sessionStorage.getItem(ONBOARDING_CHAPTER_STORAGE_KEY);
  return ONBOARDING_CHAPTERS.some((chapter) => chapter.id === stored)
    ? stored as OnboardingChapterId
    : undefined;
}

type OnboardingTourProps = {
  chapterId?: OnboardingChapterId;
};

export function OnboardingTour({ chapterId }: OnboardingTourProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const panelRef = useRef<HTMLElement | null>(null);
  const navigationRef = useRef<HTMLElement | null>(null);
  const targetRef = useRef<HTMLElement | null>(null);
  const initialized = useRef(false);
  const versionRestarted = useRef(false);
  const [current, setCurrent] = useState(0);
  const [activeChapterId, setActiveChapterId] = useState<OnboardingChapterId>(
    () => chapterId ?? readStoredChapter() ?? 'general',
  );
  const [locallyHidden, setLocallyHidden] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [panelReady, setPanelReady] = useState(false);
  const [targetBox, setTargetBox] = useState<Box | null>(null);
  const [targetVisible, setTargetVisible] = useState(false);
  const [panelSize, setPanelSize] = useState<Size>({ width: 370, height: 170 });
  const [navigationBox, setNavigationBox] = useState<Box | null>(null);
  const [viewport, setViewport] = useState<Size>(readViewportSize);

  const onboardingQuery = useQuery({
    queryKey,
    queryFn: onboardingApi.get,
    staleTime: 30_000,
  });

  const updateMutation = useMutation({
    mutationFn: (payload: OnboardingUpdate) => onboardingApi.update(payload),
    onSuccess: (nextState) =>
      queryClient.setQueryData<OnboardingState>(queryKey, nextState),
  });

  const state = onboardingQuery.data;
  const isActive = state?.status === 'active';
  const completedSteps = state?.completed_steps ?? [];
  const overlayVisible = Boolean(isActive && !locallyHidden);
  const activeChapter = getOnboardingChapter(activeChapterId);
  const chapterSteps = useMemo(
    () => getOnboardingChapterSteps(activeChapterId),
    [activeChapterId],
  );
  const currentStep = ONBOARDING_STEPS[current];
  const currentChapterIndex = Math.max(
    0,
    chapterSteps.findIndex((step) => step.id === currentStep?.id),
  );

  const panelPosition = calculatePanelPosition(
    targetVisible ? targetBox : null,
    panelSize,
    viewport,
    currentStep?.placement,
    navigationBox,
  );
  const arrowPath =
    panelReady && targetVisible && targetBox
      ? buildConnectorPath(targetBox, panelPosition, panelSize)
      : '';

  const goToStep = useCallback((index: number, persist = true) => {
    const bounded = Math.max(0, Math.min(index, ONBOARDING_STEPS.length - 1));
    const step = ONBOARDING_STEPS[bounded];
    const previous = ONBOARDING_STEPS[current];
    const nextChapterId = chapterId ?? activeChapterId
      ?? findOnboardingChapterForStep(step.id);
    const nextChapter = getOnboardingChapter(nextChapterId);
    const nextChapterSteps = getOnboardingChapterSteps(nextChapterId);

    if (
      nextChapter?.scope === 'local'
      && !nextChapterSteps.some((chapterStep) => chapterStep.id === step.id)
    ) {
      return;
    }

    setPanelReady(false);
    setCurrent(bounded);
    if (!isOnboardingRouteActive(step.route, location)) {
      navigate(step.route);
    }

    if (persist) {
      const nextIndex = nextChapterSteps.findIndex(
        (chapterStep) => chapterStep.id === step.id,
      );
      const previousIndex = nextChapterSteps.findIndex(
        (chapterStep) => chapterStep.id === previous?.id,
      );
      const nextCompleted =
        bounded !== current && previousIndex >= 0 && nextIndex > previousIndex && previous
          ? Array.from(new Set([...completedSteps, previous.id]))
          : completedSteps;
      updateMutation.mutate({
        status: 'active',
        current_step: bounded,
        completed_steps: nextCompleted,
      });
    }
  }, [
    activeChapterId,
    chapterId,
    completedSteps,
    current,
    location,
    navigate,
    updateMutation,
  ]);

  useEffect(() => {
    if (!chapterId) return;
    setActiveChapterId(chapterId);
    window.sessionStorage.setItem(ONBOARDING_CHAPTER_STORAGE_KEY, chapterId);
  }, [chapterId]);

  useEffect(() => {
    if (!state || state.version === ONBOARDING_VERSION || versionRestarted.current) return;
    versionRestarted.current = true;
    void onboardingApi.restart().then((nextState) => {
      queryClient.setQueryData<OnboardingState>(queryKey, nextState);
      window.sessionStorage.setItem(ONBOARDING_CHAPTER_STORAGE_KEY, 'general');
      setActiveChapterId('general');
      initialized.current = true;
      setLocallyHidden(false);
      goToStep(nextState.current_step, false);
    });
  }, [goToStep, queryClient, state]);

  useEffect(() => {
    if (!isActive || initialized.current) return;

    const requestedStep = ONBOARDING_STEPS[state?.current_step ?? 0];
    const storedChapterId = readStoredChapter();
    const storedChapterMatchesStep = storedChapterId
      ? getOnboardingChapterSteps(storedChapterId)
        .some((step) => step.id === requestedStep?.id)
      : false;
    const requestedChapterId =
      chapterId
      ?? (storedChapterMatchesStep ? storedChapterId : undefined)
      ?? findOnboardingChapterForStep(requestedStep?.id);
    const requestedChapterSteps = getOnboardingChapterSteps(requestedChapterId);
    const initialStep = requestedChapterSteps.some((step) => step.id === requestedStep?.id)
      ? requestedStep
      : requestedChapterSteps[0];
    const initialIndex = Math.max(
      0,
      ONBOARDING_STEPS.findIndex((step) => step.id === initialStep?.id),
    );

    initialized.current = true;
    setActiveChapterId(requestedChapterId);
    window.sessionStorage.setItem(ONBOARDING_CHAPTER_STORAGE_KEY, requestedChapterId);
    goToStep(initialIndex, false);
  }, [chapterId, goToStep, isActive, state?.current_step]);

  useEffect(() => {
    if (!isActive) {
      initialized.current = false;
      setLocallyHidden(false);
    }
  }, [isActive]);

  useEffect(() => {
    if (!overlayVisible || !currentStep) return;
    if (!isOnboardingRouteActive(currentStep.route, location)) {
      navigate(currentStep.route);
    }
  }, [currentStep, location, navigate, overlayVisible]);

  useLayoutEffect(() => {
    if (!overlayVisible) return;
    document.body.classList.add('campaignflow-onboarding-active');
    return () => document.body.classList.remove('campaignflow-onboarding-active');
  }, [overlayVisible]);

  useLayoutEffect(() => {
    setActiveOnboardingStep(
      overlayVisible && currentStep ? currentStep.id : null,
    );
  }, [currentStep, overlayVisible]);

  useEffect(
    () => () => setActiveOnboardingStep(null),
    [],
  );

  useLayoutEffect(() => {
    setPanelReady(false);
    setTargetVisible(false);
    setTargetBox(null);
    setNavigationBox(null);
    targetRef.current = null;

    if (!overlayVisible) {
      return;
    }

    const step = ONBOARDING_STEPS[current];
    if (!step || !isOnboardingRouteActive(step.route, location)) {
      return;
    }

    let cancelled = false;
    let animationFrame = 0;
    let stableFrames = 0;
    let ready = false;
    let targetIsVisible = false;
    let lastTargetBox: Box | null = null;
    let lastPanelSize: Size = { width: 0, height: 0 };
    let lastViewport: Size = { width: 0, height: 0 };
    let lastNavigationBox: Box | null = null;
    let missingSince = Date.now();
    let lastScrollAt = 0;

    const updateReady = (next: boolean) => {
      if (ready === next) return;
      ready = next;
      setPanelReady(next);
    };

    const updateTargetVisibility = (next: boolean) => {
      if (targetIsVisible === next) return;
      targetIsVisible = next;
      setTargetVisible(next);
    };

    const scheduleNextFrame = () => {
      animationFrame = window.requestAnimationFrame(trackGeometry);
    };

    const trackGeometry = () => {
      if (cancelled) return;

      let geometryChanged = false;
      const nextViewport = readViewportSize();
      if (!sameSize(lastViewport, nextViewport)) {
        lastViewport = nextViewport;
        setViewport(nextViewport);
        geometryChanged = true;
      }

      const panel = panelRef.current;
      if (panel) {
        const nextPanelSize = {
          width: panel.offsetWidth,
          height: panel.offsetHeight,
        };
        if (
          nextPanelSize.width > 0
          && nextPanelSize.height > 0
          && !sameSize(lastPanelSize, nextPanelSize)
        ) {
          lastPanelSize = nextPanelSize;
          setPanelSize(nextPanelSize);
          geometryChanged = true;
        }
      }

      const navigation = navigationRef.current;
      const nextNavigationBox = navigation
        ? toBox(navigation.getBoundingClientRect())
        : null;
      if (
        (nextNavigationBox && !sameBox(lastNavigationBox, nextNavigationBox))
        || (!nextNavigationBox && lastNavigationBox)
      ) {
        lastNavigationBox = nextNavigationBox;
        setNavigationBox(nextNavigationBox);
        geometryChanged = true;
      }

      if (!step.target) {
        updateTargetVisibility(false);
        stableFrames = geometryChanged ? 0 : stableFrames + 1;
        if (stableFrames >= TARGET_STABLE_FRAME_COUNT) updateReady(true);
        scheduleNextFrame();
        return;
      }

      let target = targetRef.current;
      if (!target?.isConnected) {
        target = findVisibleOnboardingTarget(step.target) ?? null;
        targetRef.current = target;
        if (target) missingSince = 0;
      }

      if (!target) {
        if (missingSince === 0) missingSince = Date.now();
        lastTargetBox = null;
        setTargetBox((previous) => previous === null ? previous : null);
        updateTargetVisibility(false);
        stableFrames = 0;
        if (Date.now() - missingSince >= TARGET_WAIT_TIMEOUT_MS) {
          updateReady(true);
        } else {
          updateReady(false);
        }
        scheduleNextFrame();
        return;
      }

      const nextTargetBox = toBox(target.getBoundingClientRect());
      if (!nextTargetBox.width || !nextTargetBox.height) {
        targetRef.current = null;
        missingSince = Date.now();
        updateTargetVisibility(false);
        updateReady(false);
        stableFrames = 0;
        scheduleNextFrame();
        return;
      }

      const mobile = nextViewport.width <= 640;
      const topInset = mobile ? MOBILE_TOP_CONTROL_INSET : TOP_CONTROL_INSET;
      const fallbackBottom =
        nextViewport.height
        - (mobile ? MOBILE_BOTTOM_CONTROL_INSET : BOTTOM_CONTROL_INSET);
      const bottomInset = Math.min(
        fallbackBottom,
        nextNavigationBox ? nextNavigationBox.top - 16 : fallbackBottom,
      );
      const now = Date.now();
      if (
        (
          nextTargetBox.top < topInset
          || nextTargetBox.bottom > bottomInset
        )
        && now - lastScrollAt > 300
      ) {
        lastScrollAt = now;
        target.scrollIntoView({
          block: 'center',
          inline: 'nearest',
          behavior: 'auto',
        });
        updateReady(false);
        stableFrames = 0;
        scheduleNextFrame();
        return;
      }

      if (!sameBox(lastTargetBox, nextTargetBox)) {
        lastTargetBox = nextTargetBox;
        setTargetBox(nextTargetBox);
        geometryChanged = true;
      }
      updateTargetVisibility(true);

      if (geometryChanged) {
        stableFrames = 0;
        updateReady(false);
      } else {
        stableFrames += 1;
        if (stableFrames >= TARGET_STABLE_FRAME_COUNT) updateReady(true);
      }

      scheduleNextFrame();
    };

    animationFrame = window.requestAnimationFrame(trackGeometry);

    return () => {
      cancelled = true;
      window.cancelAnimationFrame(animationFrame);
    };
  }, [current, location, overlayVisible]);

  const finish = useCallback(() => {
    if (finishing) return;
    setFinishing(true);
    updateMutation.mutate({
      status: 'completed',
      current_step: current,
      completed_steps: Array.from(new Set([
        ...completedSteps,
        ...chapterSteps.map((step) => step.id),
      ])),
    }, {
      onSuccess: () => {
        window.sessionStorage.removeItem(ONBOARDING_CHAPTER_STORAGE_KEY);
        setActiveOnboardingStep(null);
        setLocallyHidden(true);
        setFinishing(false);
      },
      onError: () => setFinishing(false),
    });
  }, [
    chapterSteps,
    completedSteps,
    current,
    finishing,
    updateMutation,
  ]);

  const close = useCallback(() => {
    if (finishing) return;
    setActiveOnboardingStep(null);
    setLocallyHidden(true);
    updateMutation.mutate({
      status: 'paused',
      current_step: current,
      completed_steps: completedSteps,
    }, { onError: () => setLocallyHidden(false) });
  }, [completedSteps, current, finishing, updateMutation]);

  useEffect(() => {
    if (!overlayVisible) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      close();
    };
    window.addEventListener('keydown', handleEscape, true);
    return () => window.removeEventListener('keydown', handleEscape, true);
  }, [close, overlayVisible]);

  if (!overlayVisible || !currentStep || !activeChapter) return null;

  const goToChapterStep = (chapterIndex: number) => {
    const nextStep = chapterSteps[chapterIndex];
    const globalIndex = ONBOARDING_STEPS.findIndex((step) => step.id === nextStep?.id);
    if (globalIndex >= 0) goToStep(globalIndex);
  };
  const isLastStep = currentChapterIndex === chapterSteps.length - 1;

  return (
    <div
      className={[
        'campaignflow-onboarding',
        panelReady ? 'campaignflow-onboarding--ready' : '',
        panelReady && targetVisible ? 'campaignflow-onboarding--targeted' : '',
      ].filter(Boolean).join(' ')}
      data-chapter={activeChapterId}
    >
      <div className="campaignflow-onboarding__blocker" aria-hidden="true" />

      {targetBox ? (
        <div
          className="campaignflow-onboarding__spotlight"
          style={{
            left: targetBox.left - TARGET_PADDING,
            top: targetBox.top - TARGET_PADDING,
            width: targetBox.width + TARGET_PADDING * 2,
            height: targetBox.height + TARGET_PADDING * 2,
          }}
          aria-hidden="true"
        />
      ) : null}

      <svg
        className="campaignflow-onboarding__connector"
        viewBox={`0 0 ${viewport.width} ${viewport.height}`}
        width={viewport.width}
        height={viewport.height}
        aria-hidden="true"
      >
        <defs>
          <marker
            id="campaignflow-onboarding-arrowhead"
            markerWidth="10"
            markerHeight="10"
            refX="8"
            refY="5"
            orient="auto"
            markerUnits="userSpaceOnUse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        {arrowPath ? (
          <path
            className="campaignflow-onboarding__connector-path"
            d={arrowPath}
            markerEnd="url(#campaignflow-onboarding-arrowhead)"
          />
        ) : null}
      </svg>

      <section
        ref={panelRef}
        className="campaignflow-onboarding__panel"
        style={{ left: panelPosition.left, top: panelPosition.top }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="campaignflow-onboarding-title"
        aria-describedby="campaignflow-onboarding-description"
      >
        <div className="campaignflow-onboarding__eyebrow">{activeChapter.title}</div>
        <h2 id="campaignflow-onboarding-title">{currentStep.title}</h2>
        <p id="campaignflow-onboarding-description">{currentStep.description}</p>
        {currentStep.details?.length ? (
          <ul className="campaignflow-onboarding__details">
            {currentStep.details.map((detail) => <li key={detail}>{detail}</li>)}
          </ul>
        ) : null}
        {currentStep.tip ? (
          <div className="campaignflow-onboarding__tip">
            <strong>Важно:</strong> {currentStep.tip}
          </div>
        ) : null}
      </section>

      <button
        type="button"
        className="campaignflow-onboarding__close"
        aria-label="Закрыть обучение"
        title="Закрыть обучение (Esc)"
        onClick={close}
        disabled={finishing}
      >
        <CloseOutlined />
      </button>

      <nav
        ref={navigationRef}
        className="campaignflow-onboarding__navigation"
        aria-label={`Шаг ${currentChapterIndex + 1} из ${chapterSteps.length}`}
      >
        <Button
          className="campaignflow-onboarding__back"
          disabled={currentChapterIndex === 0 || finishing}
          onClick={() => goToChapterStep(currentChapterIndex - 1)}
        >
          Назад
        </Button>

        <div className="campaignflow-onboarding__pagination" aria-hidden="true">
          {chapterSteps.map((step, index) => (
            <span
              key={step.id}
              className={[
                'campaignflow-onboarding__page',
                index === currentChapterIndex
                  ? 'campaignflow-onboarding__page--active'
                  : '',
                index < currentChapterIndex
                  ? 'campaignflow-onboarding__page--complete'
                  : '',
              ].filter(Boolean).join(' ')}
            />
          ))}
        </div>

        <Button
          type="primary"
          className="campaignflow-onboarding__next"
          loading={isLastStep && finishing}
          disabled={!panelReady || finishing}
          onClick={() => {
            if (isLastStep) finish();
            else goToChapterStep(currentChapterIndex + 1);
          }}
        >
          {isLastStep ? 'Готово' : 'Далее'}
        </Button>
      </nav>
    </div>
  );
}
