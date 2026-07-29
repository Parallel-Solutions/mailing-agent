import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Drawer, Space, Table, Upload } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { audiencesApi } from '@/api/audiences';
import type { Audience } from '@/api/types';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { formatLocalDateTime } from '@/utils/dateTime';
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

  const createMutation = useMutation({
    mutationFn: () => audiencesApi.create(`Аудитория ${new Date().toLocaleString('ru-RU')}`),
    onSuccess: () => {
      message.success('Аудитория создана');
      void queryClient.invalidateQueries({ queryKey: ['audiences'] });
    },
  });

  return (
    <>
      <ProTable<Audience>
        rowKey="id"
        loading={isLoading}
        search={false}
        headerTitle={embedded ? undefined : 'База получателей'}
        toolBarRender={() => [
          <Button
            key="new"
            type="primary"
            icon={<PlusOutlined />}
            data-onboarding-id="create-audience"
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
              <Button data-onboarding-id="audience-import">Импорт</Button>
            </Upload>
          ) : null
        }
      >
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
              render: (value) => statusLabel(String(value || '')),
            },
          ]}
          pagination={{ pageSize: 20, total: membersQuery.data?.total }}
        />
      </Drawer>
    </>
  );
}
