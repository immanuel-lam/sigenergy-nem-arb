import { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

interface StatPillProps extends HTMLAttributes<HTMLDivElement> {
  label: string;
  value: string | number;
  unit?: string;
  tone?: "default" | "cyan" | "amber" | "rose" | "violet";
}

const toneStyles = {
  default: "text-ink",
  cyan: "text-cyan",
  amber: "text-amber",
  rose: "text-rose",
  violet: "text-violet",
};

/**
 * Tiny label + mono value row. Used anywhere we stack small readouts.
 */
export function StatPill({
  label,
  value,
  unit,
  tone = "default",
  className,
  ...rest
}: StatPillProps) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-3 py-1.5",
        className,
      )}
      {...rest}
    >
      <span className="text-11 uppercase tracking-wider text-ink-faint">
        {label}
      </span>
      <span className={cn("num text-13 tabular-nums", toneStyles[tone])}>
        {value}
        {unit && (
          <span className="ml-1 text-11 text-ink-faint">{unit}</span>
        )}
      </span>
    </div>
  );
}
