import {
  ApartmentOutlined,
  BarChartOutlined,
  ClusterOutlined,
  MailOutlined,
  PlusCircleOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons';
import type { ProLayoutProps } from '@ant-design/pro-components';
import { PageContainer, ProLayout } from '@ant-design/pro-components';
import { Dropdown } from 'antd';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
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
  const menuPathname = /^\/campaigns\/[^/]+\/chain$/.test(location.pathname)
    ? '/chains'
    : location.pathname;

  return (
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
      actionsRender={() => []}
    >
      <PageContainer>
        <Outlet />
      </PageContainer>
    </ProLayout>
  );
}
