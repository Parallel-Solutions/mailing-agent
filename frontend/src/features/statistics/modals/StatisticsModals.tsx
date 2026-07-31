import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  DatePicker,
  Descriptions,
  Drawer,
  Form,
  Input,
  List,
  Modal,
  Pagination,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { campaignsApi } from '@/api/campaigns';
import { previewApi } from '@/api/preview';
import { statisticsApi } from '@/api/statistics';
import { buildEmailPreviewDocument } from '@/features/templates/emailTemplateUtils';
import { useAuthStore } from '@/stores/authStore';
import { formatLocalDateTime } from '@/utils/dateTime';
import { ACTION_TYPES, EXPORT_TYPES, PROVIDER_OPTIONS } from '../constants';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, downloadCsv, fmt, statusLabel } from '../utils';

export function StatisticsModals() {
  return (
    <>
      <AdvancedFiltersModal />
      <ExportReportModal />
      <ManagerActionModal />
      <DrilldownModal />
      <CampaignSummaryModal />
      <CompanyDetailModal />
    </>
  );
}

function AdvancedFiltersModal() {
  const { modal, closeModal, filters, setFilters, clearFilters, campaigns } = useStatistics();
  const [form] = Form.useForm();

  useEffect(() => {
    if (modal !== 'filters') return;
    form.setFieldsValue({
      period:
        filters.period_from || filters.period_to
          ? [
              filters.period_from ? dayjs(filters.period_from) : null,
              filters.period_to ? dayjs(filters.period_to) : null,
            ]
          : null,
      providers: filters.providers ? filters.providers.split(',').filter(Boolean) : [],
      campaign: filters.campaign || undefined,
      consent_status: filters.consent_status || undefined,
      manager_action: filters.manager_action || undefined,
      organization: filters.organization || undefined,
      problems_only: !!filters.problems_only,
    });
  }, [modal, filters, form]);

  return (
    <Modal
      title="Расширенные фильтры"
      open={modal === 'filters'}
      onCancel={closeModal}
      footer={[
        <Button
          key="reset"
          onClick={() => {
            clearFilters();
            closeModal();
          }}
        >
          Сбросить
        </Button>,
        <Button key="cancel" onClick={closeModal}>
          Закрыть
        </Button>,
        <Button key="ok" type="primary" onClick={() => form.submit()}>
          Применить
        </Button>,
      ]}
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => {
          const period = values.period as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null;
          setFilters(
            {
              period_from: period?.[0]?.format('YYYY-MM-DD'),
              period_to: period?.[1]?.format('YYYY-MM-DD'),
              providers: (values.providers as string[] | undefined)?.join(',') || undefined,
              provider: undefined,
              campaign: values.campaign || undefined,
              consent_status: values.consent_status || undefined,
              manager_action: values.manager_action || undefined,
              organization: values.organization || undefined,
              problems_only: !!values.problems_only,
            },
            { resetPages: true },
          );
          closeModal();
        }}
      >
        <Form.Item name="period" label="Период">
          <DatePicker.RangePicker style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="providers" label="Провайдеры">
          <Select
            mode="multiple"
            allowClear
            options={PROVIDER_OPTIONS.filter((item) => item.value).map((item) => ({
              value: item.value,
              label: item.label,
            }))}
          />
        </Form.Item>
        <Form.Item name="campaign" label="Рассылка">
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            options={campaigns.map((item) => ({
              value: String(item.job_id),
              label: String(item.title || 'Рассылка без названия'),
            }))}
          />
        </Form.Item>
        <Form.Item name="consent_status" label="Статус согласия">
          <Select
            allowClear
            options={[
              { value: 'confirmed', label: 'Подтверждено' },
              { value: 'pending', label: 'Ожидает' },
              { value: 'declined', label: 'Отклонено' },
            ]}
          />
        </Form.Item>
        <Form.Item name="manager_action" label="Действие менеджера">
          <Select
            allowClear
            options={ACTION_TYPES.map(([value, label]) => ({ value, label }))}
          />
        </Form.Item>
        <Form.Item name="organization" label="Организация">
          <Input allowClear />
        </Form.Item>
        <Form.Item name="problems_only" valuePropName="checked">
          <Checkbox>Только проблемные</Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
}

