import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { ConsoleLayout } from "./components/ConsoleLayout";
import { CONSOLE_PAGES } from "./pages";
import { NotFoundPage } from "./pages/NotFoundPage";
import { LoginPage } from "./pages/LoginPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { useTheme } from "./useTheme";

export function App() {
  const themeControl = useTheme();
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage {...themeControl} />} />
        <Route element={<ConsoleLayout {...themeControl} />}>
          <Route index element={<Navigate to="/metrics" replace />} />
          {CONSOLE_PAGES.map((page) => (
            <Route key={page.path} path={page.path} element={<PlaceholderPage page={page} />} />
          ))}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
