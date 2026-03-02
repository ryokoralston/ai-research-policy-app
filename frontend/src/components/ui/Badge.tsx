import { clsx } from "clsx";

const VARIANTS = {
  default: "bg-slate-700 text-slate-300",
  blue: "bg-blue-900/50 text-blue-300",
  green: "bg-green-900/50 text-green-300",
  amber: "bg-amber-900/50 text-amber-300",
  red: "bg-red-900/50 text-red-300",
} as const;

interface BadgeProps {
  children: React.ReactNode;
  variant?: keyof typeof VARIANTS;
  className?: string;
}

export default function Badge({ children, variant = "default", className }: BadgeProps) {
  return (
    <span
      className={clsx(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium",
        VARIANTS[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
