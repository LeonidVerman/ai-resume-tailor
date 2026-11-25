"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { auth, guest, ApiError } from "@/lib/api";
import {
  clearStoredToken,
  getStoredToken,
  getStoredUserId,
  isGuestSession,
  setStoredRefreshToken,
  setStoredToken,
  setStoredUserId,
  stashGuestClaimToken,
  takeGuestClaimToken,
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
    } catch (err) {
      // Only clear the stored token on a 401 (invalid/expired JWT).
      // Do NOT clear on 500 or network errors — that would wipe a valid
      // token due to a transient backend failure, locking the user out.
      if (err instanceof ApiError && err.status === 401) {
        clearStoredToken();
      }
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
        setStoredRefreshToken(result.session.refresh_token);
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
      // Guest claim (issue #155): capture the guest access token BEFORE the
      // new session tokens overwrite the shared storage slots.
      const guestToken = isGuestSession() ? getStoredToken() : null;
      try {
        const result = await auth.register({ email, password });
        setStoredToken(result.session.access_token);
        setStoredRefreshToken(result.session.refresh_token);
        // Claim the guest content with the NEW user's Authorization.
        // Non-fatal: a failed claim must never block registration.
        const claimToken = guestToken ?? takeGuestClaimToken();
        if (claimToken) {
          try {
            await guest.claim({ guest_access_token: claimToken });
          } catch (claimErr) {
            console.warn("Could not attach guest results to the new account:", claimErr);
          }
        }
        const me = await auth.me();
        setState({ user: me, loading: false, error: null });
        // New users always need to accept legal docs before using the app.
        router.push(me.legal_accepted ? "/dashboard" : "/legal/accept");
      } catch (err: unknown) {
        // Registration did not produce a session (e.g. email confirmation
        // required, or a retryable error). Stash the guest token so the
        // claim can happen on the first authenticated arrival instead.
        if (guestToken) stashGuestClaimToken(guestToken);
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
