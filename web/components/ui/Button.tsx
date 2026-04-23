import { ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "ghost" | "subtle" | "danger";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const base =
  "inline-flex items-center justify-center gap-2 rounded-sm font-medium tracking-tight " +
  "transition-all duration-150 ease-out disabled:cursor-not-allowed disabled:opacity-40 " +
  "focus-visible:outline-none";

const variantStyles: Record<Variant, string> = {
  primary:
    "bg-cyan text-bg hover:bg-cyan/90 shadow-[0_0_20px_-4px_rgba(0,229,255,0.5)]",
  ghost:
    "border border-border bg-transparent text-ink hover:border-border-strong hover:bg-card/60",
  subtle:
    "bg-card text-ink-dim hover:bg-surface hover:text-ink",
  danger:
    "bg-rose/15 text-rose border border-rose/30 hover:bg-rose/25",
};

const sizeStyles: Record<Size, string> = {
  sm: "h-7 px-3 text-11 uppercase tracking-wider",
  md: "h-9 px-4 text-12",
  lg: "h-11 px-5 text-13",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", size = "md", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      className={cn(base, variantStyles[variant], sizeStyles[size], className)}
      {...rest}
    />
  );
});