function ExportReportModal() {
  const {
    modal,
    closeModal,
    filters,
    campaigns,
    exportType,
    setExportType,
    requestRefresh,
    setError,
  } = useStatistics();
  const [fmt, setFmt] = useState('xlsx');
  const [jobId, setJobId] = useState<string>();
  const [period, setPeriod] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (modal !== 'export') return;
    setJobId(filters.campaign || undefined);
    setPeriod([
      filters.period_from ? dayjs(filters.period_from) : null,
      filters.period_to ? dayjs(filters.period_to) : null,
    ]);
    setFmt(exportType === 'auto_call_contacts' ? 'csv' : 'xlsx');
  }, [modal, filters, exportType]);

  return (
    <Modal
      title="Экспорт отчёта"
      open={modal === 'export'}
      onCancel={closeModal}
      confirmLoading={saving}
      onOk={async () => {
        setSaving(true);
        try {
          const result = await statisticsApi.exportReport({
            report_type: exportType,
            period_from: period?.[0]?.format('YYYY-MM-DD'),
            period_to: period?.[1]?.format('YYYY-MM-DD'),
            job_id: jobId,
            fmt: exportType === 'auto_call_contacts' ? 'csv' : fmt,
          });
          const reportId = String(result.report_id || '');
          if (reportId) {
            window.location.href = statisticsApi.reportDownloadUrl(reportId);
          }
          requestRefresh();
          closeModal();
        } catch {
          setError('Не удалось сформировать отчёт.');
        } finally {
          setSaving(false);
        }
      }}
      okText="Сформировать"
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Typography.Text type="secondary">Тип отчёта</Typography.Text>
          <Select
            style={{ width: '100%', marginTop: 4 }}
            value={exportType}
            onChange={(value) => {
              setExportType(value);
              if (value === 'auto_call_contacts') setFmt('csv');
            }}
            options={EXPORT_TYPES}
          />
        </div>
        <div>
          <Typography.Text type="secondary">Период</Typography.Text>
          <DatePicker.RangePicker
            style={{ width: '100%', marginTop: 4 }}
            value={period}
            onChange={(value) => setPeriod(value)}
          />
        </div>
        <div>
          <Typography.Text type="secondary">Рассылка</Typography.Text>
          <Select
            allowClear
            style={{ width: '100%', marginTop: 4 }}
            value={jobId}
            onChange={setJobId}
            options={campaigns.map((item) => ({
              value: String(item.job_id),
              label: String(item.title || 'Рассылка без названия'),
            }))}
            placeholder="Текущая / первая доступная"
          />
        </div>
        <div>
          <Typography.Text type="secondary">Формат</Typography.Text>
          <Select
            style={{ width: '100%', marginTop: 4 }}
            value={fmt}
            disabled={exportType === 'auto_call_contacts'}
            onChange={setFmt}
            options={[
              { value: 'xlsx', label: 'Excel (xlsx)' },
              { value: 'csv', label: 'CSV' },
              { value: 'ndjson', label: 'NDJSON' },
            ]}
          />
        </div>
      </Space>
    </Modal>
  );
}

function ManagerActionModal() {
  const {
    modal,
    closeModal,
    actionRecipient,
    actionType,
    setActionType,
    requestRefresh,
    openCompanyModal,
    setError,
  } = useStatistics();
  const user = useAuthStore((s) => s.user);
  const [manager, setManager] = useState('');
  const [dueAt, setDueAt] = useState<dayjs.Dayjs | null>(null);
  const [comment, setComment] = useState('');
  const [priority, setPriority] = useState('normal');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (modal !== 'action') return;
    setManager(user?.username || '');
    setDueAt(null);
    setComment('');
    setPriority('normal');
  }, [modal, user]);

  return (
    <Modal
      title="Действие по компании"
      open={modal === 'action'}
      onCancel={closeModal}
      confirmLoading={saving}
      onOk={async () => {
        if (!actionRecipient?.row_key) return;
        setSaving(true);
        try {
          await statisticsApi.saveRecipientAction(String(actionRecipient.row_key), {
            action_type: actionType,
            responsible_manager: manager,
            due_at: dueAt?.toISOString(),
            comment,
            priority,
          });
          const rowKey = String(actionRecipient.row_key);
          requestRefresh();
          closeModal();
          await openCompanyModal(rowKey);
        } catch {
          setError('Не удалось сохранить действие.');
        } finally {
          setSaving(false);
        }
      }}
      okText="Сохранить"
    >
      <Typography.Paragraph>
        {String(actionRecipient?.organization || 'Компания')}
      </Typography.Paragraph>
      <Radio.Group
        value={actionType}
        onChange={(e) => setActionType(e.target.value)}
        style={{ marginBottom: 16, display: 'flex', flexWrap: 'wrap', gap: 8 }}
      >
        {ACTION_TYPES.map(([value, label]) => (
          <Radio.Button key={value} value={value}>
            {label}
          </Radio.Button>
        ))}
      </Radio.Group>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Input
          placeholder="Ответственный менеджер"
          value={manager}
          onChange={(e) => setManager(e.target.value)}
        />
        <DatePicker
          showTime
          style={{ width: '100%' }}
          placeholder="Срок"
          value={dueAt}
          onChange={setDueAt}
        />
        <Select
          value={priority}
          onChange={setPriority}
          options={[
            { value: 'low', label: 'Низкий' },
            { value: 'normal', label: 'Обычный' },
            { value: 'high', label: 'Высокий' },
          ]}
        />
        <Input.TextArea
          rows={3}
          placeholder="Комментарий"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </Space>
    </Modal>
  );
}

