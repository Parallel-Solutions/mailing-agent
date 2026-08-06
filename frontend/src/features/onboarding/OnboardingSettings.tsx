import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import {
  onboardingApi,
  onboardingQueryKey,
} from '@/api/onboarding';
import type { OnboardingState } from '@/api/types';
import { useAuthStore } from '@/stores/authStore';

const statusLabels: Record<OnboardingState['status'], string> = {
  active: 'Идёт сейчас',
  paused: 'Приостановлено',
  dismissed: 'Отключено',
  completed: 'Завершено',
};

export function OnboardingSettings() {
  const queryClient = useQueryClient();
  const username = useAuthStore((store) => store.user?.username);
  const queryKey = onboardingQueryKey(username);
  const query = useQuery({ queryKey, queryFn: onboardingApi.get });
  const start = useMutation({
    mutationFn: () => query.data?.status === 'paused'
      ? onboardingApi.update({
          status: 'active',
          current_step: query.data.current_step,
          completed_steps: query.data.completed_steps,
        })
      : onboardingApi.restart(),
    onSuccess: (state) => queryClient.setQueryData(queryKey, state),
  });

  return (
    <Space direction="vertical" size="middle">
      <Alert
        type="info"
        showIcon
        message="Обучение по ai offer"
        description="Тематические главы подробно показывают подключения, компании, письма и документы, конструктор цепочек, создание рассылки и чтение статистики."
      />
      <Typography.Text>
        Статус: <Tag>{query.data ? statusLabels[query.data.status] : 'Загрузка…'}</Tag>
      </Typography.Text>
      <Button type="primary" loading={start.isPending} onClick={() => start.mutate()}>
        {query.data?.status === 'paused' ? 'Продолжить обучение' : 'Начать обучение заново'}
      </Button>
    </Space>
  );
}
