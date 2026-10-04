import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";
import { useAuth } from "../auth";
import { BrandMark } from "./BrandMark";
import { Icon } from "./Icon";
import { Button } from "./ui/Button";

export function RequireSession({ children }: { children: ReactNode }) {
  const { session, retry } = useAuth();
  const location = useLocation();

  if (session.status === "signed-in") return children;
  if (session.status === "signed-out") {
    return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  }
  return (
    <main className="session-screen">
      <BrandMark large />
      {session.status === "loading" ? (
        <p role="status">Checking your session…</p>
      ) : (
        <div role="alert" className="session-error">
          <h1>The console cannot reach the gateway</h1>
          <p>{session.message}</p>
          <Button variant="outline" onClick={retry}><Icon name="refresh" />Try again</Button>
        </div>
      )}
    </main>
  );
}