function DrilldownModal() {
  const { modal, closeModal, drill, openCompanyModal } = useStatistics();
  const { message, modal: appModal } = App.useApp();
  const [page, setPage] = useState(1);
  const [resendingKey, setResendingKey] = useState('');
  const [queuedResends, setQueuedResends] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (modal === 'drill') setPage(1);
  }, [modal, drill?.kind]);

  const columns =
    drill?.config.columns.map(([title, getter], index) => ({
      title,
      key: String(index),
      onCell: () => ({ style: { verticalAlign: 'top' as const } }),
      render: (_: unknown, row: Record<string, unknown>) => String(getter(row) ?? '—'),
    })) || [];
  const tableWidth = Math.max(960, columns.length * 150);
  const usesCardLayout = drill?.config.layout === 'cards';
  const usesErrorCardLayout = drill?.config.layout === 'error-cards';

  const confirmResend = (row: Record<string, unknown>) => {
    const rowKey = String(row.row_key || '');
    if (!rowKey || resendingKey || queuedResends.has(rowKey)) return;
    const organization = String(row.organization || row.email || 'получателю');
    appModal.confirm({
      title: 'Направить письмо повторно?',
      content: `Для «${organization}» сервер ещё раз проверит статус, стоп-лист и доступный email. Отправка выполнится в фоне.`,
      okText: 'Поставить в очередь',
      cancelText: 'Отмена',
      onOk: async () => {
        setResendingKey(rowKey);
        try {
          const result = await statisticsApi.resendRecipient(rowKey);
          setQueuedResends((current) => new Set(current).add(rowKey));
          const target = result.target_email ? ` Адрес: ${result.target_email}.` : '';
          message.success(`${result.reason || 'Повторная отправка поставлена в очередь.'}${target}`);
        } catch (error) {
          message.error(error instanceof Error ? error.message : 'Не удалось поставить письмо в очередь.');
          throw error;
        } finally {
          setResendingKey('');
        }
      },
    });
  };

  return (
    <Modal
      title={drill?.config.title || 'Детализация'}
      open={modal === 'drill'}
      onCancel={closeModal}
      width="calc(100vw - 32px)"
      style={{ top: 16, maxWidth: 1360, paddingBottom: 0 }}
      styles={{
        body: {
          maxHeight: 'calc(100vh - 176px)',
          overflowX: 'hidden',
          overflowY: 'auto',
        },
      }}
      footer={[
        <Button key="close" onClick={closeModal}>
          Закрыть
        </Button>,
        <Button
          key="csv"
          type="primary"
          disabled={!drill?.rows.length}
          onClick={() => {
            if (!drill) return;
            const headers = drill.config.columns.map(([title]) => title);
            const rows = drill.rows.map((row) =>
              drill.config.columns.map(([, getter]) => String(getter(row) ?? '')),
            );
            downloadCsv(`${drill.kind || 'drilldown'}.csv`, headers, rows);
          }}
        >
          Скачать таблицу
        </Button>,
      ]}
    >
      {drill?.truncated ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="Показаны первые 2000 строк"
        />
      ) : null}
      {usesCardLayout ? (
        <ConsentDrilldownCards
          loading={drill?.loading}
          rows={drill?.rows || []}
          page={page}
          onPageChange={setPage}
          onOpen={(row) => {
            if (row.row_key) void openCompanyModal(String(row.row_key));
          }}
        />
      ) : usesErrorCardLayout ? (
        <ProblemDrilldownCards
          loading={drill?.loading}
          rows={drill?.rows || []}
          page={page}
          onPageChange={setPage}
          onOpen={(row) => {
            if (row.row_key) void openCompanyModal(String(row.row_key));
          }}
          onResend={confirmResend}
          resendingKey={resendingKey}
          queuedResends={queuedResends}
        />
      ) : (
        <Table
          size="small"
          loading={drill?.loading}
          rowKey={(row, index) => String(row.row_key || row.job_id || index)}
          dataSource={drill?.rows || []}
          columns={columns}
          tableLayout="fixed"
          sticky
          scroll={{ x: tableWidth, y: 'calc(100vh - 320px)' }}
          pagination={{
            current: page,
            pageSize: 20,
            showSizeChanger: false,
            showTotal: (total) => `Всего: ${total}`,
            onChange: setPage,
          }}
          onRow={(row) => ({
            onClick: () => {
              if (row.row_key) void openCompanyModal(String(row.row_key));
            },
            style: row.row_key ? { cursor: 'pointer' } : undefined,
          })}
        />
      )}
    </Modal>
  );
}

