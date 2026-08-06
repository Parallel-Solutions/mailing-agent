import { useAuthStore } from '@/stores/authStore';

export function usePermissions() {
  const user = useAuthStore((s) => s.user);
  const isAppAdmin = user?.role === 'admin';
  const companyAccesses = user?.company_accesses ?? [];
  const isCompanyAdmin =
    user?.role === 'company_admin' ||
    user?.company_role === 'company_admin' ||
    companyAccesses.some((item) => item.access_level === 'manage');
  const hasCompany = Boolean(user?.company_id);
  const canAccessCompanies = isAppAdmin || companyAccesses.length > 0 || isCompanyAdmin;
  const canManageCompanies =
    isAppAdmin || companyAccesses.some((item) => item.access_level === 'manage') || isCompanyAdmin;
  const canViewCompany = (companyId: string) =>
    isAppAdmin ||
    user?.company_id === companyId ||
    companyAccesses.some((item) => item.company_id === companyId);
  const canManageCompany = (companyId: string) =>
    isAppAdmin ||
    (user?.company_id === companyId && user?.company_role === 'company_admin') ||
    companyAccesses.some(
      (item) => item.company_id === companyId && item.access_level === 'manage',
    );
  return {
    user,
    isAppAdmin,
    isCompanyAdmin,
    hasCompany,
    companyAccesses,
    canAccessCompanies,
    canManageCompanies,
    canViewCompany,
    canManageCompany,
  };
}
