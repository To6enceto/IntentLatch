import type { Theme } from "../useTheme";
import { Icon } from "./Icon";
import { Button } from "./ui/Button";

export type ThemeControlProps = { theme: Theme; toggleTheme: () => void };

export function ThemeToggle({ theme, toggleTheme }: ThemeControlProps) {
  const nextTheme = theme === "dark" ? "light" : "dark";
  return (
    <Button variant="outline" onClick={toggleTheme} aria-label={`Switch to ${nextTheme} theme`}>
      <Icon name={theme === "dark" ? "sun" : "moon"} />
      <span>{nextTheme === "light" ? "Light" : "Dark"} theme</span>
    </Button>
  );
}
