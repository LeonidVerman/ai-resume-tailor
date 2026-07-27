// frontend/src/components/layout/AppShell.tsx
"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { guest } from "@/lib/api";
import { takeGuestClaimToken } from "@/lib/auth";
import { Sidebar } from "./Sidebar";
import { Spinner } from "@/components/ui/Spinner";

interface AppShellProps {
  children: React.ReactNode;
}

// Pages exempt from the legal acceptance gate.
const LEGAL_EXEMPT = ["/legal"];
// Pages exempt from the onboarding redirect (the wizard itself + profile edit).
const ONBOARDING_EXEMPT = ["/profile-onboarding", "/profile"];

export function AppShell({ children }: AppShellProps) {
  const { isAuthenticated, loading, user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (loading) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }
    // Require legal acceptance before full product access.
    if (
      user &&
      !user.legal_accepted &&
      !LEGAL_EXEMPT.some((p) => pathname.startsWith(p))
    ) {
      router.replace("/legal/accept");
      return;
    }
    // Redirect new users to the onboarding wizard if not yet complete.
    if (
      user &&
      !user.onboarding_completed &&
      !ONBOARDING_EXEMPT.some((p) => pathname.startsWith(p))
    ) {
      router.replace("/profile-onboarding");
    }
  }, [loading, isAuthenticated, user, pathname, router]);

  // Guest claim (issue #155): if a guest token was stashed during a
  // registration that required email confirmation, attempt the claim once
  // on the first authenticated arrival. takeGuestClaimToken() removes the
  // key, so this runs at most once regardless of the outcome (non-fatal).
  useEffect(() => {
    if (loading || !isAuthenticated || !user || user.is_anonymous) return;
    const stashed = takeGuestClaimToken();
    if (!stashed) return;
    guest
      .claim({ guest_access_token: stashed })
      .catch((err) =>
        console.warn("Could not attach guest results to this account:", err)
      );
  }, [loading, isAuthenticated, user]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <Spinner size="lg" label="Loading..." />
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <div className="flex min-h-screen bg-slate-50">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-8 py-8">{children}</div>
      </main>
    </div>
  );
}
