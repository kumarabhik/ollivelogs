import { forwardRef } from "react";

import { cn } from "@/lib/utils";

export const Input = forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => {
  return (
    <input
      className={cn(
        "flex h-10 w-full rounded-md border-2 border-foreground bg-input px-3 py-1.5 text-sm text-foreground outline-none shadow-brutal-sm transition-all duration-100",
        "placeholder:text-muted-foreground",
        "focus-visible:translate-y-1 focus-visible:shadow-brutal-pressed",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      ref={ref}
      {...props}
    />
  );
});

Input.displayName = "Input";
