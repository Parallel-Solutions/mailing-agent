import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { sameSearchParams } from '@/utils/urlState';import {
  DASHBOARD_CACHE_PREFIX,
  isStatsTabKey,
  type StatsTabKey,
} from '../constants';

export type StatsFilters = {
  period_from?: string;
  period_to?: string;
  campaign?: string;
  provider?: string;
  providers?: string;
  consent_status?: string;
  manager_action?: string;
  organization?: string;
  problems_only?: boolean;
  quick_filter?: string;
  q?: string;
};

export type StatsPagination = {
  recipients: number;
  consents: number;
  subscribes: number;
  unsubscribes: number;
  problems: number;
};

const FILTER_KEYS = [
  'period_from',
  'period_to',
  'campaign',
  'provider',
  'providers',
  'consent_status',
  'manager_action',
  'organization',
  'problems_only',
  'quick_filter',
  'q',
] as const;

function readFiltersFromParams(params: URLSearchParams): StatsFilters {
  const filters: StatsFilters = {};
  const from = params.get('from') || params.get('period_from');
  const to = params.get('to') || params.get('period_to');
  if (from) filters.period_from = from;
  if (to) filters.period_to = to;
  if (params.get('campaign')) filters.campaign = params.get('campaign') || undefined;
  if (params.get('provider')) filters.provider = params.get('provider') || undefined;
  if (params.get('providers')) filters.providers = params.get('providers') || undefined;
  if (params.get('consent_status')) filters.consent_status = params.get('consent_status') || undefined;
  if (params.get('manager_action')) filters.manager_action = params.get('manager_action') || undefined;
  if (params.get('org') || params.get('organization')) {
    filters.organization = params.get('org') || params.get('organization') || undefined;
  }
  if (params.get('problems_only') === '1' || params.get('problems_only') === 'true') {
    filters.problems_only = true;
  }
  if (params.get('quick_filter')) filters.quick_filter = params.get('quick_filter') || undefined;
  if (params.get('q')) filters.q = params.get('q') || undefined;
  return filters;
}

function readPagination(params: URLSearchParams): StatsPagination {
  return {
    recipients: Math.max(1, Number(params.get('rp') || 1) || 1),
    consents: Math.max(1, Number(params.get('cp') || 1) || 1),
    subscribes: Math.max(1, Number(params.get('sp') || 1) || 1),
    unsubscribes: Math.max(1, Number(params.get('up') || 1) || 1),
    problems: Math.max(1, Number(params.get('pp') || 1) || 1),
  };
}

export function buildApiParams(
  filters: StatsFilters,
  extra?: Record<string, string | number | boolean | undefined | null>,
): Record<string, string | number | boolean | undefined | null> {
  const params: Record<string, string | number | boolean | undefined | null> = {
    period_from: filters.period_from,
    period_to: filters.period_to,
    campaign: filters.campaign,
    organization: filters.organization,
    consent_status: filters.consent_status,
    manager_action: filters.manager_action,
    quick_filter: filters.quick_filter,
    q: filters.q,
    ...extra,
  };
  if (filters.providers) {
    params.providers = filters.providers;
  } else if (filters.provider) {
    params.provider = filters.provider;
  }
  if (filters.problems_only) params.problems_only = true;
  return params;
}

export function dashboardCacheKey(filters: StatsFilters): string {
  return (
    DASHBOARD_CACHE_PREFIX +
    JSON.stringify({
      period_from: filters.period_from || '',
      period_to: filters.period_to || '',
      campaign: filters.campaign || '',
      provider: filters.provider || '',
      providers: filters.providers || '',
    })
  );
}

