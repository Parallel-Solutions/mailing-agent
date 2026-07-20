import {
  ApartmentOutlined,
  BarChartOutlined,
  ClusterOutlined,
  MailOutlined,
  PlusCircleOutlined,
  QuestionCircleOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ProLayoutProps } from '@ant-design/pro-components';
import { PageContainer, ProLayout } from '@ant-design/pro-components';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Dropdown, Tooltip } from 'antd';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState } from '@/api/types';
import { OnboardingTour } from '@/features/onboarding/OnboardingTour';
import { useAuthStore } from '@/stores/authStore';
import { tokens } from '@/theme/tokens';

const route: ProLayoutProps['route'] = {
  path: '/',
  routes: [
    { path: '/', name: 'Статистика', icon: <BarChartOutlined /> },
    { path: '/campaigns/new', name: 'Создать рассылку', icon: <PlusCircleOutlined /> },
    { path: '/campaigns', name: 'Рассылки', icon: <UnorderedListOutlined /> },
    { path: '/templates', name: 'Шаблоны и документы', icon: <MailOutlined /> },
    { path: '/chains', name: 'Конструктор цепочек', icon: <ApartmentOutlined /> },
    { path: '/audiences', name: 'База получателей', icon: <TeamOutlined /> },
    { path: '/connections', name: 'Подключения', icon: <ClusterOutlined /> },
    { path: '/profile', name: 'Профиль', icon: <UserOutlined /> },
  ],
};

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const queryClient = useQueryClient();
  const restartOnboarding = useMutation({
    mutationFn: onboardingApi.restart,
    onSuccess: (state) => queryClient.setQueryData<OnboardingState>(['onboarding'], state),
  });
  const menuPathname = /^\/campaigns\/[^/]+\/chain$/.test(location.pathname)
    ? '/chains'
    : location.pathname;

  return (
    <>
    <ProLayout
      title="CampaignFlow"
      layout="side"
      fixSiderbar
      fixedHeader
      breakpoint="lg"
      location={{ pathname: menuPathname }}
      route={route}
      token={{
        bgLayout: tokens.background,
        colorPrimary: tokens.primary,
        sider: { colorMenuBackground: tokens.surface },
      }}
      menuItemRender={(item, dom) => <Link to={item.path || '/'}>{dom}</Link>}
      avatarProps={{
        src: undefined,
        title: user?.username || 'user',
        size: 'small',
        render: (_props, dom) => (
          <Dropdown
            menu={{
              items: [
                { key: 'profile', label: 'Профиль', onClick: () => navigate('/profile') },
                {
                  key: 'logout',
                  label: 'Выйти',
                  onClick: async () => {
                    await logout();
                    navigate('/login');
                  },
                },
              ],
            }}
          >
            {dom}
          </Dropdown>
        ),
      }}
      actionsRender={() => [
        <Tooltip title="Запустить обучение" key="onboarding">
          <Button
            type="text"
            aria-label="Запустить обучение"
            icon={<QuestionCircleOutlined />}
            loading={restartOnboarding.isPending}
            onClick={() => restartOnboarding.mutate()}
          />
        </Tooltip>,
      ]}
    >
      <PageContainer>
        <Outlet />
      </PageContainer>
    </ProLayout>
    <OnboardingTour />
    </>
  );
}
