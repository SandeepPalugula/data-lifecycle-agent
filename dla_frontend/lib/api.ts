/**
 * lib/api.ts
 * Central API client. All HTTP calls to the FastAPI backend go through here.
 * Why: keeps all endpoint URLs in one place — if the backend URL changes,
 * you only update it here, not across every page.
 */

import axios from "axios";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Create a configured axios instance
export const api = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
});

// Automatically attach JWT token to every request
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("dla_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

// If the server returns 401, clear the token and redirect to login
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      localStorage.removeItem("dla_token");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

// ── Auth ──────────────────────────────────────────────────────
export const authApi = {
  login: (email: string, password: string) =>
    api.post("/auth/login", new URLSearchParams({ username: email, password }), {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    }),
  register: (email: string, role: string) =>
    api.post("/auth/register", { email, role }),
  me: () => api.get("/auth/me"),
};

// ── Conversations ─────────────────────────────────────────────
export const conversationsApi = {
  list: (page = 1, size = 20, state?: string) =>
    api.get("/conversations", { params: { page, size, state } }),
  get: (id: string) => api.get(`/conversations/${id}`),
  register: (data: { external_id: string; size_bytes: number; token_count: number }) =>
    api.post("/conversations", data),
};

// ── Scheduler ─────────────────────────────────────────────────
export const schedulerApi = {
  triggerRun: () => api.post("/scheduler/run"),
  listRuns: () => api.get("/scheduler/runs"),
  getRun: (id: string) => api.get(`/scheduler/runs/${id}`),
};

// ── Decisions ─────────────────────────────────────────────────
export const decisionsApi = {
  list: (pendingOnly = false) =>
    api.get("/decisions", { params: { pending_only: pendingOnly } }),
  get: (id: string) => api.get(`/decisions/${id}`),
  confirm: (id: string) => api.post(`/decisions/${id}/confirm`),
  reject: (id: string) => api.post(`/decisions/${id}/reject`),
  getBatchForecast: () => api.get("/decisions/batch-forecast"),
};

// ── Audit ─────────────────────────────────────────────────────
export const auditApi = {
  list: (page = 1, size = 50) =>
    api.get("/audit", { params: { page, size } }),
};

// ── Costs ─────────────────────────────────────────────────────
export const costsApi = {
  latest: () => api.get("/costs/latest"),
  history: () => api.get("/costs/history"),
};
