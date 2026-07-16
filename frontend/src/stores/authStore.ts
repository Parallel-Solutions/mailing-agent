import { create } from 'zustand';
import * as authApi from '@/api/auth';
import type { User } from '@/api/types';

type AuthState = {
  user: User | null;
  loading: boolean;
  checked: boolean;
  setUser: (user: User | null) => void;
  checkSession: () => Promise<boolean>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, confirm: string) => Promise<void>;
  logout: () => Promise<void>;
};

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: false,
  checked: false,
  setUser: (user) => set({ user }),
  checkSession: async () => {
    set({ loading: true });
    try {
      const result = await authApi.me();
      set({ user: result.user, checked: true, loading: false });
      return true;
    } catch {
      set({ user: null, checked: true, loading: false });
      return false;
    }
  },
  login: async (username, password) => {
    const result = await authApi.login(username, password);
    set({ user: result.user, checked: true });
  },
  register: async (username, password, confirm) => {
    const result = await authApi.register(username, password, confirm);
    set({ user: result.user, checked: true });
  },
  logout: async () => {
    await authApi.logout();
    set({ user: null });
  },
}));
