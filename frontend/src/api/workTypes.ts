import { api } from './client';

export type WorkTypeOption = {
  key: string;
  name: string;
  mail_subject: string;
  is_system: boolean;
};

export const workTypesApi = {
  list: () => api.get<WorkTypeOption[]>('/api/v1/work-types'),
  create: (body: { name: string; mail_subject: string }) =>
    api.post<WorkTypeOption>('/api/v1/work-types', body),
};
