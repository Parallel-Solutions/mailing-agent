import { api } from './client';
import type { User } from './types';

export async function login(username: string, password: string) {
  return api.post<{ user: User }>('/api/auth/login', { username, password });
}

export async function register(username: string, password: string, password_confirm: string) {
  return api.post<{ user: User }>('/api/auth/register', {
    username,
    password,
    password_confirm,
  });
}

export async function logout() {
  return api.post<unknown>('/api/auth/logout');
}

export async function me() {
  return api.get<{ user: User }>('/api/auth/me');
}
