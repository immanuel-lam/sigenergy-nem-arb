"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";

import { Badge, actionVariant } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";
import { type RationaleEntry, useRationale } from "@/hooks/useLiveData";
import { fmtTime } from "@/lib/utils";

const PREVIEW = 200;

export function RationaleFeed() {
  const { data, isLoading, error } = useRationale(5);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  // Newest first.
  const entries = ((data ?? []) as RationaleEntry[])
    .slice()
    .reverse()
    .slice(0, 5);

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        eyebrow="Opus 4.7"
        title="Rationale feed"
        right={
          <span className="flex items-center gap-1.5 text-11 uppercase tracking-wider text-ink-faint">
            <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-violet" />
            Live
          </span>
        }
      />

      {isLoading && !data ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : error ? (
        <div className="flex flex-1 items-center justify-center py-6">
          <div className="text-center">
            <div className="text-12 text-amber">Data source unreachable</div>
            <div className="num mt-1 text-11 text-ink-faint">/rationale</div>
          </div>
        </div>
      ) : entries.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-6 text-12 text-ink-faint">
          No rationale logged yet.
        </div>
      ) : (
        <ul className="flex-1 space-y-2 overflow-y-auto pr-1">
          <AnimatePresence initial={false}>
            {entries.map((e, i) => {
              const key = `${e.timestamp ?? "t"}-${i}`;
              const isLong = e.rationale.length > PREVIEW;
              const open = expanded[i] ?? false;
              const text =
                !isLong || open ? e.rationale : `${e.rationale.slice(0, PREVIEW)}…`;
              return (
                <motion.li
                  key={key}
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{
                    duration: 0.35,
                    delay: i === 0 ? 0 : Math.min(i * 0.04, 0.12),
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  className="rounded-sm border border-border bg-surface/50 px-3 py-2"
                >
                  <div className="mb-1 flex items-center gap-2">
                    <span className="num text-11 text-ink-faint">
                      {e.timestamp ? fmtTime(e.timestamp) : "--:--"}
                    </span>
                    {e.action && (
                      <Badge variant={actionVariant(e.action)}>
                        {e.action.replace("_", " ")}
                      </Badge>
                    )}
                  </div>
                  <div className="text-12 leading-relaxed text-ink-dim">
                    {text}
                    {isLong && (
                      <button
                        onClick={() =>
                          setExpanded((s) => ({ ...s, [i]: !open }))
                        }
                        className="ml-1 text-cyan hover:underline"
                      >
                        {open ? "less" : "more"}
                      </button>
                    )}
                  </div>
                </motion.li>
              );
            })}
          </AnimatePresence>
        </ul>
      )}
    </Card>
  );
}
