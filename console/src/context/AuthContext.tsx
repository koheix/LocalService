import { createContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { apiFetch, onUnauthorized } from "../lib/api";

export type User = {
  id: number;
  email: string;
  role: "admin" | "user";
  created_at: string;
};

export type AuthContextValue = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    apiFetch<User>("/api/auth/me", {}, { treatUnauthorizedAsSessionExpiry: false })
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => onUnauthorized(() => setUser(null)), []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      async login(email: string, password: string) {
        const loggedIn = await apiFetch<User>(
          "/api/auth/login",
          { method: "POST", body: { email, password } },
          { treatUnauthorizedAsSessionExpiry: false },
        );
        setUser(loggedIn);
      },
      async logout() {
        await apiFetch<void>("/api/auth/logout", { method: "POST" }).catch(() => {});
        setUser(null);
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
