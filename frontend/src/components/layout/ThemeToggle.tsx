"use client";

import { Monitor, Sun, Moon } from "lucide-react";
import { useTheme, type Theme } from "./ThemeProvider";

const OPTIONS: { value: Theme; icon: React.ElementType; label: string }[] = [
  { value: "system", icon: Monitor, label: "System" },
  { value: "light",  icon: Sun,     label: "Light" },
  { value: "dark",   icon: Moon,    label: "Dark" },
];

export default function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <div className="inline-flex items-center gap-0.5 bg-slate-800 rounded-md p-0.5">
      {OPTIONS.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          onClick={() => setTheme(value)}
          title={label}
          className={`p-1.5 rounded transition-colors ${
            theme === value
              ? "bg-slate-600 text-slate-100"
              : "text-slate-500 hover:text-slate-300"
          }`}
        >
          <Icon size={13} />
        </button>
      ))}
    </div>
  );
}
