import { api } from './client';
import type { Company } from './types';

export type CompanyWorkType = {
  id: string;
  name: string;
};

export const companiesApi = {
  getMe: () => api.get<Company | null>('/api/v1/companies/me'),
  list: () => api.get<{ items: Company[]; total: number }>('/api/v1/companies'),
  create: (body: { name: string; phone?: string; contact_person_name?: string }) =>
    api.post<Company>('/api/v1/companies', body),
  update: (id: string, body: Partial<{ name: string; phone: string; contact_person_name: string }>) =>
    api.patch<Company>(`/api/v1/companies/${id}`, body),
  uploadLogo: async (id: string, file: File) => {
    const form = new FormData();
    form.append('file', file);
    const response = await fetch(`/api/v1/companies/${id}/logo`, {
      method: 'POST',
      body: form,
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось загрузить логотип');
    }
    const payload = await response.json();
    return payload.result as Company;
  },
  deleteLogo: (id: string) => api.delete<Company>(`/api/v1/companies/${id}/logo`),
  workTypes: {
    list: (companyId: string) => api.get<CompanyWorkType[]>(`/api/v1/companies/${companyId}/work-types`),
    create: (companyId: string, body: { name: string }) =>
      api.post<CompanyWorkType>(`/api/v1/companies/${companyId}/work-types`, body),
    update: (companyId: string, workTypeId: string, body: { name: string }) =>
      api.patch<CompanyWorkType>(`/api/v1/companies/${companyId}/work-types/${workTypeId}`, body),
    remove: (companyId: string, workTypeId: string) =>
      api.delete<{ removed: boolean }>(`/api/v1/companies/${companyId}/work-types/${workTypeId}`),
  },
};
