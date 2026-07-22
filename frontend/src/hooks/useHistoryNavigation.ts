import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

type HistoryState = {
  idx?: number;
};

function readHistoryIdx(): number {
  const state = window.history.state as HistoryState | null;
  return typeof state?.idx === 'number' ? state.idx : 0;
}

export function useHistoryNavigation() {
  const navigate = useNavigate();
  const location = useLocation();
  const forwardStepsRef = useRef(0);
  const [canGoBack, setCanGoBack] = useState(false);
  const [canGoForward, setCanGoForward] = useState(false);

  useEffect(() => {
    const idx = readHistoryIdx();
    setCanGoBack(idx > 0);
    setCanGoForward(forwardStepsRef.current > 0);
  }, [location.key, location.pathname, location.search]);

  const goBack = useCallback(() => {
    if (!canGoBack) return;
    forwardStepsRef.current += 1;
    navigate(-1);
  }, [canGoBack, navigate]);

  const goForward = useCallback(() => {
    if (forwardStepsRef.current <= 0) return;
    forwardStepsRef.current -= 1;
    navigate(1);
  }, [navigate]);

  return { goBack, goForward, canGoBack, canGoForward };
}
