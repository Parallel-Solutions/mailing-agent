import { useEffect } from 'react';
import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { Alert, App, Button, Drawer, Progress, Space, Table, Tag, Tooltip, Upload } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { audiencesApi } from '@/api/audiences';
import type { Audience, Recipient } from '@/api/types';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { APP_TOP_BAR_HEIGHT } from '@/layout/AppTopBar';
import { formatLocalDateTime } from '@/utils/dateTime';
import { emailValidationReason } from '@/utils/emailValidation';
import { statusLabel } from '@/utils/presentation';

export function AudiencesPage({ embedded = false }: { embedded?: boolean }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { searchParams, pushParams } = useUrlNavigation();
  const audienceId = searchParams.get('audience');
  const { data, isLoading } = useQuery({
    queryKey: ['audiences'],
    queryFn: () => audiencesApi.list(),
  });
  const selected = (data || []).find((item) => item.id === audienceId) || null;
  const membersQuery = useQuery({
    queryKey: ['audience-members', selected?.id],
    queryFn: () => audiencesApi.members(selected!.id, { limit: 50 }),
    enabled: Boolean(selected?.id),
  });
  const validationQuery = useQuery({
    queryKey: ['audience-email-validation', selected?.id],
    queryFn: () => audiencesApi.validation(selected!.id),
    enabled: Boolean(selected?.id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'queued' || status === 'running' ? 3000 : false;
    },
  });
  const startValidation = useMutation({
    mutationFn: () => audiencesApi.startValidation(selected!.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['audience-email-validation', selected?.id] });
    },
  });
  const validation = validationQuery.data;

  useEffect(() => {
    if (!selected?.id || validation?.status !== 'completed') return;
    void queryClient.invalidateQueries({ queryKey: ['audience-members', selected.id] });
    void queryClient.invalidateQueries({ queryKey: ['audiences'] });
  }, [queryClient, selected?.id, validation?.completed_at, validation?.status]);


  const createMutation = useMutation({
    mutationFn: () => audiencesApi.create(`Аудитория ${new Date().toLocaleString('ru-RU')}`),
    onSuccess: (audience) => {
      message.success('Аудитория создана');
      void queryClient.invalidateQueries({ queryKey: ['audiences'] });
      pushParams({ audience: audience.id });
    },
  });

  return (
    <>
      <ProTable<Audience>
        rowKey="id"
        loading={isLoading}
        search={false}
        scroll={{ x: 'max-content' }}
        headerTitle={embedded ? undefined : 'База получателей'}
        toolBarRender={() => [
          <Button
            key="new"
            type="primary"
            icon={<PlusOutlined />}
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            Создать аудиторию
          </Button>,
        ]}
        dataSource={data || []}
        columns={[
          { title: 'Название', dataIndex: 'name' },
          { title: 'Записей', dataIndex: 'member_count' },
          {
            title: 'Источник',
            dataIndex: 'source',
            render: (value) =>
              ({ manual: 'Создано вручную', import: 'Импортировано' })[String(value)] ||
              'Внешний источник',
          },
          { title: 'Качество', dataIndex: 'quality_score' },
          { title: 'Обновлена', dataIndex: 'updated_at', render: (_, row) => formatLocalDateTime(row.updated_at) },
          {
            title: 'Действия',
            valueType: 'option',
            render: (_, row) => (
              <Space>
                <a onClick={() => pushParams({ audience: row.id })}>Открыть</a>
                <a
                  onClick={async () => {
                    await audiencesApi.duplicate(row.id);
                    void queryClient.invalidateQueries({ queryKey: ['audiences'] });
                  }}
                >
                  Дублировать
                </a>
              </Space>
            ),
          },
        ]}
      />

      <Drawer
        rootStyle={{ top: APP_TOP_BAR_HEIGHT }}
        width={820}
        open={Boolean(selected)}
        onClose={() => pushParams({}, ['audience'])}
        title={selected?.name}
        extra={
          selected ? (
            <Upload
              accept=".csv,.xlsx"
              showUploadList={false}
              customRequest={async ({ file, onSuccess, onError }) => {
                try {
                  await audiencesApi.importFile(selected.id, file as File);
                  message.success('Импорт выполнен');
                  void queryClient.invalidateQueries({ queryKey: ['audience-members', selected.id] });
                  void queryClient.invalidateQueries({ queryKey: ['audiences'] });
                  onSuccess?.({});
                } catch (error) {
                  onError?.(error as Error);
                }
              }}
            >
              <Button>Импорт</Button>
            </Upload>
          ) : null
        }
      >
        {validation?.enabled ? (
          <Alert
            showIcon
            style={{ marginBottom: 16 }}
            type={
              validation.status === 'failed'
                ? 'error'
                : validation.status === 'completed'
                  ? validation.invalid_count > 0 ? 'warning' : 'success'
                  : 'info'
            }
            message="Дополнительная проверка SMTP.BZ"
            description={(
              <Space direction="vertical" style={{ width: '100%' }}>
                <Progress percent={validation.progress_percent} size="small" />
                <span>
                  Подтверждено: {validation.valid_count}, SMTP.BZ считает недоставляемыми: {validation.invalid_count},
                  не подтверждено: {validation.unknown_count}.
                </span>
                <span>Результаты SMTP.BZ не исключают адреса автоматически; обязательными остаются синтаксис и DNS.</span>
              </Space>
            )}
            action={(
              <Button
                size="small"
                loading={startValidation.isPending}
                onClick={() => startValidation.mutate()}
              >
                {validation.status === 'not_started' ? 'Проверить через SMTP.BZ' : 'Проверить повторно'}
              </Button>
            )}
          />
        ) : null}
        <Table
          rowKey="id"
          loading={membersQuery.isLoading}
          dataSource={membersQuery.data?.items || []}
          columns={[
            { title: 'Компания', dataIndex: 'company' },
            { title: 'Контакт', dataIndex: 'contact_name' },
            { title: 'Email', dataIndex: 'email' },
            { title: 'Регион', dataIndex: 'region' },
            {
              title: 'Статус',
              dataIndex: 'validation_status',
              render: (value, row: Recipient) => {
                const reason = emailValidationReason(row);
                return (
                  <Tooltip title={reason || undefined}>
                    <Tag color={value === 'valid' ? 'green' : value === 'invalid' ? 'red' : 'gold'}>
                      {statusLabel(String(value || ''))}
                    </Tag>
                  </Tooltip>
                );
              },
            },
          ]}
          pagination={{ pageSize: 20, total: membersQuery.data?.total }}
        />
      </Drawer>
    </>
  );
}
