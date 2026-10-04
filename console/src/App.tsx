import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AuthProvider } from "./auth";
import { ConsoleLayout } from "./components/ConsoleLayout";
import { RequireSession } from "./components/RequireSession";
import { CONSOLE_PAGES } from "./pages";
import { NotFoundPage } from "./pages/NotFoundPage";
import { LoginPage } from "./pages/LoginPage";
import { MetricsPage } from "./pages/metrics/MetricsPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { PoliciesPage } from "./pages/policies/PoliciesPage";
import { ReportsPage } from "./pages/reports/ReportsPage";
import { TeamsPage } from "./pages/teams/TeamsPage";
import { TestsPage } from "./pages/tests/TestsPage";
import { useTheme } from "./useTheme";

const PAGE_ELEMENTS: Record<string, ReactNode> = {
  "/metrics": <MetricsPage />,
  "/teams": <TeamsPage />,
  "/policies": <PoliciesPage />,
  "/tests": <TestsPage />,
  "/reports": <ReportsPage />,
};

export function App() {
  const themeControl = useTheme();
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage {...themeControl} />} />
          <Route element={<RequireSession><ConsoleLayout {...themeControl} /></RequireSession>}>
            <Route index element={<Navigate to="/metrics" replace />} />
            {CONSOLE_PAGES.map((page) => (
              <Route key={page.path} path={page.path} element={PAGE_ELEMENTS[page.path] ?? <PlaceholderPage page={page} />} />
            ))}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
