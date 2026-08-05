import { api } from './client';
import type { CompanyAccess } from './types';

export type AdminUserRole = 'admin' | 'company_admin' | 'user';

export type AdminUser = {
  username: string;
  role: AdminUserRole;
  tenant_id?: string;
  created_at?: string;
  company_accesses: CompanyAccess[];
};

export type AdminUserCreate = {
  username: string;
  password: string;
  password_confirm?: string;
  role: AdminUserRole;
  company_accesses: Array<Pick<CompanyAccess, 'company_id' | 'access_level'>>;
};

export type AdminUserUpdate = {
  role?: AdminUserRole;
  company_accesses?: Array<Pick<CompanyAccess, 'company_id' | 'access_level'>>;
};

export const adminUsersApi = {
  list: () => api.get<{ items: AdminUser[]; total: number }>('/api/admin/users'),
  create: (body: AdminUserCreate) =>
    api.post<{ user: AdminUser }>('/api/admin/users', body),
  update: (username: string, body: AdminUserUpdate) =>
    api.patch<{ user: AdminUser }>(
      `/api/admin/users/${encodeURIComponent(username)}`,
      body,
    ),
};
