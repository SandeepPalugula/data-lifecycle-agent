/**
 * app/layout.tsx
 * Root layout — wraps every page in the app.
 * Why: Next.js App Router requires a root layout. This is where we set
 * the HTML metadata (title, description) and import global CSS.
 * Everything inside {children} is the actual page content.
 */

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DLA — Data Lifecycle Agent",
  description: "Cost-aware conversation storage optimisation dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
