import {
  ApartmentOutlined,
  BarChartOutlined,
  BankOutlined,
  ClusterOutlined,
  MailOutlined,
  PlusCircleOutlined,
  QuestionCircleOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ProLayoutProps } from '@ant-design/pro-components';
import { PageContainer, ProLayout } from '@ant-design/pro-components';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Dropdown, Tooltip } from 'antd';
import { useState } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { onboardingApi } from '@/api/onboarding';
import type { OnboardingState } from '@/api/types';
import { OnboardingTour } from '@/features/onboarding/OnboardingTour';
import {
  ONBOARDING_CHAPTERS,
  ONBOARDING_STEPS,
  type OnboardingChapterId,
} from '@/features/onboarding/steps';
import { usePermissions } from '@/hooks/usePermissions';
import { useAuthStore } from '@/stores/authStore';
import { tokens } from '@/theme/tokens';
import { APP_TOP_BAR_HEIGHT, AppTopBar } from '@/layout/AppTopBar';
import '@/layout/AppTopBar.css';

const ONBOARDING_CHAPTER_STORAGE_KEY = 'campaignflow:onboarding-chapter';

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const { isAppAdmin } = usePermissions();
  const queryClient = useQueryClient();
  const [onboardingSession, setOnboardingSession] = useState(0);
  const [onboardingChapter, setOnboardingChapter] = useState<OnboardingChapterId>();
  const [onboardingMenuOpen, setOnboardingMenuOpen] = useState(false);
  const startOnboarding = useMutation({
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: ['onboarding'] });
    },
    mutationFn: (chapterId: OnboardingChapterId) => {
      const chapter = ONBOARDING_CHAPTERS.find((item) => item.id === chapterId);
      const firstStepIndex = ONBOARDING_STEPS.findIndex(
        (step) => step.id === chapter?.stepIds[0],
      );
      const currentState = queryClient.getQueryData<OnboardingState>(['onboarding']);
      return onboardingApi.update({
        status: 'active',
        current_step: Math.max(0, firstStepIndex),
        completed_steps: currentState?.completed_steps ?? [],
      });
    },
    onSuccess: (state, chapterId) => {
      queryClient.setQueryData<OnboardingState>(['onboarding'], state);
      window.sessionStorage.setItem(ONBOARDING_CHAPTER_STORAGE_KEY, chapterId);
      setOnboardingChapter(chapterId);
      setOnboardingSession((current) => current + 1);
    },
  });
  const onboardingChapters = ONBOARDING_CHAPTERS.filter(
    (chapter) => chapter.id !== 'companies' || isAppAdmin,
  );

  const launchOnboarding = (chapterId: OnboardingChapterId) => {
    const chapter = ONBOARDING_CHAPTERS.find((item) => item.id === chapterId);
    if (!chapter) return;
    navigate(chapter.entryRoute);
    startOnboarding.mutate(chapterId);
  };

  const routes: ProLayoutProps['route'] = {
    path: '/',
    routes: [
      { path: '/', name: 'Статистика', icon: <BarChartOutlined /> },
      { path: '/campaigns/new', name: 'Создать рассылку', icon: <PlusCircleOutlined /> },
      { path: '/chains', name: 'Конструктор цепочек', icon: <ApartmentOutlined /> },
      { path: '/templates', name: 'Шаблоны и документы', icon: <MailOutlined /> },
      { path: '/connections', name: 'Подключения', icon: <ClusterOutlined /> },
      ...(isAppAdmin
        ? [{ path: '/companies', name: 'Компании', icon: <BankOutlined /> }]
        : []),
      { path: '/profile', name: 'Профиль', icon: <UserOutlined /> },
    ],
  };

  const menuPathname = /^\/campaigns\/[^/]+\/chain$/.test(location.pathname)
    ? '/chains'
    : location.pathname;

  return (
    <div
      className="app-layout-shell"
      style={{ ['--app-top-bar-height' as string]: `${APP_TOP_BAR_HEIGHT}px` }}
    >
      <AppTopBar />
      <ProLayout
        logo={false}
        title={false}
        layout="side"
        fixSiderbar
        fixedHeader
        breakpoint="lg"
        location={{ pathname: menuPathname }}
        route={routes}
        menuHeaderRender={() => null}
        token={{
          bgLayout: tokens.background,
          colorPrimary: tokens.primary,
          sider: { colorMenuBackground: tokens.surface },
        }}
        menuItemRender={(item, dom) => <Link to={item.path || '/'}>{dom}</Link>}
        avatarProps={{
          src: user?.company?.logo_url || undefined,
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
            <Dropdown
              trigger={['click']}
              open={onboardingMenuOpen}
              onOpenChange={setOnboardingMenuOpen}
              menu={{
                items: onboardingChapters.map((chapter) => ({
                  key: chapter.id,
                  label: (
                    <div>
                      <div>{chapter.title}</div>
                      <div style={{ color: tokens.secondaryText, fontSize: 12 }}>
                        {chapter.description}
                      </div>
                    </div>
                  ),
                })),
                onClick: ({ key }) => {
                  setOnboardingMenuOpen(false);
                  launchOnboarding(key as OnboardingChapterId);
                },
              }}
            >
              <Button
                type="text"
                aria-label="Запустить обучение"
                icon={<QuestionCircleOutlined />}
                loading={startOnboarding.isPending}
              />
            </Dropdown>
          </Tooltip>,
        ]}
      >
        <PageContainer>
          <Outlet />
        </PageContainer>
      </ProLayout>
      <OnboardingTour key={onboardingSession} chapterId={onboardingChapter} />
    </div>
  );
}
