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
