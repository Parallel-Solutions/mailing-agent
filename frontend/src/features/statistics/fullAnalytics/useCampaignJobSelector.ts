import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { statisticsApi } from '@/api/statistics';
import { useStatistics } from '../StatisticsContext';
import { asRecordArray } from '../utils';

export function useCampaignJobSelector() {
  const { apiBaseParams, filters, setFilters, campaigns, setCampaigns } = useStatistics();

  const campaignsQuery = useQuery({
    queryKey: ['stats-campaigns-options', apiBaseParams],
    queryFn: () => statisticsApi.campaigns(apiBaseParams),
  });

  useEffect(() => {
    if (campaignsQuery.data) setCampaigns(asRecordArray(campaignsQuery.data.campaigns));
  }, [campaignsQuery.data, setCampaigns]);

  const jobId = filters.campaign || String(campaigns[0]?.job_id || '');
  const options = (campaigns.length ? campaigns : asRecordArray(campaignsQuery.data?.campaigns)).map(
    (item) => ({
      value: String(item.job_id),
      label: String(item.title || item.job_id),
    }),
  );

  return {
    jobId,
    options,
    setJobId: (value: string) => setFilters({ campaign: value }),
    campaignsQuery,
  };
}
