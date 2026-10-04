import { Link, NavLink } from "react-router";
import { CONSOLE_PAGES } from "../pages";
import { BrandMark } from "./BrandMark";
import { Icon } from "./Icon";
import { Button } from "./ui/Button";

type SidebarProps = { collapsed: boolean; onToggle: () => void };

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  return (
    <aside className="sidebar">
      <Link className="brand" to="/metrics" aria-label="IntentLatch Console">
        <BrandMark />
        {!collapsed && <span><strong>IntentLatch</strong><span className="brand-caption">Console</span></span>}
      </Link>
      <nav id="console-navigation" aria-label="Console" className="sidebar-nav">
        {(["Monitor", "Govern", "Assure"] as const).map((group) => (
          <div className="nav-group" key={group}>
            <p className={collapsed ? "sr-only" : "nav-caption"}>{group}</p>
            {CONSOLE_PAGES.filter((page) => page.group === group).map((page) => (
              <NavLink key={page.path} to={page.path} end className="nav-link" aria-label={page.title} title={collapsed ? page.title : undefined}>
                <Icon name={page.icon} />
                {!collapsed && <span>{page.title}</span>}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <Button variant="ghost" className="collapse-button" onClick={onToggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} aria-controls="console-navigation" title={collapsed ? "Expand sidebar" : undefined}>
          <Icon name={collapsed ? "expand" : "collapse"} />
          {!collapsed && <span>Collapse sidebar</span>}
        </Button>
      </div>
    </aside>
  );
}
