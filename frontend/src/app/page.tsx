// Root page — redirect to dashboard if logged in, otherwise to login.
"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getStoredUserId } from "@/lib/auth";
import { Spinner } from "@/components/ui/Spinner";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    const uid = getStoredUserId();
    router.replace(uid ? "/dashboard" : "/login");
  }, [router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner size="lg" />
    </div>
  );
}
