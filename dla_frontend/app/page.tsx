/**
 * app/page.tsx
 * Root page — immediately redirects to /dashboard.
 * Why: the app's home is the dashboard. If not logged in,
 * the dashboard layout will redirect to /login automatically.
 */

import { redirect } from "next/navigation";

export default function Home() {
  redirect("/dashboard");
}
