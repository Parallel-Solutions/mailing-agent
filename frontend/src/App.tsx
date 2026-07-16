import { ConfigProvider, App as AntApp } from 'antd';
import ruRU from 'antd/locale/ru_RU';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { AppRoutes } from '@/routes';
import { tokens } from '@/theme/tokens';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <ConfigProvider
      locale={ruRU}
      theme={{
        token: {
          colorPrimary: tokens.primary,
          colorError: tokens.error,
          colorText: tokens.text,
          colorTextSecondary: tokens.secondaryText,
          colorBorder: tokens.outline,
          colorBgLayout: tokens.background,
          colorBgContainer: tokens.surface,
          borderRadius: tokens.borderRadius,
          fontFamily: tokens.fontFamily,
          controlHeight: 36,
        },
        components: {
          Card: { borderRadiusLG: tokens.cardRadius },
        },
      }}
    >
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <ErrorBoundary>
            <BrowserRouter>
              <AppRoutes />
            </BrowserRouter>
          </ErrorBoundary>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
