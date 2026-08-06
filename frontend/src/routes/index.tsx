import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';
import { AppLayout } from '@/layout/AppLayout';
import { ProtectedRoute } from '@/routes/ProtectedRoute';
import { LoginPage } from '@/pages/LoginPage';
import { RegisterPage } from '@/pages/RegisterPage';

const StatisticsPage = lazy(() =>
  import('@/features/statistics/StatisticsPage').then((m) => ({ default: m.StatisticsPage })),
);
const CampaignNewPage = lazy(() =>
  import('@/pages/CampaignNewPage').then((m) => ({ default: m.CampaignNewPage })),
);
const CampaignDetailPage = lazy(() =>
  import('@/pages/CampaignDetailPage').then((m) => ({ default: m.CampaignDetailPage })),
);
const EmailChainBuilderPage = lazy(() =>
  import('@/pages/EmailChainBuilderPage').then((m) => ({ default: m.EmailChainBuilderPage })),
);
const ChainsPage = lazy(() =>
  import('@/pages/ChainsPage').then((m) => ({ default: m.ChainsPage })),
);
const TemplatesPage = lazy(() =>
  import('@/pages/TemplatesPage').then((m) => ({ default: m.TemplatesPage })),
);
const TemplateEditorPage = lazy(() =>
  import('@/pages/TemplateEditorPage').then((m) => ({ default: m.TemplateEditorPage })),
);
const ConnectionsPage = lazy(() =>
  import('@/pages/ConnectionsPage').then((m) => ({ default: m.ConnectionsPage })),
);
const ProfilePage = lazy(() =>
  import('@/pages/ProfilePage').then((m) => ({ default: m.ProfilePage })),
);
const CompaniesPage = lazy(() =>
  import('@/pages/CompaniesPage').then((m) => ({ default: m.CompaniesPage })),
);
const NotFoundPage = lazy(() =>
  import('@/pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })),
);

function Lazy({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<Spin style={{ margin: 48 }} />}>{children}</Suspense>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<AppLayout />}>
          <Route
            path="/"
            element={
              <Lazy>
                <StatisticsPage />
              </Lazy>
            }
          />
          <Route path="/statistics" element={<Navigate to="/" replace />} />
          <Route path="/campaigns" element={<Navigate to="/?tab=campaign-list" replace />} />
          <Route
            path="/campaigns/new"
            element={
              <Lazy>
                <CampaignNewPage />
              </Lazy>
            }
          />
          <Route
            path="/chains/:id"
            element={
              <Lazy>
                <EmailChainBuilderPage />
              </Lazy>
            }
          />
          <Route
            path="/campaigns/:id/chain"
            element={
              <Lazy>
                <EmailChainBuilderPage legacyCampaign />
              </Lazy>
            }
          />
          <Route
            path="/campaigns/:id"
            element={
              <Lazy>
                <CampaignDetailPage />
              </Lazy>
            }
          />
          <Route
            path="/templates"
            element={
              <Lazy>
                <TemplatesPage />
              </Lazy>
            }
          />
          <Route
            path="/chains"
            element={
              <Lazy>
                <ChainsPage />
              </Lazy>
            }
          />
          <Route
            path="/templates/:id/edit"
            element={
              <Lazy>
                <TemplateEditorPage />
              </Lazy>
            }
          />
          <Route path="/audiences" element={<Navigate to="/?tab=audiences" replace />} />
          <Route
            path="/connections"
            element={
              <Lazy>
                <ConnectionsPage />
              </Lazy>
            }
          />
          <Route
            path="/profile"
            element={
              <Lazy>
                <ProfilePage />
              </Lazy>
            }
          />
          <Route
            path="/companies"
            element={
              <Lazy>
                <CompaniesPage />
              </Lazy>
            }
          />
          <Route path="/dashboard" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
      <Route
        path="*"
        element={
          <Lazy>
            <NotFoundPage />
          </Lazy>
        }
      />
    </Routes>
  );
}
