"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";

const ThemeContext = createContext<{
  theme: Theme;
  setTheme: (t: Theme) => void;
}>({ theme: "system", setTheme: () => {} });

export function useTheme() {
  return useContext(ThemeContext);
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isLight = theme === "light" || (theme === "system" && !prefersDark);
  root.classList.toggle("light", isLight);
  root.classList.toggle("dark", !isLight);
}

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>("system");

  // On mount: read stored preference and apply
  useEffect(() => {
    const stored = (localStorage.getItem("theme") as Theme) || "system";
    setTheme(stored);
    applyTheme(stored);
  }, []);

  // When system theme is active, respond to OS changes
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => applyTheme("system");
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);

  const handleSetTheme = (t: Theme) => {
    setTheme(t);
    localStorage.setItem("theme", t);
    applyTheme(t);
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme: handleSetTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

/**
 * Inline script for <head> to prevent flash of wrong theme.
 * Must be rendered as a server component or in <head>.
 */
export function ThemeScript() {
  const script = `(function(){
    var t=localStorage.getItem('theme')||'system';
    var d=window.matchMedia('(prefers-color-scheme: dark)').matches;
    var light=t==='light'||(t==='system'&&!d);
    document.documentElement.classList.add(light?'light':'dark');
  })();`;
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}
