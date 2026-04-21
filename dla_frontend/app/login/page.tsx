/**
 * app/login/page.tsx
 * Login page — the entry point for unauthenticated users.
 * Why: every protected route checks for a JWT token. If there's none,
 * the user lands here. On successful login, the token is stored in
 * localStorage and Zustand, then the user is redirected to /dashboard.
 */

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/lib/store";

export default function LoginPage() {
  const router = useRouter();
  const { setAuth } = useAuthStore();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      // Step 1: Login to get token
      const res = await authApi.login(email, password);
      const token = res.data.access_token;

      // Step 2: Fetch user details with the token
      localStorage.setItem("dla_token", token);
      const meRes = await authApi.me();

      // Step 3: Store both in Zustand + localStorage
      setAuth(token, meRes.data);
      router.push("/dashboard");
    } catch {
      setError("Invalid credentials. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-8 w-full max-w-md">
        {/* Header */}
        <div className="mb-8">
          <div className="w-10 h-10 bg-indigo-600 rounded-xl mb-4 flex items-center justify-center">
            <span className="text-white font-bold text-sm">DLA</span>
          </div>
          <h1 className="text-2xl font-semibold text-gray-900">Sign in</h1>
          <p className="text-gray-500 text-sm mt-1">Data Lifecycle Agent Dashboard</p>
        </div>

        {/* Form */}
        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="admin@gmail.com"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              placeholder="Enter any password"
              required
            />
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2 rounded-lg">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-indigo-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="text-xs text-gray-400 mt-6 text-center">
          DLA v0.1.0 — Storage Optimisation Agent
        </p>
      </div>
    </div>
  );
}
