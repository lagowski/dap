import type { Metadata } from "next";
import "./globals.css";
import "@xyflow/react/dist/style.css";
import { QueryProvider } from "@/lib/query-client";

export const metadata: Metadata = {
  title: "DAP Dashboard",
  description: "Deterministic Agent Pipeline — runs, agents, pipelines",
};

/**
 * Root layout is intentionally minimal — providers + shell HTML only.
 * The full app chrome (sidebar, active-project context) lives under
 * the ``(app)`` route group so the ``(auth)`` group can render
 * without it (#300 sub-B2).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full font-sans antialiased">
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
