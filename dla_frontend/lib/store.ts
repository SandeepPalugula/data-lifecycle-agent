/**
 * lib/store.ts
 * Global auth state using Zustand.
 * Why Zustand: it's simpler than Redux but works across all pages without
 * wrapping the app in a Provider. The token and user info persist in
 * localStorage so refreshing the page doesn't log you out.
 */

import { create } from "zustand";

interface User {
  id: string;
  email: string;
  role: string;
}

interface AuthStore {
  token: string | null;
  user: User | null;
  setAuth: (token: string, user: User) => void;
  logout: () => void;
  isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthStore>((set, get) => ({
  token: typeof window !== "undefined" ? localStorage.getItem("dla_token") : null,
  user: typeof window !== "undefined"
    ? JSON.parse(localStorage.getItem("dla_user") || "null")
    : null,

  setAuth: (token, user) => {
    localStorage.setItem("dla_token", token);
    localStorage.setItem("dla_user", JSON.stringify(user));
    set({ token, user });
  },

  logout: () => {
    localStorage.removeItem("dla_token");
    localStorage.removeItem("dla_user");
    set({ token: null, user: null });
  },

  isAuthenticated: () => !!get().token,
}));
