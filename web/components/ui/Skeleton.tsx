import { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** Subtle shimmer rectangle for initial loads. */
export function Skeleton({
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-sm bg-surface/80",
        "before:absolute before:inset-0",
        "before:-translate-x-full",
        "before:bg-gradient-to-r before:from-transparent before:via-white/[0.04] before:to-transparent",
        "before:animate-[shimmer_1.8s_infinite]",
        className,
      )}
      {...rest}
    />
  );
}
