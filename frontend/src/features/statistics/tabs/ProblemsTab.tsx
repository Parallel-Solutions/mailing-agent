import { Button, Table } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { statisticsApi } from '@/api/statistics';
import { formatLocalDateTime } from '@/utils/dateTime';
import { KpiGrid } from '../components/KpiGrid';
import { ProblemsCharts } from '../components/StatsCharts';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, companyEmailsText, fmt, statusLabel } from '../utils';

export function ProblemsTab() {
  const {
    apiBaseParams,
    pagination,
    setPage,
    refreshNonce,
    openDrilldown,
    openCompanyModal,
    openActionModal,
    setError,
  } = useStatistics();

  const query = useQuery({
    queryKey: ['stats-problems', apiBaseParams, pagination.problems, refreshNonce],
    queryFn: () =>
      statisticsApi.problems({
        ...apiBaseParams,
        page: pagination.problems,
        per_page: 10,
      }),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить проблемы с email.');
  }, [query.isError, setError]);

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const items = asRecordArray(result.items);
  const pageInfo = asRecord(result.pagination);

  const kpis = [
    { title: 'Проблемные адреса', value: fmt(summary.problem_addresses), drill: 'problems_all' },
    { title: 'Постоянные ошибки', value: fmt(summary.hard_bounce), drill: 'problems_hard' },
    { title: 'Временные ошибки', value: fmt(summary.soft_bounce), drill: 'problems_soft' },
    { title: 'Требуют проверки', value: fmt(summary.need_check), drill: 'problems_hard' },
    { title: 'Повторить позже', value: fmt(summary.retry_later), drill: 'problems_soft' },
  ];

  return (
    <div>
      <KpiGrid items={kpis} loading={query.isLoading} onDrill={(key) => void openDrilldown(key)} />
      <ProblemsCharts reasons={result.reasons} domains={result.domains} />
      <Table
        style={{ marginTop: 16 }}
        loading={query.isLoading}
        rowKey={(row) => String(row.row_key)}
        dataSource={items}
        locale={{ emptyText: 'Нет проблемных компаний' }}
        pagination={{
          current: Number(pageInfo.page || pagination.problems),
          pageSize: Number(pageInfo.per_page || 10),
          total: Number(pageInfo.total || 0),
          onChange: (page) => setPage('problems', page),
        }}
        onRow={(row) => ({
          onClick: () => void openCompanyModal(String(row.row_key)),
          style: { cursor: 'pointer' },
        })}
        columns={[
          { title: 'Компания', dataIndex: 'organization' },
          { title: 'Контакты', render: (_, r) => companyEmailsText(r) },
          { title: 'Причина', dataIndex: 'bounce_reason_label' },
          { title: 'Провайдер', dataIndex: 'provider' },
          { title: 'Писем', dataIndex: 'attempts', render: (v) => fmt(v) },
          {
            title: 'Последнее событие',
            dataIndex: 'last_event_at',
            render: (value) => formatLocalDateTime(String(value || '')),
          },
          {
            title: 'Рекомендация',
            render: (_, r) => (
              <span>
                {statusLabel(r.recommended_action)}{' '}
                <Button
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    void openActionModal(String(r.row_key), 'create_task');
                  }}
                >
                  ⋯
                </Button>
              </span>
            ),
          },
        ]}
      />
    </div>
  );
}
