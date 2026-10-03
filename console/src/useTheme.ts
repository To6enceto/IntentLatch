import { useLayoutEffect, useState } from "react";

export type Theme = "dark" | "light";
const STORAGE_KEY = "intentlatch.theme";

function storageUnavailable(error: unknown) {
  return error instanceof DOMException &&
    (error.name === "SecurityError" || error.name === "QuotaExceededError");
}

function loadTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch (error) {
    if (!storageUnavailable(error)) throw error;
    return "dark";
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(loadTheme);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content", theme === "dark" ? "#0B0F16" : "#F3F5F8",
    );
  }, [theme]);

  function toggleTheme() {
    const nextTheme = theme === "dark" ? "light" : "dark";
    setTheme(nextTheme);
    try {
      localStorage.setItem(STORAGE_KEY, nextTheme);
    } catch (error) {
      if (!storageUnavailable(error)) throw error;
    }
  }

  return { theme, toggleTheme };
}
