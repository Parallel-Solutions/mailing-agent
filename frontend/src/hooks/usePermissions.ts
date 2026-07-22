import { useAuthStore } from '@/stores/authStore';

export function usePermissions() {
  const user = useAuthStore((s) => s.user);
  const isAppAdmin = user?.role === 'admin';
  const isCompanyAdmin = user?.company_role === 'company_admin';
  const hasCompany = Boolean(user?.company_id);
  return { user, isAppAdmin, isCompanyAdmin, hasCompany };
}
