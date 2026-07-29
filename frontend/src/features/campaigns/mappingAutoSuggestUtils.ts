import type { QueryClient } from '@tanstack/react-query';
import type { VariableMappingSuggestResult } from '@/api/campaigns';
import { campaignsApi } from '@/api/campaigns';
import {
  campaignVariableMappingQueryKey,
  campaignVariableMappingSuggestQueryKey,
} from '@/features/campaigns/campaignQueryUtils';

export function isMappingAutoSavable(suggest: VariableMappingSuggestResult): boolean {
  if (suggest.status === 'complete') {
    return true;
  }
  const systemKeys = new Set(Object.keys(suggest.system_variables || {}));
  const recipientVars = (suggest.template_variables || []).filter((item) => !systemKeys.has(item.name));
  return recipientVars.every((item) => Boolean(suggest.suggested_mapping?.[item.name]?.trim()));
}

export async function fetchVariableMappingSuggest(
  queryClient: QueryClient,
  campaignId: string,
  signature: string,
): Promise<VariableMappingSuggestResult> {
  return queryClient.fetchQuery({
    queryKey: campaignVariableMappingSuggestQueryKey(campaignId, signature),
    queryFn: () => campaignsApi.suggestVariableMapping(campaignId),
    staleTime: Infinity,
  });
}

export async function runMappingAutoSuggest(
  campaignId: string,
  queryClient: QueryClient,
  signature: string,
): Promise<boolean> {
  const state = await queryClient.fetchQuery({
    queryKey: campaignVariableMappingQueryKey(campaignId),
    queryFn: () => campaignsApi.getVariableMapping(campaignId),
    staleTime: Infinity,
  });
  if (state.mapping_confirmed) {
    return true;
  }

  const suggest = await fetchVariableMappingSuggest(queryClient, campaignId, signature);
  if (!isMappingAutoSavable(suggest)) {
    return false;
  }
  await campaignsApi.saveVariableMapping(campaignId, suggest.suggested_mapping || {});
  void queryClient.removeQueries({ queryKey: campaignVariableMappingQueryKey(campaignId) });
  return true;
}
