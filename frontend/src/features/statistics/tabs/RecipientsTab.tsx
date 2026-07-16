import { Button, Input, Space, Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { statisticsApi } from '@/api/statistics';
import { RECIPIENT_CHIPS } from '../constants';
import { KpiGrid } from '../components/KpiGrid';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, companyEmailsText, companyField, fmt, statusLabel } from '../utils';

export function RecipientsTab() {
  const {
    apiBaseParams,
    filters,
    setFilters,
    pagination,
    setPage,
    refreshNonce,
    openDrilldown,
    openCompanyModal,
    openActionModal,
    setError,
  } = useStatistics();
  const [search, setSearch] = useState(filters.q || '');

  useEffect(() => {
    const timer = setTimeout(() => {
      if ((filters.q || '') !== search) {
        setFilters({ q: search || undefined }, { resetPages: true });
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [search, filters.q, setFilters]);

  const query = useQuery({
    queryKey: [
      'stats-recipients',
      apiBaseParams,
      filters.quick_filter,
      filters.q,
      pagination.recipients,
      refreshNonce,
    ],
    queryFn: () =>
      statisticsApi.recipients({
        ...apiBaseParams,
        quick_filter: filters.quick_filter,
        q: filters.q,
        page: pagination.recipients,
        per_page: 10,
      }),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить компании.');
  }, [query.isError, setError]);

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const items = asRecordArray(result.items);
  const pageInfo = asRecord(result.pagination);

  const kpis = [
    { title: 'Всего компаний', value: fmt(summary.total), drill: 'sent' },
    { title: 'Активные', value: fmt(summary.active), drill: 'recipients_active' },
    { title: 'Проблемные', value: fmt(summary.problematic), drill: 'problems' },
    { title: 'Нужно перезвонить', value: fmt(summary.need_call), drill: 'recipients_call' },
  ];

  return (
    <div>
      <KpiGrid items={kpis} loading={query.isLoading} onDrill={(key) => void openDrilldown(key)} />
      <Space wrap style={{ marginTop: 16 }}>
        {RECIPIENT_CHIPS.map(([value, label]) => (
          <Button
            key={label}
            type={(filters.quick_filter || '') === value ? 'primary' : 'default'}
            size="small"
            onClick={() => setFilters({ quick_filter: value || undefined }, { resetPages: true })}
          >
            {label}
          </Button>
        ))}
      </Space>
      <Input.Search
        allowClear
        placeholder="Поиск компании"
        style={{ marginTop: 12, maxWidth: 360 }}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <Table
        style={{ marginTop: 16 }}
        loading={query.isLoading}
        rowKey={(row) => String(row.row_key)}
        dataSource={items}
        locale={{ emptyText: 'Нет компаний за выбранный период' }}
        pagination={{
          current: Number(pageInfo.page || pagination.recipients),
          pageSize: Number(pageInfo.per_page || 10),
          total: Number(pageInfo.total || 0),
          onChange: (page) => setPage('recipients', page),
          showTotal: (total, range) => `Показано ${range[0]}–${range[1]} из ${total}`,
        }}
        onRow={(row) => ({
          onClick: () => void openCompanyModal(String(row.row_key)),
          style: { cursor: 'pointer' },
        })}
        columns={[
          { title: 'Компания', dataIndex: 'organization' },
          { title: 'Регион', render: (_, r) => companyField(r, 'region') },
          { title: 'ИНН', render: (_, r) => companyField(r, 'inn') },
          { title: 'Контакты', render: (_, r) => companyEmailsText(r) },
          {
            title: 'Статус',
            render: (_, r) => <Tag>{statusLabel(r.manager_status)}</Tag>,
          },
          { title: 'Интерес', render: (_, r) => statusLabel(r.interest) },
          { title: 'Следующее действие', render: (_, r) => statusLabel(r.next_action) },
          {
            title: '',
            key: 'action',
            render: (_, r) => (
              <Button
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  void openActionModal(String(r.row_key));
                }}
              >
                ⋯
              </Button>
            ),
          },
        ]}
      />
    </div>
  );
}
