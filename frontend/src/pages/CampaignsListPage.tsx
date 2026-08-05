import { MoreOutlined, PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Dropdown, Progress, Space, Tag, Tooltip } from 'antd';
import type { MenuProps } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { campaignsApi, type CampaignListScope } from '@/api/campaigns';
import type { Campaign } from '@/api/types';
import {
  campaignProgressLabel,
  canCampaignAction,
  shouldPollCampaign,
} from '@/features/campaigns/campaignLifecycle';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';
import { formatLocalDateTime } from '@/utils/dateTime';
import { statusLabel } from '@/utils/presentation';

const statusColor: Record<string, string> = {
  draft: 'default',
  scheduled: 'processing',
  running: 'success',
  paused: 'warning',
  completed: 'blue',
  completed_with_errors: 'orange',
  cancelled: 'error',
};

type CampaignsListPageProps = {
  embedded?: boolean;
  scope?: Extract<CampaignListScope, 'draft' | 'launched'>;
};

export function CampaignsListPage({
  embedded = false,
  scope = 'launched',
}: CampaignsListPageProps) {
  const navigate = useNavigate();
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const activeCampaignId = useCampaignDraftStore((state) => state.campaignId);
  const resetCampaignDraft = useCampaignDraftStore((state) => state.reset);
  const { data, isLoading } = useQuery({
    queryKey: ['campaigns', scope],
    queryFn: () => campaignsApi.list({ scope, limit: 100 }),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => shouldPollCampaign(item.status))
        ? 10_000
        : 30_000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['campaigns', scope] });
  const isDraftList = scope === 'draft';
  const campaignPath = (campaign: Campaign) =>
    isDraftList ? `/campaigns/new?id=${campaign.id}` : `/campaigns/${campaign.id}`;

  const pause = useMutation({
    mutationFn: (id: string) => campaignsApi.pause(id),
    onSuccess: () => {
      message.success('Пауза');
      invalidate();
    },
  });
  const resume = useMutation({
    mutationFn: (id: string) => campaignsApi.resume(id),
    onSuccess: () => {
      message.success('Продолжено');
      invalidate();
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => campaignsApi.cancel(id),
    onSuccess: () => {
      message.success('Отменено');
      invalidate();
    },
  });
  const duplicate = useMutation({
    mutationFn: (id: string) => campaignsApi.duplicate(id),
    onSuccess: (camp) => {
      message.success('Копия создана');
      navigate(`/campaigns/new?id=${camp.id}`);
    },
  });
  const archive = useMutation({
    mutationFn: (id: string) => campaignsApi.archive(id),
    onSuccess: (_, archivedId) => {
      if (activeCampaignId === archivedId) resetCampaignDraft();
      message.success(isDraftList ? 'Черновик удалён' : 'Рассылка удалена');
      invalidate();
    },
    onError: (error: Error) => message.error(error.message),
  });

  const confirmArchive = (campaign: Campaign) => {
    modal.confirm({
      title: isDraftList ? 'Удалить черновик?' : 'Удалить рассылку?',
      content: isDraftList
        ? `Черновик «${campaign.name}» исчезнет из списка.`
        : `Рассылка «${campaign.name}» исчезнет из списка. История отправки сохранится.`,
      okText: 'Удалить',
      cancelText: 'Отмена',
      okButtonProps: { danger: true },
      onOk: () => archive.mutateAsync(campaign.id),
    });
  };

  const actionMenu = (campaign: Campaign): MenuProps => ({
    items: [
      ...(canCampaignAction(campaign, 'edit')
        ? [{ key: 'edit', label: 'Редактировать' }]
        : []),
      { key: 'duplicate', label: 'Дублировать' },
      ...(canCampaignAction(campaign, 'resume')
        ? [{ key: 'resume', label: 'Продолжить отправку' }]
        : canCampaignAction(campaign, 'pause')
          ? [{ key: 'pause', label: 'Поставить на паузу' }]
          : []),
      ...(canCampaignAction(campaign, 'cancel')
        ? [{ key: 'cancel', label: 'Отменить рассылку', danger: true }]
        : []),
      ...(canCampaignAction(campaign, 'archive')
        ? [
            { type: 'divider' as const },
            { key: 'archive', label: 'Удалить', danger: true },
          ]
        : []),
    ],
    onClick: ({ key }) => {
      if (key === 'edit') navigate(`/campaigns/new?id=${campaign.id}`);
      if (key === 'duplicate') duplicate.mutate(campaign.id);
      if (key === 'resume') resume.mutate(campaign.id);
      if (key === 'pause') pause.mutate(campaign.id);
      if (key === 'cancel') cancel.mutate(campaign.id);
      if (key === 'archive') confirmArchive(campaign);
    },
  });

  return (
    <div data-onboarding-id="campaigns-overview">
      <ProTable<Campaign>
      rowKey="id"
      loading={isLoading}
      search={false}
      headerTitle={embedded ? undefined : isDraftList ? 'Черновики' : 'Рассылки'}
      toolBarRender={() => [
        <Button key="new" type="primary" icon={<PlusOutlined />} onClick={() => navigate('/campaigns/new?new=1')}>
          Создать рассылку
        </Button>,
      ]}
      dataSource={data?.items || []}
      columns={[
        {
          title: 'Название',
          dataIndex: 'name',
          width: 180,
          ellipsis: true,
          render: (_, row) => <Link to={campaignPath(row)} title={row.name}>{row.name}</Link>,
        },
        {
          title: 'Статус',
          dataIndex: 'status',
          width: 150,
          render: (_, row) => <Tag color={statusColor[row.status] || 'default'}>{statusLabel(row.status)}</Tag>,
        },
        { title: 'Тема', dataIndex: 'mail_subject', width: 160, ellipsis: true },
        { title: 'Провайдер', dataIndex: 'transport', width: 110, ellipsis: true },
        {
          title: 'Прогресс',
          width: 180,
          render: (_, row) => (
            <Progress
              percent={row.progress || 0}
              size="small"
              format={() => campaignProgressLabel(row)}
            />
          ),
        },
        { title: 'Попытки', dataIndex: 'attempt_count', width: 90 },
        { title: 'Принято провайдером', dataIndex: 'success_count', width: 170 },
        { title: 'Пропущено', dataIndex: 'skipped_count', width: 110 },
        { title: 'Ошибки', dataIndex: 'failed_recipient_count', width: 90 },
        { title: 'Создана', dataIndex: 'created_at', width: 150, render: (_, row) => formatLocalDateTime(row.created_at) },
        {
          title: 'Действия',
          valueType: 'option',
          fixed: 'right',
          width: 140,
          align: 'center',
          render: (_, row) => (
            <Space size={2} wrap={false}>
              <Button type="link" size="small" onClick={() => navigate(campaignPath(row))}>
                {isDraftList ? 'Редактировать' : 'Открыть'}
              </Button>
              <Dropdown menu={actionMenu(row)} placement="bottomRight" trigger={['click']}>
                <Tooltip title="Другие действия">
                  <Button
                    type="text"
                    size="small"
                    aria-label={`Другие действия: ${row.name}`}
                    icon={<MoreOutlined />}
                    loading={archive.isPending && archive.variables === row.id}
                  />
                </Tooltip>
              </Dropdown>
            </Space>
          ),
        },
      ]}
      scroll={{ x: 1530 }}
      />
    </div>
  );
}
