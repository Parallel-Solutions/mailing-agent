import { Spin } from 'antd';
import { useEffect } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuthStore } from '@/stores/authStore';

export function ProtectedRoute() {
  const location = useLocation();
  const { user, checked, loading, checkSession } = useAuthStore();

  useEffect(() => {
    if (!checked) {
      void checkSession();
    }
  }, [checked, checkSession]);

  if (!checked || loading) {
    return (
      <div style={{ display: 'grid', placeItems: 'center', minHeight: '60vh' }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
