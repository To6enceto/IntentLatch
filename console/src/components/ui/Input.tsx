import type { ComponentProps } from "react";
import { cn } from "../../lib/utils";

export function Input({ className, type = "text", ...props }: ComponentProps<"input">) {
  return (
    <input
      data-slot="input"
      type={type}
      className={cn(
        "h-[38px] w-full min-w-0 rounded-lg border border-border-strong bg-secondary px-3 text-[13px] text-foreground placeholder:text-muted-foreground disabled:opacity-50 aria-invalid:border-destructive",
        className,
      )}
      {...props}
    />
  );
}
