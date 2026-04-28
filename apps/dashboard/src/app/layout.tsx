import type { Metadata } from "next";
import "./globals.css";
import "@xyflow/react/dist/style.css";
import { Sidebar } from "@/components/sidebar";
import { ActiveProjectProvider } from "@/lib/active-project";
import { QueryProvider } from "@/lib/query-client";

export const metadata: Metadata = {
  title: "DAP Dashboard",
  description: "Deterministic Agent Pipeline — runs, agents, pipelines",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full">
      <body className="h-full font-sans antialiased">
        <QueryProvider>
          <ActiveProjectProvider>
            <div className="flex h-full">
              <Sidebar />
              <main className="flex-1 overflow-auto bg-muted/30">{children}</main>
            </div>
          </ActiveProjectProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
