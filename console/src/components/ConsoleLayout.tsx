import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { CONSOLE_PAGES } from "../pages";
import { Icon } from "./Icon";
import { Sidebar } from "./Sidebar";
import { ThemeToggle, type ThemeControlProps } from "./ThemeToggle";
import { buttonVariants } from "./ui/Button";

export function ConsoleLayout(themeControl: ThemeControlProps) {
  const [collapsed, setCollapsed] = useState(false);
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);
  const previousPath = useRef(pathname);
  const title = CONSOLE_PAGES.find((page) => page.path === pathname)?.title ?? "Page not found";

  useEffect(() => {
    document.title = `${title} | IntentLatch Console`;
    if (previousPath.current !== pathname) mainRef.current?.focus();
    previousPath.current = pathname;
  }, [pathname, title]);

  return (
    <div className={`console-shell${collapsed ? " sidebar-collapsed" : ""}`}>
      <a href="#main-content" className="skip-link">Skip to content</a>
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />
      <div className="console-workspace">
        <header className="console-header">
          <p className="header-location"><span>Console</span><span aria-hidden="true">/</span><span>{title}</span></p>
          <div className="header-actions">
            <ThemeToggle {...themeControl} />
            <Link to="/login" className={buttonVariants({ variant: "outline" })}><Icon name="login" />Sign in</Link>
          </div>
        </header>
        <main id="main-content" className="console-content" ref={mainRef} tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
