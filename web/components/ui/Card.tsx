import { HTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";

type Variant = "default" | "glow" | "subtle";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  glow?: boolean;
  variant?: Variant;
  padded?: boolean;
}

/**
 * Card primitive — dark card with hairline border. Pass `glow` for the
 * cyan edge used on the agent's row in the backtest table.
 */
export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { className, glow = false, variant = "default", padded = true, ...rest },
  ref,
) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-md border border-border bg-card",
        padded && "p-4",
        variant === "subtle" && "bg-card/60",
        glow && "shadow-glow-cyan border-cyan/30",
        className,
      )}
      {...rest}
    />
  );
});

export function CardHeader({
  title,
  eyebrow,
  right,
  className,
}: {
  title: string;
  eyebrow?: string;
  right?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex items-start justify-between gap-3", className)}>
      <div>
        {eyebrow && (
          <div className="mb-1 text-11 uppercase tracking-widest text-ink-faint">
            {eyebrow}
          </div>
        )}
        <div className="text-13 font-medium tracking-tight text-ink">
          {title}
        </div>
      </div>
      {right}
    </div>
  );
}
