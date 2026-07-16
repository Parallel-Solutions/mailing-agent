import { Button, Card, Col, Input, List, Row, Table } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { FunnelRow } from '../components/FunnelRow';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt, statusLabel } from '../utils';

export function ConsentsTab() {
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
    queryKey: ['stats-consents', apiBaseParams, filters.q, pagination.consents, refreshNonce],
    queryFn: () =>
      statisticsApi.consents({
        ...apiBaseParams,
        q: filters.q,
        page: pagination.consents,
        per_page: 10,
      }),
  });

  useEffect(() => {
    if (query.isError) setError('Не удалось загрузить согласия.');
  }, [query.isError, setError]);

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const items = asRecordArray(result.items);
  const pageInfo = asRecord(result.pagination);
  const priority = asRecordArray(result.priority_contacts);

  const kpis = [
    { title: 'Дали согласие', value: fmt(summary.confirmed), drill: 'consents_confirmed' },
    { title: 'Материалы отправлены', value: fmt(summary.materials_sent), drill: 'materials' },
    {
      title: 'Открыли после согласия',
      value: fmt(summary.opened_after_consent),
      drill: 'consents_opened',
    },
    { title: 'Нужно перезвонить', value: fmt(summary.need_call), drill: 'consents_call' },
  ];

  return (
    <div>
      <KpiGrid items={kpis} loading={query.isLoading} onDrill={(key) => void openDrilldown(key)} />
      <FunnelRow funnel={result.funnel} title="Воронка согласий" />
      <Input.Search
        allowClear
        placeholder="Поиск"
        style={{ marginTop: 16, maxWidth: 360 }}
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={16}>
          <Table
            loading={query.isLoading}
            rowKey={(row, index) => String(row.row_key || index)}
            dataSource={items}
            locale={{ emptyText: 'Нет данных по согласиям' }}
            pagination={{
              current: Number(pageInfo.page || pagination.consents),
              pageSize: Number(pageInfo.per_page || 10),
              total: Number(pageInfo.total || 0),
              onChange: (page) => setPage('consents', page),
            }}
            onRow={(row) => ({
              onClick: () => {
                if (row.row_key) void openCompanyModal(String(row.row_key));
              },
              style: row.row_key ? { cursor: 'pointer' } : undefined,
            })}
            columns={[
              { title: 'Компания', dataIndex: 'organization' },
              { title: 'Контакт', dataIndex: 'contact' },
              { title: 'Email', dataIndex: 'email' },
              { title: 'Статус согласия', dataIndex: 'consent_status_label' },
              { title: 'Материалы', dataIndex: 'materials_label' },
              {
                title: 'Последнее действие',
                render: (_, r) => (
                  <span>
                    {String(r.last_action_label || '—')}
                    <div>{String(r.last_action_at || '')}</div>
                  </span>
                ),
              },
              { title: 'Интерес', render: (_, r) => statusLabel(r.interest) },
              {
                title: '',
                key: 'action',
                render: (_, r) =>
                  r.row_key ? (
                    <Button
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        void openActionModal(String(r.row_key));
                      }}
                    >
                      ⋯
                    </Button>
                  ) : (
                    statusLabel(r.next_action)
                  ),
              },
            ]}
          />
        </Col>
        <Col xs={24} lg={8}>
          <Card title="Приоритетные контакты" size="small">
            <List
              size="small"
              dataSource={priority}
              locale={{ emptyText: 'Нет приоритетных контактов' }}
              renderItem={(item, index) => (
                <List.Item>
                  <span>
                    {index + 1}. {String(item.organization || '—')}
                  </span>
                  <span>{String(item.contact || '')}</span>
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
