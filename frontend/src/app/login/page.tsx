// frontend/src/app/login/page.tsx
//
// Dev-mode login: user enters their User ID (UUID from the database).
// The backend verifies it exists via GET /auth/me with X-User-Id header.
//
// Full Supabase JWT auth is deferred to a later phase.
"use client";

import { useState } from "react";
import { Wand2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/hooks/useAuth";

export default function LoginPage() {
  const [userId, setUserId] = useState("");
  const { login, loading, error } = useAuth();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!userId.trim()) return;
    await login(userId.trim());
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center h-12 w-12 rounded-xl bg-indigo-600 mb-4">
            <Wand2 className="h-6 w-6 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">AI Resume Tailor</h1>
          <p className="text-sm text-gray-500 mt-1">Sign in to your account</p>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="User ID"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              hint="Enter your user ID (UUID). Dev-mode auth — Supabase login coming soon."
              required
            />

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

        <p className="text-center text-xs text-gray-400 mt-4">
          Dev-mode: authentication uses X-User-Id header bypass.
        </p>
      </div>
    </div>
  );
}