type ProblemDrilldownCardsProps = {
  loading?: boolean;
  rows: Record<string, unknown>[];
  page: number;
  onPageChange: (page: number) => void;
  onOpen: (row: Record<string, unknown>) => void;
  onResend: (row: Record<string, unknown>) => void;
  resendingKey: string;
  queuedResends: Set<string>;
};

export function ProblemDrilldownCards({
  loading,
  rows,
  page,
  onPageChange,
  onOpen,
  onResend,
  resendingKey,
  queuedResends,
}: ProblemDrilldownCardsProps) {
  const pageSize = 12;
  const pageRows = rows.slice((page - 1) * pageSize, page * pageSize);

  return (
    <>
      <List
        loading={loading}
        locale={{ emptyText: 'Ошибок доставки нет' }}
        grid={{ gutter: 12, xs: 1, sm: 1, md: 2, lg: 2, xl: 2, xxl: 3 }}
        dataSource={pageRows}
        rowKey={(row) => String(row.row_key || row.email || JSON.stringify(row))}
        renderItem={(row) => {
          const rowKey = String(row.row_key || '');
          const organization = String(row.organization || 'Без названия');
          const status = row.manager_status as { key?: string; label?: string } | undefined;
          const statusKey = String(status?.key || '');
          const statusLabel = String(status?.label || 'Ошибка доставки');
          const emails = Array.isArray(row.emails)
            ? row.emails
                .map((item) => String((item as { email?: string }).email || '').trim())
                .filter(Boolean)
            : [String(row.email || '').trim()].filter(Boolean);
          const lastEventAt = formatLocalDateTime(String(row.last_event_at || ''));
          const reason = String(
            row.bounce_reason_label ||
              (row.next_action as { label?: string } | undefined)?.label ||
              'Причина уточняется у почтового провайдера',
          );
          const queued = queuedResends.has(rowKey);
          const isAutomaticRetry = statusKey === 'soft_bounce';
          const canRequest = Boolean(rowKey) && !isAutomaticRetry && !queued;

          return (
            <List.Item style={{ height: '100%' }}>
              <Card
                size="small"
                hoverable={Boolean(rowKey)}
                onClick={rowKey ? () => onOpen(row) : undefined}
                style={{ height: '100%', borderColor: '#f0d5d5' }}
                styles={{ body: { padding: 16, height: '100%' } }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    justifyContent: 'space-between',
                    gap: 12,
                    marginBottom: 14,
                  }}
                >
                  <Typography.Text
                    strong
                    ellipsis={{ tooltip: organization }}
                    style={{ minWidth: 0, display: 'block', fontSize: 15 }}
                  >
                    {organization}
                  </Typography.Text>
                  <Tag color={statusKey === 'soft_bounce' ? 'gold' : 'red'} style={{ margin: 0 }}>
                    {statusLabel}
                  </Tag>
                </div>

                <Descriptions
                  size="small"
                  column={1}
                  colon={false}
                  styles={{ label: { width: 124, color: '#667085' } }}
                  items={[
                    {
                      key: 'emails',
                      label: 'Email',
                      children: emails.length ? emails.join(', ') : '—',
                    },
                    {
                      key: 'reason',
                      label: 'Что произошло',
                      children: reason,
                    },
                    {
                      key: 'last-event',
                      label: 'Последнее событие',
                      children: lastEventAt,
                    },
                  ]}
                />

                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 12,
                    marginTop: 14,
                    paddingTop: 12,
                    borderTop: '1px solid #f2e7e7',
                  }}
                >
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {isAutomaticRetry
                      ? 'Повтор выполняется автоматически'
                      : queued
                        ? 'Письмо поставлено в очередь'
                        : 'Перед отправкой адрес проверится ещё раз'}
                  </Typography.Text>
                  <Button
                    type="primary"
                    danger
                    disabled={!canRequest}
                    loading={resendingKey === rowKey}
                    onClick={(event) => {
                      event.stopPropagation();
                      onResend(row);
                    }}
                  >
                    {queued ? 'В очереди' : isAutomaticRetry ? 'Автоповтор' : 'Направить повторно'}
                  </Button>
                </div>
              </Card>
            </List.Item>
          );
        }}
      />
      {rows.length > pageSize ? (
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 4 }}>
          <Pagination
            current={page}
            pageSize={pageSize}
            total={rows.length}
            showSizeChanger={false}
            showTotal={(total) => `Всего: ${total}`}
            onChange={onPageChange}
          />
        </div>
      ) : null}
    </>
  );
}
type ConsentDrilldownCardsProps = {
  loading?: boolean;
  rows: Record<string, unknown>[];
  page: number;
  onPageChange: (page: number) => void;
  onOpen: (row: Record<string, unknown>) => void;
};

