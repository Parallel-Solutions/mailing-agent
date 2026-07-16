import { api } from './client';
import type { Profile } from './types';

export const profileApi = {
  get: () => api.get<Profile>('/api/v1/profile'),
  update: (body: Partial<Profile>) => api.patch<Profile>('/api/v1/profile', body),
};
