import { ArrowLeftOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { App, Button, Empty, Popconfirm, Tag } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { chainsApi, type ChainListItem } from '@/api/chains';
import { formatLocalDateTime } from '@/utils/dateTime';
import {
  useActiveOnboardingStep,
} from '@/features/onboarding/events';
import { OnboardingChainPreview } from '@/features/onboarding/OnboardingChainPreview';
import { resolveCampaignReturnTarget } from '@/features/campaigns/campaignNavigation';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';

type ChainsLocationState = {
  campaignId?: string;
  returnTo?: string;
};

export function ChainsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const activeCampaignId = useCampaignDraftStore((state) => state.campaignId);
  const campaignContext = resolveCampaignReturnTarget(location.state, activeCampaignId);
  const campaignId = campaignContext?.campaignId;
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [showOnboardingPreview, setShowOnboardingPreview] = useState(false);
  const activeOnboardingStep = useActiveOnboardingStep();
  const previousOnboardingStepRef = useRef<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['chains'],
    queryFn: () => chainsApi.list({ limit: 100 }),
  });

  const chainNavigationState: ChainsLocationState | undefined = campaignContext
    ? { campaignId: campaignContext.campaignId, returnTo: campaignContext.path }
    : undefined;

  const campaignUrl = (chainId: string) =>
    campaignId
      ? `/campaigns/new?id=${campaignId}&email_chain_id=${chainId}`
      : `/campaigns/new?email_chain_id=${chainId}`;

  const createChain = useMutation({
    mutationFn: () => chainsApi.create({ name: 'Новая цепочка' }),
    onSuccess: (chain) => {
      void queryClient.invalidateQueries({ queryKey: ['chains'] });
      navigate(`/chains/${chain.id}`, { state: chainNavigationState });
    },
    onError: (error: Error) => message.error(error.message),
  });

  const deleteChain = useMutation({
    mutationFn: (id: string) => chainsApi.remove(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['chains'] });
      message.success('Цепочка удалена');
    },
    onError: (error: Error) => message.error(error.message),
  });

  useEffect(() => {
    const previewSteps = [
      'chain-builder',
      'chain-settings',
      'chain-publish',
      'chain-name-status',
      'chain-add-nodes',
      'chain-email-template',
      'chain-documents',
      'chain-transitions',
      'chain-link-purpose',
      'chain-save',
      'chain-publish-button',
    ];
    const previousStep = previousOnboardingStepRef.current;
    previousOnboardingStepRef.current = activeOnboardingStep;
    if (previewSteps.includes(activeOnboardingStep || '')) {
      setShowOnboardingPreview(true);
    } else if (previousStep?.startsWith('chain-')) {
      setShowOnboardingPreview(false);
    }
  }, [activeOnboardingStep]);

  return (
    <div data-onboarding-id="chains-overview">
      <div data-onboarding-id="chains-list">
        <ProTable<ChainListItem>
      rowKey="id"
      loading={isLoading}
      search={false}
      headerTitle="Конструктор цепочек"
      toolBarRender={() => [
        ...(campaignContext
          ? [
              <Button
                key="return-to-campaign"
                icon={<ArrowLeftOutlined />}
                onClick={() => navigate(campaignContext.path)}
              >
                {'\u0412\u0435\u0440\u043d\u0443\u0442\u044c\u0441\u044f \u043a \u0440\u0430\u0441\u0441\u044b\u043b\u043a\u0435'}
              </Button>,
            ]
          : []),
        <Button
          key="new"
          type="primary"
          icon={<PlusOutlined />}
          loading={createChain.isPending}
          data-onboarding-id="create-chain"
          onClick={() => createChain.mutate()}
        >
          Создать цепочку
        </Button>,
      ]}
      dataSource={data?.items ?? []}
      locale={{
        emptyText: (
          <Empty description="Цепочек пока нет">
            <Button
              type="primary"
              icon={<PlusOutlined />}
              loading={createChain.isPending}
              data-onboarding-id="create-chain"
              onClick={() => createChain.mutate()}
            >
              Создать цепочку
            </Button>
          </Empty>
        ),
      }}
      columns={[
        {
          title: 'Название',
          dataIndex: 'name',
          render: (_, row) => (
            <Link to={`/chains/${row.id}`} state={chainNavigationState}>
              {row.name}
            </Link>
          ),
        },
        {
          title: 'Статус',
          dataIndex: 'published',
          render: (_, row) => (
            <Tag color={row.published ? 'success' : 'default'}>
              {row.published ? 'Опубликована' : 'Черновик'}
            </Tag>
          ),
        },
        { title: 'Обновлена', dataIndex: 'updated_at', render: (_, row) => formatLocalDateTime(row.updated_at) },
        {
          title: 'Действия',
          valueType: 'option',
          render: (_, row) => [
            <a
              key="open"
              onClick={() => navigate(`/chains/${row.id}`, { state: chainNavigationState })}
            >
              Открыть конструктор
            </a>,
            <a key="campaign" onClick={() => navigate(campaignUrl(row.id))}>
              К рассылке
            </a>,
            <Popconfirm
              key="delete"
              title={`Удалить цепочку «${row.name}»?`}
              description="Цепочка исчезнет из списка. В связанных рассылках потребуется выбрать новую цепочку."
              okText="Удалить"
              cancelText="Отмена"
              okButtonProps={{ danger: true }}
              onConfirm={() => deleteChain.mutateAsync(row.id)}
            >
              <Button
                type="link"
                danger
                icon={<DeleteOutlined />}
                loading={deleteChain.isPending && deleteChain.variables === row.id}
              >
                Удалить
              </Button>
            </Popconfirm>,
          ],
        },
      ]}
        />
      </div>
      <OnboardingChainPreview open={showOnboardingPreview} />
    </div>
  );
}
