import { DeleteOutlined } from '@ant-design/icons';
import { App, Button, Input, Space, Table, Tag } from 'antd';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import { statisticsApi } from '@/api/statistics';
import { RECIPIENT_CHIPS } from '../constants';
import { KpiGrid } from '../components/KpiGrid';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, companyEmailsText, companyField, fmt, statusLabel } from '../utils';

export function RecipientsTab() {
  const { message, modal } = App.useApp();
  const {
    apiBaseParams,
    filters,
    setFilters,
    pagination,
    setPage,
    refreshNonce,
    requestRefresh,
    openDrilldown,
    openCompanyModal,
    openActionModal,
    setError,
  } = useStatistics();
  const [search, setSearch] = useState(filters.q || '');

  const deleteCompany = useMutation({
    mutationFn: async ({ campaignId, recipientId }: { campaignId: string; recipientId: number }) => {
      const result = await campaignsApi.deleteRecipients(campaignId, [recipientId]);
      if (!result.deleted) throw new Error('Компания уже удалена или недоступна');
      return result;
    },
    onSuccess: () => {
      message.success('Компания удалена из рассылки и статистики');
      requestRefresh();
    },
    onError: (error: Error) => message.error(error.message),
  });

  const confirmDelete = (row: Record<string, unknown>) => {
    const campaignId = String(row.campaign_id || '');
    const recipientId = Number(row.row_id);
    if (!campaignId || !Number.isInteger(recipientId) || recipientId <= 0) {
      message.error('Не удалось определить компанию для удаления');
      return;
    }
    modal.confirm({
      title: 'Удалить компанию?',
      content: `«${String(row.organization || 'Компания')}» будет удалена из этой рассылки и статистики. Отменить действие нельзя.`,
      okText: 'Удалить',
      cancelText: 'Отмена',
      okButtonProps: { danger: true },
      onOk: () => deleteCompany.mutateAsync({ campaignId, recipientId }),
    });
  };

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
            title: 'Действия',
            key: 'actions',
            fixed: 'right',
            width: 190,
            render: (_, r) => (
              <Space size={4}>
                <Button
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    void openActionModal(String(r.row_key));
                  }}
                >
                  Действие
                </Button>
                {r.can_delete && r.campaign_id ? (
                  <Button
                    type="link"
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    loading={
                      deleteCompany.isPending &&
                      deleteCompany.variables?.campaignId === String(r.campaign_id) &&
                      deleteCompany.variables?.recipientId === Number(r.row_id)
                    }
                    onClick={(e) => {
                      e.stopPropagation();
                      confirmDelete(r);
                    }}
                  >
                    Удалить
                  </Button>
                ) : null}
              </Space>
            ),
          },
        ]}
      />
    </div>
  );
}
