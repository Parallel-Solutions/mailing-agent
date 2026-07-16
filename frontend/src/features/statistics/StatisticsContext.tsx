import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { statisticsApi } from '@/api/statistics';
import { DRILLDOWN_CONFIG, type DrillConfig } from './drilldownConfig';
import { buildApiParams, useStatisticsState } from './hooks/useStatisticsState';
import { asRecordArray } from './utils';
import type { StatsTabKey } from './constants';
import type { StatsFilters, StatsPagination } from './hooks/useStatisticsState';

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
  const [campaigns, setCampaigns] = useState<Record<string, unknown>[]>([]);
  const [reportsHistory, setReportsHistory] = useState<Record<string, unknown>[]>([]);
  const [modal, setModal] = useState<ModalKind>(null);
  const [companyDetail, setCompanyDetail] = useState<Record<string, unknown> | null>(null);
  const [actionRecipient, setActionRecipient] = useState<Record<string, unknown> | null>(null);
  const [actionType, setActionType] = useState('call');
  const [campaignSummary, setCampaignSummary] = useState<Record<string, unknown> | null>(null);
  const [exportType, setExportType] = useState('delivery_summary');
  const [drill, setDrill] = useState<DrillState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const closeModal = useCallback(() => {
    setModal(null);
  }, []);

  const openFiltersModal = useCallback(() => setModal('filters'), []);
  const openExportModal = useCallback((type?: string) => {
    if (type) setExportType(type);
    setModal('export');
  }, []);

  const openCampaignSummary = useCallback(
    (jobId: string) => {
      const item = campaigns.find((campaign) => String(campaign.job_id) === jobId) || null;
      setCampaignSummary(item);
      setModal('campaign');
    },
    [campaigns],
  );

  const openCompanyModal = useCallback(async (rowKey: string) => {
    try {
      const detail = await statisticsApi.recipientDetail(rowKey);
      setCompanyDetail(detail);
      setModal('company');
      setError(null);
    } catch {
      setError('Карточка компании недоступна: запись не найдена среди отправок.');
    }
  }, []);

  const openActionModal = useCallback(async (rowKey: string, defaultType = 'call') => {
    try {
      const detail = await statisticsApi.recipientDetail(rowKey);
      setActionRecipient(detail);
      setActionType(defaultType);
      setModal('action');
      setError(null);
    } catch {
      setError('Не удалось открыть действие: запись не найдена.');
    }
  }, []);

  const openDrilldown = useCallback(
    async (kind: string, override?: Partial<DrillConfig>) => {
      const base = DRILLDOWN_CONFIG[kind];
      if (!base && !override) return;
      const config: DrillConfig = { ...(base || { title: kind, source: 'recipients', columns: [] }), ...override };
      setDrill({ config, kind, rows: [], loading: true, truncated: false });
      setModal('drill');
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
