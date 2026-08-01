"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export type ModelOption = { group: string; id: string; label: string };

// Anthropic labels come back from the catalog as "Claude Opus 5" etc. — drop
// the "Claude " prefix so the trigger button reads like Claude.ai's own
// compact model switcher ("Opus 5" rather than "Claude Opus 5").
function shortLabel(label: string): string {
  return label.replace(/^Claude\s+/, "");
}

export default function ModelPicker({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ModelOption[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const current = models.find((m) => m.id === value);
  const groups = Array.from(new Set(models.map((m) => m.group)));

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-slate-400 hover:bg-slate-800 hover:text-slate-100 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <span className="max-w-[9rem] truncate">
          {current ? shortLabel(current.label) : "Model"}
        </span>
        <ChevronDown size={13} className="flex-shrink-0" />
      </button>

      {/* Opens downward: the trigger sits on the composer's bottom row, but the
          composer itself is at the top of the page — opening upward would put the
          panel above the viewport. */}
      {open && (
        <div className="absolute right-0 top-full mt-2 w-72 bg-slate-900 border border-slate-700 rounded-xl shadow-xl py-2 z-20">
          {current && (
            <>
              <div className="flex items-center justify-between px-3 py-2">
                <div>
                  <p className="text-sm font-medium text-slate-100">{shortLabel(current.label)}</p>
                  <p className="text-xs text-slate-500">{current.group}</p>
                </div>
                <Check size={16} className="text-blue-400 flex-shrink-0" />
              </div>
              <div className="border-t border-slate-800 my-1" />
            </>
          )}

          <div className="max-h-72 overflow-y-auto">
            {groups.map((group) => (
              <div key={group}>
                <p className="px-3 pt-2 pb-1 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                  {group}
                </p>
                {models
                  .filter((m) => m.group === group)
                  .map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => {
                        onChange(m.id);
                        setOpen(false);
                      }}
                      className={`w-full text-left px-3 py-2 text-sm rounded-md transition-colors ${
                        m.id === value
                          ? "text-blue-400"
                          : "text-slate-300 hover:bg-slate-800"
                      }`}
                    >
                      {m.label}
                    </button>
                  ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
