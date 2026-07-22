import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { statisticsApi } from '@/api/statistics';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { MODAL_PARAM_KEYS } from '@/utils/urlState';
import { DRILLDOWN_CONFIG, type DrillConfig } from './drilldownConfig';
import { buildApiParams, useStatisticsState } from './hooks/useStatisticsState';
import { asRecordArray } from './utils';
import type { StatsTabKey } from './constants';
import type { StatsFilters, StatsPagination } from './hooks/useStatisticsState';

const STATS_MODAL_KINDS = ['filters', 'export', 'action', 'drill', 'campaign', 'company'] as const;

type ModalKind =
  | null
  | 'filters'
  | 'export'
  | 'action'
  | 'drill'
  | 'campaign'
  | 'company';

type DrillState = {
  config: DrillConfig;
  kind: string;
  rows: Record<string, unknown>[];
  loading: boolean;
  truncated: boolean;
};

type StatisticsContextValue = ReturnType<typeof useStatisticsState> & {
  campaigns: Record<string, unknown>[];
  setCampaigns: (items: Record<string, unknown>[]) => void;
  reportsHistory: Record<string, unknown>[];
  setReportsHistory: (items: Record<string, unknown>[]) => void;
  modal: ModalKind;
  openFiltersModal: () => void;
  openExportModal: (exportType?: string) => void;
  openCampaignSummary: (jobId: string) => void;
  openCompanyModal: (rowKey: string) => Promise<void>;
  openActionModal: (rowKey: string, defaultType?: string) => Promise<void>;
  openDrilldown: (kind: string, override?: Partial<DrillConfig>) => Promise<void>;
  closeModal: () => void;
  companyDetail: Record<string, unknown> | null;
  actionRecipient: Record<string, unknown> | null;
  actionType: string;
  setActionType: (value: string) => void;
  campaignSummary: Record<string, unknown> | null;
  exportType: string;
  setExportType: (value: string) => void;
  drill: DrillState | null;
  error: string | null;
  setError: (message: string | null) => void;
};

const StatisticsContext = createContext<StatisticsContextValue | null>(null);

