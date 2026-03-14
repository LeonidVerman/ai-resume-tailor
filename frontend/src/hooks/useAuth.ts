"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { auth } from "@/lib/api";
import {
  clearStoredToken,
  getStoredToken,
  getStoredUserId,
  setStoredToken,
  setStoredUserId,
} from "@/lib/auth";
import type { AuthMeResponse } from "@/types/api";

interface AuthState {
  user: AuthMeResponse | null;
  loading: boolean;
  error: string | null;
}

export function useAuth() {
  const router = useRouter();
  const [state, setState] = useState<AuthState>({
    user: null,
    loading: true,
    error: null,
  });

  // On mount: check stored credentials and fetch user info
  const bootstrap = useCallback(async () => {
    const hasToken = Boolean(getStoredToken() || getStoredUserId());
    if (!hasToken) {
      setState({ user: null, loading: false, error: null });
      return;
    }
    try {
      const user = await auth.me();
      setState({ user, loading: false, error: null });
    } catch {
      clearStoredToken();
      setState({ user: null, loading: false, error: null });
    }
  }, []);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  // Real auth: email + password → Supabase JWT
  const login = useCallback(
    async (email: string, password: string) => {
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const result = await auth.login({ email, password });
        setStoredToken(result.session.access_token);
        const me = await auth.me();
        setState({ user: me, loading: false, error: null });
        router.push("/dashboard");
      } catch (err: unknown) {
        clearStoredToken();
        const msg =
          err instanceof Error ? err.message : "Invalid email or password.";
        setState({ user: null, loading: false, error: msg });
      }
    },
    [router]
  );

  // Dev-bypass: UUID entry (only used when AUTH_MODE=dev_bypass)
  const loginDevBypass = useCallback(
    async (userId: string) => {
      setState((s) => ({ ...s, loading: true, error: null }));
      setStoredUserId(userId);
      try {
        const user = await auth.me();
        setState({ user, loading: false, error: null });
        router.push("/dashboard");
      } catch {
        clearStoredToken();
        setState({
          user: null,
          loading: false,
          error: "User not found. Check your User ID.",
        });
      }
    },
    [router]
  );

  const register = useCallback(
    async (email: string, password: string) => {
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const result = await auth.register({ email, password });
        setStoredToken(result.session.access_token);
        const me = await auth.me();
        setState({ user: me, loading: false, error: null });
        router.push("/dashboard");
      } catch (err: unknown) {
        clearStoredToken();
        const msg =
          err instanceof Error ? err.message : "Registration failed.";
        setState({ user: null, loading: false, error: msg });
      }
    },
    [router]
  );

  const logout = useCallback(async () => {
    try {
      await auth.logout();
    } catch {
      // best-effort server-side invalidation
    }
    clearStoredToken();
    setState({ user: null, loading: false, error: null });
    router.push("/login");
  }, [router]);

  return {
    user: state.user,
    userId: state.user?.user_id ?? null,
    loading: state.loading,
    error: state.error,
    isAuthenticated: Boolean(state.user),
    isAdmin: state.user?.role === "admin",
    login,
    loginDevBypass,
    register,
    logout,
  };
}
