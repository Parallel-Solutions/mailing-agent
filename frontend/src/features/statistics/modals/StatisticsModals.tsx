import { useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  DatePicker,
  Descriptions,
  Form,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import dayjs from 'dayjs';
import { statisticsApi } from '@/api/statistics';
import { useAuthStore } from '@/stores/authStore';
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
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (modal === 'drill') setPage(1);
  }, [modal, drill?.kind]);

  const columns =
    drill?.config.columns.map(([title, getter], index) => ({
      title,
      key: String(index),
      render: (_: unknown, row: Record<string, unknown>) => String(getter(row) ?? '—'),
    })) || [];

  return (
    <Modal
      title={drill?.config.title || 'Детализация'}
      open={modal === 'drill'}
      onCancel={closeModal}
      width={960}
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
      <Table
        size="small"
        loading={drill?.loading}
        rowKey={(row, index) => String(row.row_key || row.job_id || index)}
        dataSource={drill?.rows || []}
        columns={columns}
        pagination={{ current: page, pageSize: 20, onChange: setPage }}
        onRow={(row) => ({
          onClick: () => {
            if (row.row_key) void openCompanyModal(String(row.row_key));
          },
          style: row.row_key ? { cursor: 'pointer' } : undefined,
        })}
      />
    </Modal>
  );
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
  const statusHistory = asRecordArray(detail?.status_history);
  const consents = asRecordArray(detail?.consents);
  const actions = asRecordArray(detail?.action_history);

  return (
    <Modal
      title={String(detail?.organization || 'Компания')}
      open={modal === 'company'}
      onCancel={closeModal}
      width={900}
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
      <Space style={{ marginBottom: 12 }}>
        <Tag>{statusLabel(detail?.manager_status)}</Tag>
        <Typography.Text type="secondary">
          {statusLabel(detail?.interest)}
        </Typography.Text>
      </Space>
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
                <Typography.Text type="secondary"> ({String(entry.role_label)})</Typography.Text>
              ) : null}
            </div>
            <Tag>{statusLabel(entry.manager_status)}</Tag>
            {entry.bounce_reason_label ? (
              <Typography.Text type="secondary"> {String(entry.bounce_reason_label)}</Typography.Text>
            ) : null}
          </div>
        ))
      ) : (
        <Typography.Text type="secondary">Нет отправленных писем</Typography.Text>
      )}

      <Typography.Title level={5} style={{ marginTop: 16 }}>
        История статусов
      </Typography.Title>
      {statusHistory.length ? (
        statusHistory.map((entry, index) => (
          <div key={index}>
            {String(entry.label || '—')}{' '}
            <Typography.Text type="secondary">{String(entry.at || '')}</Typography.Text>
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
            {String(entry.contact || entry.email || '—')} — {String(entry.consent_status_label || '—')}
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
    </Modal>
  );
}
