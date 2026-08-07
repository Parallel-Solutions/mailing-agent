export type StatisticsQueryParams = Record<
  string,
  string | number | boolean | undefined | null
>;

export function managerDashboardQueryKey(
  params: StatisticsQueryParams,
  refreshNonce: number,
) {
  return ['stats-dashboard', params, refreshNonce] as const;
}

export function managerDashboardParams(
  params: StatisticsQueryParams,
  refreshProviders: boolean,
): StatisticsQueryParams {
  return {
    ...params,
    refresh: refreshProviders ? true : undefined,
  };
}
