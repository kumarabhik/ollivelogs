import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

/**
 * Brutal button — every variant gets the hard teal border + offset orange
 * shadow, "presses down" on focus/active via translate-y.
 */
export const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 font-semibold transition-all duration-100",
    "border-2 border-foreground",
    "shadow-brutal-sm",
    "hover:-translate-y-px hover:shadow-brutal",
    "active:translate-y-1 active:shadow-brutal-pressed",
    "focus-visible:translate-y-1 focus-visible:shadow-brutal-pressed focus-visible:outline-none",
    "disabled:pointer-events-none disabled:opacity-50 disabled:shadow-brutal-pressed",
  ],
  {
    variants: {
      variant: {
        default: "bg-surface text-foreground",
        primary: "bg-primary text-primary-foreground",
        secondary: "bg-secondary text-secondary-foreground",
        accent: "bg-accent text-accent-foreground",
        ghost:
          "border-transparent shadow-none hover:translate-y-0 hover:bg-muted hover:shadow-none active:bg-muted active:shadow-none focus-visible:translate-y-0 focus-visible:shadow-none",
        outline: "bg-transparent text-foreground",
        destructive: "bg-destructive text-destructive-foreground",
      },
      size: {
        default: "h-10 px-4 text-sm rounded-brutal",
        sm: "h-8 px-3 text-xs rounded-md",
        lg: "h-12 px-5 text-base rounded-brutal",
        icon: "h-10 w-10 rounded-brutal",
        "icon-sm": "h-8 w-8 rounded-md",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        className={cn(buttonVariants({ variant, size }), className)}
        ref={ref}
        {...props}
      />
    );
  },
);

Button.displayName = "Button";
