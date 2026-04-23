"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

type Status = "ok" | "warn" | "crit";

interface HeaderProps {
  status?: Status;
  dryRun?: boolean;
}

const statusStyles: Record<Status, string> = {
  ok: "bg-cyan shadow-[0_0_12px_0_rgba(0,229,255,0.8)]",
  warn: "bg-amber shadow-[0_0_12px_0_rgba(255,176,32,0.7)]",
  crit: "bg-rose shadow-[0_0_12px_0_rgba(255,59,92,0.8)]",
};

const statusLabel: Record<Status, string> = {
  ok: "Healthy",
  warn: "Degraded",
  crit: "Halted",
};

/** App header — sticky, 56px, subtle backdrop blur. */
export function Header({ status = "ok", dryRun = true }: HeaderProps) {
  const [now, setNow] = useState<string>("");

  useEffect(() => {
    const tick = () =>
      setNow(
        new Date().toLocaleTimeString("en-AU", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
          timeZone: "Australia/Sydney",
        }),
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 h-[56px] w-full",
        "border-b border-border",
        "bg-bg/70 backdrop-blur-md backdrop-saturate-150",
      )}
    >
      <div className="mx-auto flex h-full max-w-[1400px] items-center justify-between px-6">
        {/* Left: wordmark */}
        <div className="flex items-center gap-3">
          <LogoMark />
          <div className="flex items-baseline gap-2 text-13 tracking-tight">
            <span className="font-semibold text-ink">Sigenergy</span>
            <span className="text-ink-faint">·</span>
            <span className="font-mono text-ink-dim tracking-wider">NEM</span>
            <span className="text-ink-dim">Arbitrage</span>
          </div>
        </div>

        {/* Right: status + dry-run + clock */}
        <div className="flex items-center gap-4">
          {dryRun && (
            <span
              className={cn(
                "flex items-center gap-2 rounded-xs px-2 py-1",
                "border border-amber/30 bg-amber/10",
                "text-11 font-medium uppercase tracking-wider text-amber",
              )}
            >
              <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-amber" />
              Dry Run
            </span>
          )}
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "relative inline-block h-2 w-2 rounded-full",
                statusStyles[status],
              )}
            >
              <span
                className={cn(
                  "absolute inset-0 animate-pulse-soft rounded-full",
                  statusStyles[status],
                  "opacity-60",
                )}
              />
            </span>
            <span className="text-12 text-ink-dim">{statusLabel[status]}</span>
          </div>
          <div className="hairline-v h-4 w-px bg-border" />
          <time className="num text-13 text-ink-dim" suppressHydrationWarning>
            {now || "--:--:--"}
          </time>
        </div>
      </div>
    </header>
  );
}

/** Tiny SVG mark — a battery cell with a spark. */
function LogoMark() {
  return (
    <svg
      width="22"
      height="22"
      viewBox="0 0 22 22"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <rect
        x="2.5"
        y="5.5"
        width="14"
        height="11"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.25"
        className="text-ink-dim"
      />
      <rect x="17" y="8.5" width="2.5" height="5" rx="0.8" fill="currentColor" className="text-ink-dim" />
      <path
        d="M8.5 8.5 L6 11.5 H9 L7.5 14"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-cyan"
      />
    </svg>
  );
}
