// frontend/src/hooks/useAuth.ts
"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { auth } from "@/lib/api";
import {
  getStoredUserId,
  setStoredUserId,
  clearStoredUserId,
} from "@/lib/auth";
import type { AuthMeResponse } from "@/types/api";

interface AuthState {
  userId: string | null;
  user: AuthMeResponse | null;
  loading: boolean;
  error: string | null;
}

export function useAuth() {
  const router = useRouter();
  const [state, setState] = useState<AuthState>({
    userId: null,
    user: null,
    loading: true,
    error: null,
  });

  const fetchUser = useCallback(async (userId: string) => {
    try {
      const user = await auth.me();
      setState({ userId, user, loading: false, error: null });
    } catch {
      // Invalid or unknown user ID — clear and redirect
      clearStoredUserId();
      setState({ userId: null, user: null, loading: false, error: null });
    }
  }, []);

  useEffect(() => {
    const stored = getStoredUserId();
    if (!stored) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    fetchUser(stored);
  }, [fetchUser]);

  const login = useCallback(
    async (userId: string) => {
      setState((s) => ({ ...s, loading: true, error: null }));
      setStoredUserId(userId);
      try {
        const user = await auth.me();
        setState({ userId, user, loading: false, error: null });
        router.push("/dashboard");
      } catch {
        clearStoredUserId();
        setState({
          userId: null,
          user: null,
          loading: false,
          error: "User not found. Check your User ID.",
        });
      }
    },
    [router]
  );

  const logout = useCallback(() => {
    clearStoredUserId();
    setState({ userId: null, user: null, loading: false, error: null });
    router.push("/login");
  }, [router]);

  return {
    ...state,
    isAuthenticated: Boolean(state.userId),
    login,
    logout,
  };
}
