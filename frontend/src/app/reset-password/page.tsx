"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Wand2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { auth } from "@/lib/api";
import { PublicFooter } from "@/components/layout/PublicFooter";

export default function ResetPasswordPage() {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  // Supabase appends the recovery token in the URL fragment:
  //   /reset-password#access_token=...&type=recovery
  useEffect(() => {
    const hash = window.location.hash.slice(1); // strip leading '#'
    const params = new URLSearchParams(hash);
    const token = params.get("access_token");
    const type = params.get("type");

    if (!token) {
      setTokenError("No reset token found. Please request a new password-reset link.");
      return;
    }
    if (type !== "recovery") {
      setTokenError("This link is not a password-reset link. Please request a new one.");
      return;
    }
    setAccessToken(token);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!accessToken) return;

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }

    setLoading(true);
    setError(null);
    try {
      await auth.resetPassword({ access_token: accessToken, new_password: newPassword });
      setDone(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to reset password. The link may have expired.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex flex-col bg-gray-50 px-4">
      <div className="flex-1 flex items-center justify-center">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center h-12 w-12 rounded-xl bg-indigo-600 mb-4">
            <Wand2 className="h-6 w-6 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">CVRocket</h1>
          <p className="text-sm text-gray-500 mt-1">Choose a new password</p>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          {done ? (
            <div className="text-center space-y-3">
              <p className="text-sm text-gray-700">
                Your password has been updated. You can now sign in with your new password.
              </p>
              <Link href="/login">
                <Button className="w-full mt-2">Sign in</Button>
              </Link>
            </div>
          ) : tokenError ? (
            <div className="space-y-3">
              <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
                {tokenError}
              </p>
              <Link href="/forgot-password">
                <Button variant="secondary" className="w-full">
                  Request a new link
                </Button>
              </Link>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <Input
                label="New password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="new-password"
                required
              />
              <Input
                label="Confirm new password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="new-password"
                required
              />

              {error && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
                  {error}
                </p>
              )}

              <Button type="submit" loading={loading} className="w-full" disabled={!accessToken}>
                Set new password
              </Button>
            </form>
          )}
        </div>

        {!done && (
          <p className="text-center text-sm text-gray-500 mt-4">
            <Link href="/login" className="text-indigo-600 hover:underline font-medium">
              Back to sign in
            </Link>
          </p>
        )}
      </div>
      </div>
      <PublicFooter />
    </div>
  );
}
