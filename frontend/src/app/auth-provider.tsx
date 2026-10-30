"use client";

import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api").replace(/\/$/, "");

export type AuthMembership = {
  tenant_id: string;
  tenant_name: string;
  role: string;
};

export type AuthUser = {
  id: string;
  email: string;
  full_name: string;
  memberships: AuthMembership[];
};

type AuthContextValue = {
  user: AuthUser | null;
  accessToken: string | null;
  isLoading: boolean;
  error: string;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  authFetch: (path: string, init?: RequestInit) => Promise<Response>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

function getErrorMessage(payload: unknown): string {
  if (typeof payload === "object" && payload !== null) {
    const detail = "detail" in payload ? payload.detail : undefined;
    if (typeof detail === "string") return detail;

    for (const [field, value] of Object.entries(payload)) {
      if (typeof value === "string") return `${field.replaceAll("_", " ")}: ${value}`;
      if (Array.isArray(value) && typeof value[0] === "string") {
        return `${field.replaceAll("_", " ")}: ${value[0]}`;
      }
    }
  }
  return "Authentication could not be completed. Please try again.";
}

async function readJson(response: Response): Promise<unknown> {
  return response.json() as Promise<unknown>;
}

function isAuthUser(value: unknown): value is AuthUser {
  if (typeof value !== "object" || value === null) return false;
  if (!("id" in value) || typeof value.id !== "string") return false;
  if (!("email" in value) || typeof value.email !== "string") return false;
  if (!("full_name" in value) || typeof value.full_name !== "string") return false;
  if (!("memberships" in value) || !Array.isArray(value.memberships)) return false;
  return value.memberships.every(
    (membership: unknown) =>
      typeof membership === "object" &&
      membership !== null &&
      "tenant_id" in membership &&
      typeof membership.tenant_id === "string" &&
      "tenant_name" in membership &&
      typeof membership.tenant_name === "string" &&
      "role" in membership &&
      typeof membership.role === "string",
  );
}

function getAccessToken(value: unknown): string | null {
  return typeof value === "object" &&
    value !== null &&
    "access" in value &&
    typeof value.access === "string"
    ? value.access
    : null;
}

// Refresh tokens rotate, so concurrent restores (React StrictMode runs effects
// twice in development) must share one request or the second one is rejected.
let pendingRefresh: Promise<{ ok: boolean; body: unknown }> | null = null;

function refreshOnce() {
  if (!pendingRefresh) {
    pendingRefresh = fetch(`${API_URL}/auth/refresh/`, {
      method: "POST",
      credentials: "include",
    })
      .then(async (response) => ({ ok: response.ok, body: await readJson(response) }))
      .finally(() => {
        pendingRefresh = null;
      });
  }
  return pendingRefresh;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  const installSession = useCallback(async (token: string) => {
    const response = await fetch(`${API_URL}/auth/me/`, {
      headers: { Authorization: `Bearer ${token}` },
      credentials: "include",
    });
    const result = await readJson(response);
    if (!response.ok) throw new Error(getErrorMessage(result));
    if (!isAuthUser(result)) {
      throw new Error("The authentication service returned an invalid account.");
    }
    setAccessToken(token);
    setUser(result);
  }, []);

  useEffect(() => {
    let mounted = true;
    async function restoreSession() {
      try {
        const refreshed = await refreshOnce();
        if (!refreshed.ok) return;
        const token = getAccessToken(refreshed.body);
        if (!token) {
          throw new Error("The authentication service returned an invalid session.");
        }
        const currentUser = await fetch(`${API_URL}/auth/me/`, {
          headers: { Authorization: `Bearer ${token}` },
          credentials: "include",
        });
        const userResult = await readJson(currentUser);
        if (!currentUser.ok) throw new Error(getErrorMessage(userResult));
        if (!isAuthUser(userResult)) {
          throw new Error("The authentication service returned an invalid account.");
        }
        if (mounted) {
          setAccessToken(token);
          setUser(userResult);
        }
      } catch (caught) {
        if (mounted) {
          setError(caught instanceof Error ? caught.message : "Could not restore your session.");
        }
      } finally {
        if (mounted) setIsLoading(false);
      }
    }
    void restoreSession();
    return () => {
      mounted = false;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    setError("");
    const response = await fetch(`${API_URL}/auth/login/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
    const result = await readJson(response);
    if (!response.ok) throw new Error(getErrorMessage(result));
    const token = getAccessToken(result);
    if (!token) {
      throw new Error("The authentication service returned an invalid session.");
    }
    await installSession(token);
  }, [installSession]);

  const signOut = useCallback(async () => {
    try {
      let token = accessToken;
      let response = await fetch(`${API_URL}/auth/logout/`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        credentials: "include",
      });
      if (response.status === 401) {
        const refreshed = await fetch(`${API_URL}/auth/refresh/`, {
          method: "POST",
          credentials: "include",
        });
        if (!refreshed.ok) {
          setAccessToken(null);
          setUser(null);
          setError("");
          return;
        }
        const result = await readJson(refreshed);
        token = getAccessToken(result);
        if (!token) throw new Error("The authentication service returned an invalid session.");
        response = await fetch(`${API_URL}/auth/logout/`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          credentials: "include",
        });
      }
      if (!response.ok) {
        setError("The server could not revoke your session. Please try signing out again.");
        return;
      }
      setError("");
      setAccessToken(null);
      setUser(null);
    } catch {
      setError("Could not reach NLSchool to revoke your session. Please try again.");
    }
  }, [accessToken]);

  // Retries once with a refreshed access token when the short-lived one has expired.
  const authFetch = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const send = (token: string | null) => {
        const headers = new Headers(init.headers);
        if (token) headers.set("Authorization", `Bearer ${token}`);
        return fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
      };
      const response = await send(accessToken);
      if (response.status !== 401) return response;

      const refreshed = await fetch(`${API_URL}/auth/refresh/`, {
        method: "POST",
        credentials: "include",
      });
      if (!refreshed.ok) {
        setAccessToken(null);
        setUser(null);
        return response;
      }
      const token = getAccessToken(await readJson(refreshed));
      if (!token) return response;
      setAccessToken(token);
      return send(token);
    },
    [accessToken],
  );

  return (
    <AuthContext.Provider value={{ user, accessToken, isLoading, error, signIn, signOut, authFetch }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider.");
  return context;
}
