// frontend/src/components/layout/AppShell.tsx
"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
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

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50">
        <Spinner size="lg" label="Loading..." />
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return (
    <div className="flex min-h-screen bg-gray-50">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="max-w-5xl mx-auto px-8 py-8">{children}</div>
      </main>
    </div>
  );
}
