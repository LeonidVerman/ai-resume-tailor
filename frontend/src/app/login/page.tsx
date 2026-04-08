"use client";

import { Suspense, useState, useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Wand2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";
import { auth } from "@/lib/api";

function SessionExpiredBanner() {
  const searchParams = useSearchParams();
  if (searchParams.get("reason") !== "session_expired") return null;
  return (
    <p className="text-sm text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
      Your session expired. Please sign in again to continue.
    </p>
  );
}

export default function LoginPage() {
  const { login, loginDevBypass, loading, error } = useAuth();
  const [authMode, setAuthMode] = useState<"supabase" | "dev_bypass" | null>(null);

  // Email/password fields (supabase mode)
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  // Dev-bypass field
  const [userId, setUserId] = useState("");

  useEffect(() => {
    auth.status().then((s) => setAuthMode(s.auth_mode as "supabase" | "dev_bypass")).catch(() => {});
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (authMode === "dev_bypass") {
      if (!userId.trim()) return;
      await loginDevBypass(userId.trim());
    } else {
      if (!email.trim() || !password) return;
      await login(email.trim(), password);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center h-12 w-12 rounded-xl bg-indigo-600 mb-4">
            <Wand2 className="h-6 w-6 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">CVRocket</h1>
          <p className="text-sm text-gray-500 mt-1">Sign in to your account</p>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            {authMode === "dev_bypass" ? (
              <Input
                label="User ID"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                hint="Dev mode — enter your user UUID from the database."
                required
              />
            ) : (
              <>
                <Input
                  label="Email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  autoComplete="email"
                  required
                />
                <div>
                  <Input
                    label="Password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    autoComplete="current-password"
                    required
                  />
                  <div className="mt-1 text-right">
                    <Link
                      href="/forgot-password"
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      Forgot password?
                    </Link>
                  </div>
                </div>
              </>
            )}

            <Suspense>
              <SessionExpiredBanner />
            </Suspense>

            {error && (
              <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <Button type="submit" loading={loading} className="w-full">
              Sign in
            </Button>
          </form>
        </div>

        {authMode !== "dev_bypass" && (
          <p className="text-center text-sm text-gray-500 mt-4">
            Don&apos;t have an account?{" "}
            <Link href="/register" className="text-indigo-600 hover:underline font-medium">
              Sign up
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