export function ConsentDrilldownCards({
  loading,
  rows,
  page,
  onPageChange,
  onOpen,
}: ConsentDrilldownCardsProps) {
  const pageSize = 12;
  const pageRows = rows.slice((page - 1) * pageSize, page * pageSize);

  return (
    <>
      <List
        loading={loading}
        locale={{ emptyText: 'Нет данных по согласиям' }}
        grid={{ gutter: 12, xs: 1, sm: 1, md: 2, lg: 2, xl: 2, xxl: 3 }}
        dataSource={pageRows}
        rowKey={(row) => String(row.row_key || row.email || row.contact || JSON.stringify(row))}
        renderItem={(row) => {
          const organization = String(row.organization || '').trim();
          const contact = String(row.contact || '').trim();
          const email = String(row.email || '').trim();
          const title = organization || contact || email || 'Без названия';
          const consentStatus = String(row.consent_status_label || 'Статус не указан');
          const materials = String(row.materials_label || 'Нет данных');
          const interest = String(
            (row.interest as { label?: string } | undefined)?.label || 'Не указан',
          );
          const nextAction = String(
            (row.next_action as { label?: string } | undefined)?.label || 'Не запланировано',
          );
          const lastAction = String(row.last_action_label || 'Действий ещё не было');
          const lastActionAt = formatLocalDateTime(String(row.last_action_at || ''));
          const isClickable = Boolean(row.row_key);

          return (
            <List.Item style={{ height: '100%' }}>
              <Card
                size="small"
                hoverable={isClickable}
                onClick={isClickable ? () => onOpen(row) : undefined}
                onKeyDown={
                  isClickable
                    ? (event) => {
                        if (event.key === 'Enter' || event.key === ' ') onOpen(row);
                      }
                    : undefined
                }
                tabIndex={isClickable ? 0 : undefined}
                style={{ height: '100%', borderColor: '#dfe6e2' }}
                styles={{ body: { padding: 16 } }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    justifyContent: 'space-between',
                    gap: 12,
                    marginBottom: 14,
                  }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <Typography.Text
                      strong
                      ellipsis={{ tooltip: title }}
                      style={{ display: 'block', fontSize: 15 }}
                    >
                      {title}
                    </Typography.Text>
                    {contact && contact !== title ? (
                      <Typography.Text
                        type="secondary"
                        ellipsis={{ tooltip: contact }}
                        style={{ display: 'block', marginTop: 2 }}
                      >
                        {contact}
                      </Typography.Text>
                    ) : null}
                  </div>
                  <Tag
                    color={drillStatusColor(consentStatus)}
                    style={{ margin: 0, whiteSpace: 'normal', textAlign: 'center' }}
                  >
                    {consentStatus}
                  </Tag>
                </div>

                <Descriptions
                  size="small"
                  column={1}
                  colon={false}
                  styles={{ label: { width: 130, color: '#667085' } }}
                  items={[
                    {
                      key: 'email',
                      label: 'Email',
                      children: (
                        <Typography.Text
                          ellipsis={{ tooltip: email || '—' }}
                          style={{ display: 'block', maxWidth: 280 }}
                        >
                          {email || '—'}
                        </Typography.Text>
                      ),
                    },
                    {
                      key: 'last-action',
                      label: 'Последнее действие',
                      children: (
                        <div>
                          <div>{lastAction}</div>
                          {lastActionAt !== '—' ? (
                            <Typography.Text type="secondary">{lastActionAt}</Typography.Text>
                          ) : null}
                        </div>
                      ),
                    },
                    {
                      key: 'next-action',
                      label: 'Следующее действие',
                      children: nextAction,
                    },
                  ]}
                />

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
                    gap: 8,
                    marginTop: 14,
                    paddingTop: 12,
                    borderTop: '1px solid #edf0ee',
                  }}
                >
                  <ConsentStatusField label="Материалы" value={materials} />
                  <ConsentStatusField label="Интерес" value={interest} />
                </div>
              </Card>
            </List.Item>
          );
        }}
      />
      {rows.length > pageSize ? (
        <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 4 }}>
          <Pagination
            current={page}
            pageSize={pageSize}
            total={rows.length}
            showSizeChanger={false}
            showTotal={(total) => `Всего: ${total}`}
            onChange={onPageChange}
          />
        </div>
      ) : null}
    </>
  );
}

function ConsentStatusField({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ minWidth: 0 }}>
      <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
        {label}
      </Typography.Text>
      <Tag
        color={drillStatusColor(value)}
        style={{
          margin: '4px 0 0',
          maxWidth: '100%',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {value}
      </Tag>
    </div>
  );
}

