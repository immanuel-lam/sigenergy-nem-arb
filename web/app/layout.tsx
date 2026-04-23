import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Header } from "@/components/layout/Header";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: "Sigenergy · NEM Arbitrage",
  description:
    "Autonomous battery arbitrage agent. Re-plans every 30 minutes against AEMO wholesale prices.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const dryRun = process.env.DRY_RUN !== "false";

  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} ${mono.variable} min-h-screen bg-bg font-sans text-ink antialiased`}
      >
        {/* Atmosphere layer — drifts behind everything */}
        <div
          aria-hidden
          className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
        >
          <div className="atmosphere-grid" />
          <div className="atmosphere-blob atmosphere-blob-1" />
          <div className="atmosphere-blob atmosphere-blob-2" />
        </div>

        <Header dryRun={dryRun} />

        <main className="mx-auto max-w-[1400px] px-6 py-6">{children}</main>
      </body>
    </html>
  );
}
