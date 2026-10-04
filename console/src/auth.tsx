import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, SIGNED_OUT_EVENT, api, errorMessage } from "./lib/api";

export type Role = "viewer" | "analyst" | "admin";
export type ConsoleUser = { id: string; username: string; role: Role };

const ROLE_RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };

export function hasRole(user: ConsoleUser, required: Role) {
  return ROLE_RANK[user.role] >= ROLE_RANK[required];
}

type SessionState =
  | { status: "loading" }
  | { status: "signed-out"; expired: boolean }
  | { status: "signed-in"; user: ConsoleUser }
  | { status: "error"; message: string };

type Auth = {
  session: SessionState;
  retry: () => void;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>({ status: "loading" });

  const load = useCallback(async () => {
    setSession({ status: "loading" });
    try {
      const { user } = await api<{ user: ConsoleUser }>("/console/session");
      setSession({ status: "signed-in", user });
    } catch (error) {
      setSession(error instanceof ApiError && error.status === 401
        ? { status: "signed-out", expired: false }
        : { status: "error", message: errorMessage(error) });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const expire = () => setSession({ status: "signed-out", expired: true });
    window.addEventListener(SIGNED_OUT_EVENT, expire);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, expire);
  }, []);

  const auth = useMemo<Auth>(() => ({
    session,
    retry: () => void load(),
    signIn: async (username, password) => {
      const { user } = await api<{ user: ConsoleUser }>("/console/session", { method: "POST", body: { username, password } });
      setSession({ status: "signed-in", user });
    },
    signOut: async () => {
      try {
        await api("/console/session", { method: "DELETE" });
      } catch {
        // Unreachable gateway: the session cookie still expires on its own.
      }
      setSession({ status: "signed-out", expired: false });
    },
  }), [session, load]);

  return <AuthContext value={auth}>{children}</AuthContext>;
}

export function useAuth(): Auth {
  const auth = use(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside AuthProvider");
  return auth;
}

/** The signed-in user. Only for components rendered behind RequireSession. */
export function useUser(): ConsoleUser {
  const { session } = useAuth();
  if (session.status !== "signed-in") throw new Error("useUser needs a signed-in session");
  return session.user;
}