export function StatisticsProvider({ children }: { children: ReactNode }) {
  const stats = useStatisticsState();
  const { searchParams, pushParams } = useUrlNavigation();
  const modalRaw = searchParams.get('modal');
  const modal: ModalKind = STATS_MODAL_KINDS.includes(modalRaw as (typeof STATS_MODAL_KINDS)[number])
    ? (modalRaw as ModalKind)
    : null;
  const modalId = searchParams.get('modal_id');
  const exportType = searchParams.get('export_type') || 'delivery_summary';
  const actionType = searchParams.get('action_type') || 'call';
  const drillKind = searchParams.get('drill_kind') || '';

  const [campaigns, setCampaigns] = useState<Record<string, unknown>[]>([]);
  const [reportsHistory, setReportsHistory] = useState<Record<string, unknown>[]>([]);
  const [companyDetail, setCompanyDetail] = useState<Record<string, unknown> | null>(null);
  const [actionRecipient, setActionRecipient] = useState<Record<string, unknown> | null>(null);
  const [campaignSummary, setCampaignSummary] = useState<Record<string, unknown> | null>(null);
  const [drill, setDrill] = useState<DrillState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const closeModal = useCallback(() => {
    const keysToRemove = MODAL_PARAM_KEYS.filter((key) => searchParams.has(key));
    pushParams({}, keysToRemove);
  }, [pushParams, searchParams]);

  const openFiltersModal = useCallback(() => {
    pushParams({ modal: 'filters' });
  }, [pushParams]);

  const openExportModal = useCallback(
    (type?: string) => {
      pushParams({ modal: 'export', export_type: type || exportType });
    },
    [exportType, pushParams],
  );

  const openCampaignSummary = useCallback(
    (jobId: string) => {
      pushParams({ modal: 'campaign', modal_id: jobId });
    },
    [pushParams],
  );

  const openCompanyModal = useCallback(
    (rowKey: string) => {
      pushParams({ modal: 'company', modal_id: rowKey });
      return Promise.resolve();
    },
    [pushParams],
  );

  const openActionModal = useCallback(
    (rowKey: string, defaultType = 'call') => {
      pushParams({ modal: 'action', modal_id: rowKey, action_type: defaultType });
      return Promise.resolve();
    },
    [pushParams],
  );

  const loadDrilldown = useCallback(
    async (kind: string, override?: Partial<DrillConfig>) => {
      const base = DRILLDOWN_CONFIG[kind];
      if (!base && !override) return;
      const config: DrillConfig = { ...(base || { title: kind, source: 'recipients', columns: [] }), ...override };
      setDrill({ config, kind, rows: [], loading: true, truncated: false });
      try {
        const params = buildApiParams(stats.filters, config.params);
        let rows: Record<string, unknown>[] = [];
        let truncated = false;

        if (config.source === 'campaigns') {
          const result = await statisticsApi.campaigns(params);
          rows = asRecordArray(result.campaigns);
        } else if (config.source === 'reports') {
          rows = reportsHistory;
        } else if (config.source === 'consents') {
          rows = await fetchAllPages((page) =>
            statisticsApi.consents({ ...params, page, per_page: 100 }),
          );
          truncated = rows.length >= 2000;
        } else if (config.source === 'email-problems') {
          rows = await fetchAllPages((page) =>
            statisticsApi.problems({ ...params, page, per_page: 100 }),
          );
          truncated = rows.length >= 2000;
        } else {
          rows = await fetchAllPages((page) =>
            statisticsApi.recipients({ ...params, page, per_page: 100 }),
          );
          truncated = rows.length >= 2000;
        }

        if (config.filter) rows = rows.filter(config.filter);
        setDrill({ config, kind, rows, loading: false, truncated });
      } catch {
        setDrill({ config, kind, rows: [], loading: false, truncated: false });
        setError('Не удалось загрузить детализацию.');
      }
    },
    [reportsHistory, stats.filters],
  );

  const openDrilldown = useCallback(
    async (kind: string, override?: Partial<DrillConfig>) => {
      pushParams({ modal: 'drill', drill_kind: kind });
      await loadDrilldown(kind, override);
    },
    [loadDrilldown, pushParams],
  );

  const setExportType = useCallback(
    (value: string) => {
      if (modal === 'export') {
        pushParams({ export_type: value });
      }
    },
    [modal, pushParams],
  );

  const setActionType = useCallback(
    (value: string) => {
      if (modal === 'action') {
        pushParams({ action_type: value });
      }
    },
    [modal, pushParams],
  );

  useEffect(() => {
    if (modal !== 'campaign') {
      setCampaignSummary(null);
      return;
    }
    if (!modalId) return;
    const item = campaigns.find((campaign) => String(campaign.job_id) === modalId) || null;
    setCampaignSummary(item);
  }, [campaigns, modal, modalId]);

  useEffect(() => {
    if (modal !== 'company' || !modalId) {
      if (modal !== 'company') setCompanyDetail(null);
      return;
    }
    let cancelled = false;
    void statisticsApi
      .recipientDetail(modalId)
      .then((detail) => {
        if (!cancelled) {
          setCompanyDetail(detail);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCompanyDetail(null);
          setError('Карточка компании недоступна: запись не найдена среди отправок.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [modal, modalId]);

  useEffect(() => {
    if (modal !== 'action' || !modalId) {
      if (modal !== 'action') setActionRecipient(null);
      return;
    }
    let cancelled = false;
    void statisticsApi
      .recipientDetail(modalId)
      .then((detail) => {
        if (!cancelled) {
          setActionRecipient(detail);
          setError(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setActionRecipient(null);
          setError('Не удалось открыть действие: запись не найдена.');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [modal, modalId]);

  useEffect(() => {
    if (modal !== 'drill' || !drillKind) {
      if (modal !== 'drill') setDrill(null);
      return;
    }
    void loadDrilldown(drillKind);
  }, [drillKind, loadDrilldown, modal, stats.filters, reportsHistory]);

  const value = useMemo<StatisticsContextValue>(
    () => ({
      ...stats,
      campaigns,
      setCampaigns,
      reportsHistory,
      setReportsHistory,
      modal,
      openFiltersModal,
      openExportModal,
      openCampaignSummary,
      openCompanyModal,
      openActionModal,
      openDrilldown,
      closeModal,
      companyDetail,
      actionRecipient,
      actionType,
      setActionType,
      campaignSummary,
      exportType,
      setExportType,
      drill,
      error,
      setError,
    }),
    [
      stats,
      campaigns,
      reportsHistory,
      modal,
      openFiltersModal,
      openExportModal,
      openCampaignSummary,
      openCompanyModal,
      openActionModal,
      openDrilldown,
      closeModal,
      companyDetail,
      actionRecipient,
      actionType,
      campaignSummary,
      exportType,
      drill,
      error,
      setExportType,
      setActionType,
    ],
  );

  return <StatisticsContext.Provider value={value}>{children}</StatisticsContext.Provider>;
}

async function fetchAllPages(
  loader: (page: number) => Promise<Record<string, unknown>>,
): Promise<Record<string, unknown>[]> {
  const all: Record<string, unknown>[] = [];
  let page = 1;
  while (page <= 20 && all.length < 2000) {
    const result = await loader(page);
    const items = asRecordArray(result.items);
    all.push(...items);
    const pagination = result.pagination as { pages?: number } | undefined;
    const pages = Number(pagination?.pages || 1);
    if (page >= pages || !items.length) break;
    page += 1;
  }
  return all.slice(0, 2000);
}

export function useStatistics() {
  const ctx = useContext(StatisticsContext);
  if (!ctx) throw new Error('useStatistics must be used within StatisticsProvider');
  return ctx;
}

export type { StatsFilters, StatsPagination, StatsTabKey };
