import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cx } from "./cx";

export type ButtonVariant = "create" | "primary" | "outline" | "outline-success" | "tertiary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

const base =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-medium select-none " +
  "transition-[background-color,color,transform] duration-150 active:scale-[.97] " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
  "disabled:pointer-events-none disabled:opacity-50";

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-sm rounded-medium",
  md: "h-9 px-4 text-sm rounded-large",
};

const variants: Record<ButtonVariant, string> = {
  // The one solid action of a view: create / submit.
  create: "bg-success text-white font-semibold hover:bg-success/80",
  // The one solid action when it is not a create (upload, connect): ink fill, inverts in dark mode.
  primary: "bg-primary text-primary-foreground font-semibold hover:bg-primary/85",
  outline: "border border-tertiary text-primary hover:bg-quaternary",
  "outline-success": "border border-success text-success hover:bg-success/10",
  tertiary: "bg-default text-primary hover:bg-default/70",
  ghost: "text-primary hover:bg-quaternary",
  danger: "bg-danger text-white font-semibold hover:bg-danger/80",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  loading?: boolean;
}

export function Button({
  variant = "outline",
  size = "sm",
  icon,
  loading = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(base, sizes[size], variants[variant], className)}
      {...rest}
    >
      {loading ? (
        <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
      ) : (
        icon
      )}
      {children}
    </button>
  );
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: icon-only buttons need an accessible name. */
  label: string;
  icon: ReactNode;
}

export function IconButton({ label, icon, className, type = "button", ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      className={cx(
        "inline-flex size-8 items-center justify-center rounded-medium text-secondary transition-colors duration-150",
        "hover:bg-quaternary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  );
}