export function readDashboardCache(filters: StatsFilters): Record<string, unknown> | null {
  try {
    const raw = sessionStorage.getItem(dashboardCacheKey(filters));
    if (!raw) return null;
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function writeDashboardCache(filters: StatsFilters, data: Record<string, unknown>) {
  try {
    sessionStorage.setItem(dashboardCacheKey(filters), JSON.stringify(data));
  } catch {
    /* ignore quota */
  }
}

export function useStatisticsState() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const tab: StatsTabKey = isStatsTabKey(tabParam) ? tabParam : 'dashboard';

  const [filters, setFiltersState] = useState<StatsFilters>(() => readFiltersFromParams(searchParams));
  const [pagination, setPaginationState] = useState<StatsPagination>(() => readPagination(searchParams));
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    setFiltersState(readFiltersFromParams(searchParams));
    setPaginationState(readPagination(searchParams));
  }, [searchParams]);

  const syncUrl = useCallback(
    (next: { tab?: StatsTabKey; filters?: StatsFilters; pagination?: StatsPagination }) => {
      const nextTab = next.tab ?? tab;
      const nextFilters = next.filters ?? filters;
      const nextPagination = next.pagination ?? pagination;
      const params = new URLSearchParams(searchParams);
      if (nextTab !== 'dashboard') params.set('tab', nextTab);
      else params.delete('tab');
      params.delete('from');
      params.delete('to');
      params.delete('period_from');
      params.delete('period_to');
      params.delete('campaign');
      params.delete('provider');
      params.delete('providers');
      params.delete('consent_status');
      params.delete('manager_action');
      params.delete('org');
      params.delete('organization');
      params.delete('problems_only');
      params.delete('quick_filter');
      params.delete('q');
      params.delete('rp');
      params.delete('cp');
      params.delete('sp');
      params.delete('up');
      params.delete('pp');
      if (nextFilters.period_from) params.set('from', nextFilters.period_from);
      if (nextFilters.period_to) params.set('to', nextFilters.period_to);
      if (nextFilters.campaign) params.set('campaign', nextFilters.campaign);
      if (nextFilters.providers) params.set('providers', nextFilters.providers);
      else if (nextFilters.provider) params.set('provider', nextFilters.provider);
      if (nextFilters.consent_status) params.set('consent_status', nextFilters.consent_status);
      if (nextFilters.manager_action) params.set('manager_action', nextFilters.manager_action);
      if (nextFilters.organization) params.set('org', nextFilters.organization);
      if (nextFilters.problems_only) params.set('problems_only', '1');
      if (nextFilters.quick_filter) params.set('quick_filter', nextFilters.quick_filter);
      if (nextFilters.q) params.set('q', nextFilters.q);
      if (nextPagination.recipients > 1) params.set('rp', String(nextPagination.recipients));
      if (nextPagination.consents > 1) params.set('cp', String(nextPagination.consents));
      if (nextPagination.subscribes > 1) params.set('sp', String(nextPagination.subscribes));
      if (nextPagination.unsubscribes > 1) params.set('up', String(nextPagination.unsubscribes));
      if (nextPagination.problems > 1) params.set('pp', String(nextPagination.problems));
      if (sameSearchParams(searchParams, params)) return;
      setSearchParams(params, { replace: false });
    },
    [filters, pagination, searchParams, setSearchParams, tab],
  );
  const setTab = useCallback(
    (nextTab: StatsTabKey, patch?: Partial<StatsFilters>) => {
      const nextFilters = { ...filters, ...patch };
      // Clear tab-specific filters when leaving recipients unless explicitly set
      if (nextTab !== 'recipients' && patch?.quick_filter === undefined) {
        delete nextFilters.quick_filter;
      }
      if (
        nextTab !== 'campaigns' &&
        nextTab !== 'recipients' &&
        nextTab !== 'marketing-consents'
      ) {
        delete nextFilters.q;
      }
      const nextPagination = { recipients: 1, consents: 1, subscribes: 1, unsubscribes: 1, problems: 1 };
      setFiltersState(nextFilters);
      setPaginationState(nextPagination);
      syncUrl({ tab: nextTab, filters: nextFilters, pagination: nextPagination });
    },
    [filters, syncUrl],
  );

  const setFilters = useCallback(
    (patch: Partial<StatsFilters>, options?: { resetPages?: boolean }) => {
      const nextFilters = { ...filters, ...patch };
      for (const key of FILTER_KEYS) {
        if (patch[key] === undefined && key in patch) delete nextFilters[key];
      }
      const nextPagination = options?.resetPages
        ? { recipients: 1, consents: 1, subscribes: 1, unsubscribes: 1, problems: 1 }
        : pagination;
      setFiltersState(nextFilters);
      if (options?.resetPages) setPaginationState(nextPagination);
      syncUrl({ filters: nextFilters, pagination: nextPagination });
    },
    [filters, pagination, syncUrl],
  );

  const clearFilters = useCallback(() => {
    const empty: StatsFilters = {};
    const nextPagination = { recipients: 1, consents: 1, subscribes: 1, unsubscribes: 1, problems: 1 };
    setFiltersState(empty);
    setPaginationState(nextPagination);
    syncUrl({ filters: empty, pagination: nextPagination });
  }, [syncUrl]);

  const setPage = useCallback(
    (key: keyof StatsPagination, page: number) => {
      const nextPagination = { ...pagination, [key]: page };
      setPaginationState(nextPagination);
      syncUrl({ pagination: nextPagination });
    },
    [pagination, syncUrl],
  );

  const requestRefresh = useCallback(() => setRefreshNonce((n) => n + 1), []);

  const apiBaseParams = useMemo(() => buildApiParams(filters), [filters]);

  return {
    tab,
    setTab,
    filters,
    setFilters,
    clearFilters,
    pagination,
    setPage,
    apiBaseParams,
    refreshNonce,
    requestRefresh,
    syncUrl,
  };
}
