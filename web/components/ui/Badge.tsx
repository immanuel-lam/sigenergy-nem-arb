import { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "cyan" | "amber" | "rose" | "violet" | "subtle" | "ok";

const variantStyles: Record<Variant, string> = {
  default: "border-border bg-card text-ink-dim",
  subtle: "border-border/60 bg-surface/60 text-ink-faint",
  cyan: "border-cyan/30 bg-cyan/10 text-cyan",
  amber: "border-amber/30 bg-amber/10 text-amber",
  rose: "border-rose/30 bg-rose/10 text-rose",
  violet: "border-violet/30 bg-violet/10 text-violet",
  ok: "border-cyan/30 bg-cyan/10 text-cyan",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: Variant;
  dot?: boolean;
}

/** Small pill. Use for action labels, statuses, tags. */
export function Badge({
  variant = "default",
  dot = false,
  className,
  children,
  ...rest
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-xs border px-2 py-0.5",
        "text-11 font-medium uppercase tracking-wider",
        variantStyles[variant],
        className,
      )}
      {...rest}
    >
      {dot && (
        <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      )}
      {children}
    </span>
  );
}

/** Convenience — map an action string to the right variant. */
export function actionVariant(
  action: string | null | undefined,
): Variant {
  switch (action) {
    case "CHARGE_GRID":
      return "cyan";
    case "DISCHARGE_GRID":
      return "rose";
    case "HOLD_SOLAR":
      return "violet";
    case "IDLE":
      return "subtle";
    default:
      return "default";
  }
}
