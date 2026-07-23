import type { Metadata } from "next";
import "./globals.css";
import { Rail } from "@/components/rail";

export const metadata: Metadata = {
  title: "Studio",
  description: "Idea to published video.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        {/* Left rail + content. No top nav bar, no breadcrumbs — the hierarchy is
            one level deep everywhere, so neither earns its pixels. */}
        <div className="flex min-h-screen">
          <Rail />
          <main className="flex-1 min-w-0">{children}</main>
        </div>
      </body>
    </html>
  );
}
