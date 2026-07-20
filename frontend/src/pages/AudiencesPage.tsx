import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Drawer, Space, Table, Upload } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { audiencesApi } from '@/api/audiences';
import type { Audience } from '@/api/types';
import { advanceOnboarding } from '@/features/onboarding/events';

export function AudiencesPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Audience | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['audiences'],
    queryFn: () => audiencesApi.list(),
  });
  const membersQuery = useQuery({
    queryKey: ['audience-members', selected?.id],
    queryFn: () => audiencesApi.members(selected!.id, { limit: 50 }),
    enabled: Boolean(selected?.id),
  });

  const createMutation = useMutation({
    mutationFn: () => audiencesApi.create(`Аудитория ${new Date().toLocaleString('ru-RU')}`),
    onSuccess: (audience) => {
      message.success('Аудитория создана');
      setSelected(audience);
      advanceOnboarding('audience-open');
      void queryClient.invalidateQueries({ queryKey: ['audiences'] });
    },
  });

  return (
    <>
      <ProTable<Audience>
        rowKey="id"
        loading={isLoading}
        search={false}
        headerTitle="База получателей"
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
          { title: 'Источник', dataIndex: 'source' },
          { title: 'Качество', dataIndex: 'quality_score' },
          { title: 'Обновлена', dataIndex: 'updated_at', valueType: 'dateTime' },
          {
            title: 'Действия',
            valueType: 'option',
            render: (_, row) => (
              <Space>
                <a onClick={() => setSelected(row)}>Открыть</a>
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
        onClose={() => setSelected(null)}
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
                  advanceOnboarding('audience-import', 'campaign-basics');
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
            { title: 'Статус', dataIndex: 'validation_status' },
          ]}
          pagination={{ pageSize: 20, total: membersQuery.data?.total }}
        />
      </Drawer>
    </>
  );
}