function drillStatusColor(value: string) {
  const normalized = value.toLocaleLowerCase('ru');
  if (/ошиб|отказ|спам|заблок|не достав/.test(normalized)) return 'red';
  if (/ожида|ещё не|еще не|не отправ|запрос согласия отправлен|низк/.test(normalized)) {
    return 'gold';
  }
  if (/согласие получено|подтвержд|материалы отправлены|открыт|высок/.test(normalized)) {
    return 'green';
  }
  return 'blue';
}

function CampaignSummaryModal() {
  const { modal, closeModal, campaignSummary, setTab, setFilters } = useStatistics();
  const item = campaignSummary;
  const jobId = String(item?.job_id || '');

  return (
    <Modal
      title={String(item?.title || 'Сводка по рассылке')}
      open={modal === 'campaign'}
      onCancel={closeModal}
      footer={[
        <Button key="close" onClick={closeModal}>
          Закрыть
        </Button>,
        <Button
          key="report"
          onClick={() => {
            if (jobId) window.location.href = statisticsApi.deliveryReportUrl(jobId);
          }}
        >
          Скачать отчёт
        </Button>,
        <Button
          key="autocall"
          onClick={() => {
            if (jobId) window.location.href = statisticsApi.autoCallContactsUrl(jobId);
          }}
        >
          Выгрузить для обзвона
        </Button>,
        <Button
          key="analytics"
          type="primary"
          onClick={() => {
            closeModal();
            setFilters({ campaign: jobId });
            setTab('campaign-analytics', { campaign: jobId });
          }}
        >
          Открыть аналитику
        </Button>,
      ]}
    >
      <Descriptions column={1} size="small">
        <Descriptions.Item label="Период">{String(item?.period_label || '—')}</Descriptions.Item>
        <Descriptions.Item label="Доставляемость">{fmt(item?.delivery_rate)}%</Descriptions.Item>
        <Descriptions.Item label="Открываемость">{fmt(item?.open_rate)}%</Descriptions.Item>
        <Descriptions.Item label="Доля переходов">{fmt(item?.ctr)}%</Descriptions.Item>
      </Descriptions>
    </Modal>
  );
}

