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
const CampaignsListPage = lazy(() =>
  import('@/pages/CampaignsListPage').then((m) => ({ default: m.CampaignsListPage })),
);
const CampaignNewPage = lazy(() =>
  import('@/pages/CampaignNewPage').then((m) => ({ default: m.CampaignNewPage })),
);
const CampaignDetailPage = lazy(() =>
  import('@/pages/CampaignDetailPage').then((m) => ({ default: m.CampaignDetailPage })),
);
const TemplatesPage = lazy(() =>
  import('@/pages/TemplatesPage').then((m) => ({ default: m.TemplatesPage })),
);
const TemplateEditorPage = lazy(() =>
  import('@/pages/TemplateEditorPage').then((m) => ({ default: m.TemplateEditorPage })),
);
const AudiencesPage = lazy(() =>
  import('@/pages/AudiencesPage').then((m) => ({ default: m.AudiencesPage })),
);
const ConnectionsPage = lazy(() =>
  import('@/pages/ConnectionsPage').then((m) => ({ default: m.ConnectionsPage })),
);
const ProfilePage = lazy(() =>
  import('@/pages/ProfilePage').then((m) => ({ default: m.ProfilePage })),
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
          <Route
            path="/campaigns"
            element={
              <Lazy>
                <CampaignsListPage />
              </Lazy>
            }
          />
          <Route
            path="/campaigns/new"
            element={
              <Lazy>
                <CampaignNewPage />
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
            path="/templates/:id/edit"
            element={
              <Lazy>
                <TemplateEditorPage />
              </Lazy>
            }
          />
          <Route
            path="/audiences"
            element={
              <Lazy>
                <AudiencesPage />
              </Lazy>
            }
          />
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
