import { useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  buildSearchParams,
  sameSearchParams,
  searchParamsToString,
  type UrlParamPatch,
} from '@/utils/urlState';

export function useUrlNavigation() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const applyParams = useCallback(
    (patch: UrlParamPatch, remove: string[] = [], replace = false) => {
      const next = buildSearchParams(searchParams, patch, remove);
      if (sameSearchParams(searchParams, next)) return;
      setSearchParams(next, { replace });
    },
    [searchParams, setSearchParams],
  );

  const pushParams = useCallback(
    (patch: UrlParamPatch, remove: string[] = []) => {
      applyParams(patch, remove, false);
    },
    [applyParams],
  );

  const replaceParams = useCallback(
    (patch: UrlParamPatch, remove: string[] = []) => {
      applyParams(patch, remove, true);
    },
    [applyParams],
  );

  const pushPath = useCallback(
    (pathname: string, patch: UrlParamPatch = {}, remove: string[] = []) => {
      const next = buildSearchParams(new URLSearchParams(), patch, remove);
      navigate(`${pathname}${searchParamsToString(next)}`);
    },
    [navigate],
  );

  const replacePath = useCallback(
    (pathname: string, patch: UrlParamPatch = {}, remove: string[] = []) => {
      const next = buildSearchParams(new URLSearchParams(), patch, remove);
      navigate(`${pathname}${searchParamsToString(next)}`, { replace: true });
    },
    [navigate],
  );

  const closeOverlay = useCallback(
    (keys: string[] = []) => {
      pushParams({}, keys);
    },
    [pushParams],
  );

  return {
    searchParams,
    pushParams,
    replaceParams,
    pushPath,
    replacePath,
    closeOverlay,
  };
}