function CompanyDetailModal() {
  const { modal, closeModal, companyDetail, openActionModal } = useStatistics();
  const detail = companyDetail;
  const fields = asRecord(asRecord(detail?.company).fields);
  const emails = asRecordArray(detail?.emails);
  const attempts = asRecordArray(detail?.attempts);
  const acceptedEmails = asRecordArray(detail?.sent_emails);
  const documents = asRecordArray(detail?.documents);
  const statusHistory = asRecordArray(detail?.status_history);
  const consents = asRecordArray(detail?.consents);
  const actions = asRecordArray(detail?.action_history);
  const summary = asRecord(detail?.summary);
  const [emailPreview, setEmailPreview] = useState<{
    campaignId: string;
    recipientId: number;
  } | null>(null);
  const [emailPreviewIndex, setEmailPreviewIndex] = useState(0);
  const [documentPreview, setDocumentPreview] = useState<{
    jobId: string;
    path: string;
  } | null>(null);

  useEffect(() => {
    if (modal !== 'company') {
      setEmailPreview(null);
      setEmailPreviewIndex(0);
      setDocumentPreview(null);
    }
  }, [modal]);

  const emailPreviewQuery = useQuery({
    queryKey: [
      'statistics-company-sent-email-preview',
      emailPreview?.campaignId,
      emailPreview?.recipientId,
    ],
    enabled: Boolean(emailPreview),
    queryFn: () =>
      campaignsApi.sentEmailPreview(
        String(emailPreview?.campaignId),
        Number(emailPreview?.recipientId),
      ),
  });
  const previewItems = asRecordArray(emailPreviewQuery.data?.items);
  const activePreview = asRecord(
    previewItems[emailPreviewIndex] || previewItems[0],
  );
  const previewHtml = useMemo(
    () => buildEmailPreviewDocument(String(activePreview.body_html || '')),
    [activePreview.body_html],
  );

  return (
    <>
      <Modal
        title={String(detail?.organization || 'Компания')}
        open={modal === 'company'}
        onCancel={closeModal}
        width={1100}
        footer={[
          <Button key="close" onClick={closeModal}>
            Закрыть
          </Button>,
          <Button
            key="action"
            danger
            type="primary"
            onClick={() => {
              if (detail?.row_key) void openActionModal(String(detail.row_key));
            }}
          >
            Действие по компании
          </Button>,
        ]}
      >
        <div style={{ maxHeight: '72vh', overflowY: 'auto', paddingRight: 4 }}>
          <Space style={{ marginBottom: 12 }}>
            <Tag>{statusLabel(detail?.manager_status)}</Tag>
            <Typography.Text type="secondary">
              {statusLabel(detail?.interest)}
            </Typography.Text>
          </Space>
          <Descriptions size="small" column={3} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Попыток">{fmt(summary.attempts)}</Descriptions.Item>
            <Descriptions.Item label="Принято провайдером">
              {fmt(summary.accepted ?? summary.sent_emails)}
            </Descriptions.Item>
            <Descriptions.Item label="Доставлено">{fmt(summary.delivered)}</Descriptions.Item>
            <Descriptions.Item label="Ошибок">{fmt(summary.errors)}</Descriptions.Item>
            <Descriptions.Item label="Ожидает статуса">{fmt(summary.pending)}</Descriptions.Item>
            <Descriptions.Item label="Документов">{fmt(summary.documents)}</Descriptions.Item>
          </Descriptions>
          <Typography.Paragraph>
            <strong>Следующее действие:</strong> {statusLabel(detail?.next_action)}
          </Typography.Paragraph>
          <Typography.Paragraph>
            <strong>Рекомендация:</strong> {statusLabel(detail?.recommended_action)}
          </Typography.Paragraph>

          <Typography.Title level={5}>Данные из документа</Typography.Title>
          <Descriptions size="small" column={1} bordered>
            {Object.entries(fields).map(([key, field]) => {
              const f = asRecord(field);
              return (
                <Descriptions.Item key={key} label={String(f.label || key)}>
                  {String(f.display || '—')}
                </Descriptions.Item>
              );
            })}
          </Descriptions>

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Email-адреса и статусы
          </Typography.Title>
          {emails.length ? (
            emails.map((entry, index) => (
              <div key={index} style={{ marginBottom: 8 }}>
                <div>
                  {String(entry.email || '—')}
                  {entry.role_label ? (
                    <Typography.Text type="secondary">
                      {' '}({String(entry.role_label)})
                    </Typography.Text>
                  ) : null}
                </div>
                <Tag>{statusLabel(entry.manager_status)}</Tag>
                {entry.bounce_reason_label ? (
                  <Typography.Text type="secondary">
                    {' '}{String(entry.bounce_reason_label)}
                  </Typography.Text>
                ) : null}
              </div>
            ))
          ) : (
            <Typography.Text type="secondary">Нет email-адресов</Typography.Text>
          )}

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Все попытки
          </Typography.Title>
          <Table
            size="small"
            rowKey={(row, index) => String(row.id || index)}
            dataSource={attempts}
            locale={{ emptyText: 'Нет зарегистрированных попыток' }}
            pagination={attempts.length > 10 ? { pageSize: 10 } : false}
            columns={[
              { title: 'Email', dataIndex: 'email', render: (value) => String(value || '—') },
              {
                title: 'Попытка',
                dataIndex: 'attempt_number',
                width: 90,
                render: (value) => fmt(value),
              },
              {
                title: 'Статус отправки',
                dataIndex: 'status_label',
                render: (value) => String(value || '—'),
              },
              {
                title: 'Статус доставки',
                dataIndex: 'delivery_status_label',
                render: (value) => String(value || '—'),
              },
              {
                title: 'Провайдер',
                dataIndex: 'provider_label',
                render: (value) => String(value || '—'),
              },
              {
                title: 'Дата',
                dataIndex: 'created_at',
                render: (value) => formatLocalDateTime(String(value || '')),
              },
              {
                title: 'Ошибка',
                dataIndex: 'error',
                render: (value) => (
                  <span style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>
                    {String(value || '—')}
                  </span>
                ),
              },
            ]}
          />

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Письма, принятые провайдером
          </Typography.Title>
          <Table
            size="small"
            rowKey={(row, index) =>
              String(row.provider_message_id || `${row.email || ''}-${row.sent_at || index}`)
            }
            dataSource={acceptedEmails}
            locale={{ emptyText: 'Нет писем, принятых провайдером' }}
            pagination={acceptedEmails.length > 10 ? { pageSize: 10 } : false}
            columns={[
              { title: 'Email', dataIndex: 'email', render: (value) => String(value || '—') },
              {
                title: 'Тема',
                dataIndex: 'subject',
                render: (value) => String(value || '—'),
              },
              {
                title: 'Статус доставки',
                dataIndex: 'manager_status',
                render: (value) => statusLabel(value),
              },
              {
                title: 'Ошибка доставки',
                render: (_, row) => String(row.error || row.bounce_reason_label || '—'),
              },
              {
                title: 'Провайдер',
                dataIndex: 'provider_label',
                render: (value) => String(value || '—'),
              },
              {
                title: 'Принято провайдером',
                dataIndex: 'sent_at',
                render: (value) => formatLocalDateTime(String(value || '')),
              },
              {
                title: '',
                width: 110,
                render: (_, row) =>
                  row.preview_available ? (
                    <Button
                      size="small"
                      onClick={() => {
                        setEmailPreview({
                          campaignId: String(row.campaign_id),
                          recipientId: Number(row.recipient_id),
                        });
                        setEmailPreviewIndex(0);
                      }}
                    >
                      Просмотр
                    </Button>
                  ) : null,
              },
            ]}
          />

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Документы
          </Typography.Title>
          <Table
            size="small"
            rowKey={(row, index) => String(row.path || index)}
            dataSource={documents}
            locale={{ emptyText: 'Нет документов для этой компании' }}
            pagination={documents.length > 10 ? { pageSize: 10 } : false}
            columns={[
              {
                title: 'Документ',
                dataIndex: 'label',
                render: (value, row) => String(value || row.name || '—'),
              },
              { title: 'Тип', dataIndex: 'ext', width: 80 },
              {
                title: 'Размер',
                dataIndex: 'size',
                width: 110,
                render: (value) => `${fmt(Math.round(Number(value || 0) / 1024))} КБ`,
              },
              {
                title: '',
                width: 110,
                render: (_, row) =>
                  String(row.ext || '').toLowerCase() === '.pdf' ? (
                    <Button
                      size="small"
                      onClick={() =>
                        setDocumentPreview({
                          jobId: String(row.job_id || detail?.job_id || ''),
                          path: String(row.path),
                        })
                      }
                    >
                      Просмотр
                    </Button>
                  ) : (
                    <Button
                      size="small"
                      href={previewApi.fileUrl(
                        String(row.job_id || detail?.job_id || ''),
                        String(row.path),
                      )}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Скачать
                    </Button>
                  ),
              },
            ]}
          />

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            История статусов
          </Typography.Title>
          {statusHistory.length ? (
            statusHistory.map((entry, index) => (
              <div key={index}>
                {String(entry.label || '—')}{' '}
                <Typography.Text type="secondary">
                  {formatLocalDateTime(String(entry.at || ''))}
                </Typography.Text>
              </div>
            ))
          ) : (
            <Typography.Text type="secondary">Нет истории статусов</Typography.Text>
          )}

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            Согласия
          </Typography.Title>
          {consents.length ? (
            consents.map((entry, index) => (
              <div key={index}>
                {String(entry.contact || entry.email || '—')} —{' '}
                {String(entry.consent_status_label || '—')}
              </div>
            ))
          ) : (
            <Typography.Text type="secondary">Нет данных по согласиям</Typography.Text>
          )}

          <Typography.Title level={5} style={{ marginTop: 16 }}>
            История действий
          </Typography.Title>
          {actions.length ? (
            actions.map((entry, index) => (
              <div key={index}>
                {String(entry.action_type_label || entry.action_type || '—')}
                {entry.comment ? ` — ${String(entry.comment)}` : ''}
              </div>
            ))
          ) : (
            <Typography.Text type="secondary">Нет истории действий</Typography.Text>
          )}
        </div>
      </Modal>

      <Drawer
        title="Просмотр отправленного письма"
        width="80%"
        open={Boolean(emailPreview)}
        onClose={() => setEmailPreview(null)}
        destroyOnClose
      >
        {emailPreviewQuery.isLoading ? <Typography.Text>Загрузка…</Typography.Text> : null}
        {emailPreviewQuery.isError ? (
          <Alert type="error" showIcon message="Не удалось загрузить письмо" />
        ) : null}
        {previewItems.length > 1 ? (
          <Select
            style={{ minWidth: 260, marginBottom: 16 }}
            value={emailPreviewIndex}
            options={previewItems.map((item, index) => ({
              value: index,
              label: String(item.node_name || `Письмо ${index + 1}`),
            }))}
            onChange={setEmailPreviewIndex}
          />
        ) : null}
        {previewItems.length ? (
          <>
            <Typography.Paragraph>
              <Typography.Text strong>Тема: </Typography.Text>
              {String(activePreview.subject || '—')}
            </Typography.Paragraph>
            <iframe
              title="Превью отправленного письма"
              srcDoc={previewHtml}
              sandbox=""
              style={{
                width: '100%',
                minHeight: '70vh',
                border: '1px solid #e2e7d8',
                borderRadius: 8,
              }}
            />
          </>
        ) : null}
      </Drawer>

      <Drawer
        title="Просмотр документа"
        width="80%"
        open={Boolean(documentPreview)}
        onClose={() => setDocumentPreview(null)}
        destroyOnClose
      >
        {documentPreview ? (
          <iframe
            title="Просмотр документа"
            src={previewApi.fileUrl(documentPreview.jobId, documentPreview.path)}
            style={{ width: '100%', height: '80vh', border: 0 }}
          />
        ) : null}
      </Drawer>
    </>
  );
}
