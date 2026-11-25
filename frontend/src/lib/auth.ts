// frontend/src/lib/auth.ts
//
// Auth token storage utilities.
// The backend issues Supabase JWTs; we store the access token in localStorage
// and send it as Authorization: Bearer on every API request.
//
// In dev_bypass mode the backend still accepts X-User-Id header — the login
// page will use a UUID entry form in that mode (see useAuth.ts).

const TOKEN_KEY = "art_access_token";
const REFRESH_TOKEN_KEY = "art_refresh_token";
const DEV_USER_KEY = "art_user_id"; // legacy dev-bypass key
// Guest generation (issue #155):
// - GUEST_SESSION_KEY marks that the currently stored tokens belong to an
//   anonymous guest session created on /try.
// - GUEST_CLAIM_KEY stashes a guest access token so it can be claimed on the
//   first authenticated arrival (e.g. after email confirmation + login).
const GUEST_SESSION_KEY = "art_is_guest";
const GUEST_CLAIM_KEY = "art_guest_claim_token";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setStoredToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(DEV_USER_KEY); // also clear legacy key on logout
  // The guest marker describes the stored tokens — clear it with them.
  // (The claim stash is intentionally NOT cleared: it must survive a
  // logout/login cycle, e.g. email-confirmation registration.)
  localStorage.removeItem(GUEST_SESSION_KEY);
}

export function getStoredRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setStoredRefreshToken(token: string): void {
  localStorage.setItem(REFRESH_TOKEN_KEY, token);
}

// Dev-bypass helpers (used when AUTH_MODE=dev_bypass)
export function getStoredUserId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(DEV_USER_KEY);
}

export function setStoredUserId(userId: string): void {
  localStorage.setItem(DEV_USER_KEY, userId);
}

export function isAuthenticated(): boolean {
  return Boolean(getStoredToken() || getStoredUserId());
}

// ── Guest session helpers (issue #155, Phase 4) ────────────────────────────

/** Mark the currently stored tokens as belonging to an anonymous guest. */
export function markGuestSession(): void {
  localStorage.setItem(GUEST_SESSION_KEY, "1");
}

export function isGuestSession(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(GUEST_SESSION_KEY) === "1";
}

/** Stash a guest access token for a later one-shot claim attempt. */
export function stashGuestClaimToken(token: string): void {
  localStorage.setItem(GUEST_CLAIM_KEY, token);
}

/**
 * Read AND remove the stashed guest claim token (single-attempt semantics:
 * whatever the claim outcome, we never retry with the same stash).
 */
export function takeGuestClaimToken(): string | null {
  if (typeof window === "undefined") return null;
  const token = localStorage.getItem(GUEST_CLAIM_KEY);
  if (token !== null) localStorage.removeItem(GUEST_CLAIM_KEY);
  return token;
}
