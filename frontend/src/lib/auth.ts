// frontend/src/lib/auth.ts
//
// Auth utilities — dev-mode implementation.
//
// Current auth strategy: X-User-Id header bypass.
// The backend accepts an X-User-Id header (UUID) and looks up the user in DB.
// Full Supabase JWT integration is deferred to a later phase.
//
// For dev/demo, the "login" flow stores a user ID in localStorage.
// The API client reads it and includes it in every request.

const STORAGE_KEY = "art_user_id";

export function getStoredUserId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STORAGE_KEY);
}

export function setStoredUserId(userId: string): void {
  localStorage.setItem(STORAGE_KEY, userId);
}

export function clearStoredUserId(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function isAuthenticated(): boolean {
  return Boolean(getStoredUserId());
}
