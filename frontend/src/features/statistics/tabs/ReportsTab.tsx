import { Button, Card, Col, Row, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { statisticsApi } from '@/api/statistics';
import { KpiGrid } from '../components/KpiGrid';
import { useStatistics } from '../StatisticsContext';
import { asRecord, asRecordArray, fmt } from '../utils';

export function ReportsTab() {
  const {
    apiBaseParams,
    refreshNonce,
    openDrilldown,
    openExportModal,
    setReportsHistory,
    setError,
  } = useStatistics();

  const query = useQuery({
    queryKey: ['stats-reports', apiBaseParams, refreshNonce],
    queryFn: () => statisticsApi.reports(apiBaseParams),
  });

  useEffect(() => {
    if (query.data) setReportsHistory(asRecordArray(query.data.history));
    if (query.isError) setError('Не удалось загрузить отчёты.');
  }, [query.data, query.isError, setReportsHistory, setError]);

  const result = query.data || {};
  const summary = asRecord(result.summary);
  const available = asRecordArray(result.available);
  const history = asRecordArray(result.history);

  const kpis = [
    { title: 'Сформировано отчётов', value: fmt(summary.generated), drill: 'reports_all' },
    { title: 'Excel выгрузки', value: fmt(summary.xlsx), drill: 'reports_xlsx' },
    { title: 'CSV выгрузки', value: fmt(summary.csv), drill: 'reports_csv' },
    { title: 'NDJSON журналы', value: fmt(summary.ndjson), drill: 'reports_ndjson' },
  ];

  return (
    <div>
      <KpiGrid items={kpis} loading={query.isLoading} onDrill={(key) => void openDrilldown(key)} />
      <div style={{ marginTop: 16, marginBottom: 8, display: 'flex', justifyContent: 'flex-end' }}>
        <Button type="primary" onClick={() => openExportModal()}>
          Экспорт отчёта
        </Button>
      </div>
      <Row gutter={[16, 16]}>
        {available.map((item) => (
          <Col key={String(item.id)} xs={24} md={12} lg={8}>
            <Card size="small" title={String(item.title || item.id)}>
              <Typography.Paragraph type="secondary">{String(item.description || '')}</Typography.Paragraph>
              <Button onClick={() => openExportModal(String(item.id))}>Сформировать отчёт</Button>
            </Card>
          </Col>
        ))}
      </Row>
      <Table
        style={{ marginTop: 16 }}
        loading={query.isLoading}
        rowKey={(row) => String(row.report_id)}
        dataSource={history}
        locale={{ emptyText: 'Отчёты ещё не формировались' }}
        columns={[
          { title: 'Отчёт', dataIndex: 'report_type' },
          {
            title: 'Период',
            render: (_, r) => `${r.period_from || ''} — ${r.period_to || ''}`,
          },
          { title: 'Формат', dataIndex: 'format' },
          { title: 'Создан', dataIndex: 'created_at' },
          { title: 'Автор', dataIndex: 'author' },
          { title: 'Статус', dataIndex: 'status' },
          {
            title: '',
            key: 'download',
            render: (_, r) => (
              <a href={statisticsApi.reportDownloadUrl(String(r.report_id))}>Скачать</a>
            ),
          },
        ]}
      />
    </div>
  );
}
