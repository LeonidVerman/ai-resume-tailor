"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Wand2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { legal, ApiError } from "@/lib/api";
import { clearStoredToken, getStoredToken, getStoredUserId } from "@/lib/auth";
import type { LegalCurrentResponse } from "@/types/api";

export default function LegalAcceptPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<LegalCurrentResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [termsChecked, setTermsChecked] = useState(false);
  const [privacyChecked, setPrivacyChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Redirect to login if no credentials stored
    if (!getStoredToken() && !getStoredUserId()) {
      router.replace("/login");
      return;
    }
    legal.current()
      .then(setDocs)
      .catch(() => setLoadError("Could not load legal documents. Please refresh."));
  }, [router]);

  async function handleAccept(e: React.FormEvent) {
    e.preventDefault();
    if (!docs || !termsChecked || !privacyChecked) return;
    setSubmitting(true);
    setError(null);
    try {
      await legal.accept({
        terms_document_id: docs.terms.id,
        privacy_document_id: docs.privacy.id,
        acceptance_method: "checkbox",
        source_surface: "legal_gate",
      });
      // Refresh user state by navigating to dashboard — AppShell will re-check
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // Session expired — clear stale token and send to login
        clearStoredToken();
        router.replace("/login?reason=session_expired");
        return;
      }
      setError("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  }

  const canSubmit = termsChecked && privacyChecked && !submitting;

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
        <div className="w-full max-w-sm text-center space-y-4">
          <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
            {loadError}
          </p>
          <Button onClick={() => window.location.reload()}>Retry</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4 py-12">
      <div className="w-full max-w-lg">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center h-12 w-12 rounded-xl bg-indigo-600 mb-4">
            <Wand2 className="h-6 w-6 text-white" />
          </div>
          <h1 className="text-2xl font-bold text-gray-900">AI Resume Tailor</h1>
          <p className="text-sm text-gray-500 mt-1">Please review and accept before continuing</p>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-1">Terms & Privacy</h2>
          <p className="text-sm text-gray-500 mb-6">
            To use AI Resume Tailor, you must accept our legal documents. Please read each one
            before checking the boxes below.
          </p>

          {docs && (
            <div className="space-y-4 mb-6">
              {/* Terms of Service */}
              <div className="rounded-lg border border-gray-200 p-4">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-sm font-medium text-gray-900">{docs.terms.title}</p>
                    <p className="text-xs text-gray-500">
                      Version {docs.terms.version} &middot; Effective{" "}
                      {new Date(docs.terms.effective_at).toLocaleDateString()}
                    </p>
                  </div>
                  <Link
                    href="/legal/terms"
                    target="_blank"
                    className="text-xs text-indigo-600 hover:underline whitespace-nowrap ml-4"
                  >
                    Read full text
                  </Link>
                </div>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={termsChecked}
                    onChange={(e) => setTermsChecked(e.target.checked)}
                    className="mt-0.5 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <span className="text-sm text-gray-700">
                    I agree to the <strong>Terms of Service</strong>
                  </span>
                </label>
              </div>

              {/* Privacy Notice */}
              <div className="rounded-lg border border-gray-200 p-4">
                <div className="flex items-start justify-between mb-2">
                  <div>
                    <p className="text-sm font-medium text-gray-900">{docs.privacy.title}</p>
                    <p className="text-xs text-gray-500">
                      Version {docs.privacy.version} &middot; Effective{" "}
                      {new Date(docs.privacy.effective_at).toLocaleDateString()}
                    </p>
                  </div>
                  <Link
                    href="/legal/privacy"
                    target="_blank"
                    className="text-xs text-indigo-600 hover:underline whitespace-nowrap ml-4"
                  >
                    Read full text
                  </Link>
                </div>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={privacyChecked}
                    onChange={(e) => setPrivacyChecked(e.target.checked)}
                    className="mt-0.5 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <span className="text-sm text-gray-700">
                    I acknowledge the <strong>Privacy Notice</strong>
                  </span>
                </label>
              </div>
            </div>
          )}

          {!docs && !loadError && (
            <div className="py-8 flex justify-center">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
            </div>
          )}

          {error && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mb-4">
              {error}
            </p>
          )}

          <form onSubmit={handleAccept}>
            <Button
              type="submit"
              loading={submitting}
              disabled={!canSubmit}
              className="w-full"
            >
              Accept and continue
            </Button>
          </form>
        </div>

        <p className="text-center text-xs text-gray-400 mt-4">
          You can view these documents at any time in your account settings.
        </p>
      </div>
    </div>
  );
}
