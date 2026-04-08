// frontend/src/app/profile/page.tsx
"use client";
import { useState, useEffect } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";
import { Spinner } from "@/components/ui/Spinner";
import { ProfileForm } from "@/components/candidate-profile/ProfileForm";
import { candidateProfile, legal } from "@/lib/api";
import type { CandidateProfileResponse, LegalStatusResponse } from "@/types/api";
import { formatDate } from "@/lib/utils";

export default function ProfilePage() {
  const [existing, setExisting] = useState<CandidateProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [legalStatus, setLegalStatus] = useState<LegalStatusResponse | null>(null);

  useEffect(() => {
    candidateProfile
      .get()
      .then(setExisting)
      .catch(() => {})
      .finally(() => setLoading(false));
    legal.status().then(setLegalStatus).catch(() => {});
  }, []);

  return (
    <AppShell>
      <div className="max-w-3xl mx-auto space-y-10">
        <div>
          <div className="mb-6">
            <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Candidate profile</h1>
            <p className="text-gray-500 mt-1">
              Your profile is injected into every generation. Keep it accurate and complete.
            </p>
          </div>
          {loading ? (
            <div className="py-12 flex justify-center">
              <Spinner size="lg" label="Loading profile..." />
            </div>
          ) : (
            <ProfileForm initial={existing} onSaved={setExisting} />
          )}
        </div>

        {/* Legal section */}
        <div>
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Legal</h2>
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm divide-y divide-gray-100">
            {/* Terms of Service */}
            <div className="px-5 py-4 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900">Terms of Service</p>
                {legalStatus?.terms.accepted ? (
                  <p className="text-xs text-gray-500 mt-0.5">
                    Version {legalStatus.terms.version} &middot; Accepted{" "}
                    {legalStatus.terms.accepted_at ? formatDate(legalStatus.terms.accepted_at) : "—"}
                    {!legalStatus.terms.is_current && (
                      <span className="ml-2 text-amber-600 font-medium">Update required</span>
                    )}
                  </p>
                ) : (
                  <p className="text-xs text-amber-600 mt-0.5">Not yet accepted</p>
                )}
              </div>
              <Link href="/legal/terms" className="text-xs text-indigo-600 hover:underline">
                View
              </Link>
            </div>

            {/* Privacy Notice */}
            <div className="px-5 py-4 flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-900">Privacy Notice</p>
                {legalStatus?.privacy.accepted ? (
                  <p className="text-xs text-gray-500 mt-0.5">
                    Version {legalStatus.privacy.version} &middot; Accepted{" "}
                    {legalStatus.privacy.accepted_at ? formatDate(legalStatus.privacy.accepted_at) : "—"}
                    {!legalStatus.privacy.is_current && (
                      <span className="ml-2 text-amber-600 font-medium">Update required</span>
                    )}
                  </p>
                ) : (
                  <p className="text-xs text-amber-600 mt-0.5">Not yet accepted</p>
                )}
              </div>
              <Link href="/legal/privacy" className="text-xs text-indigo-600 hover:underline">
                View
              </Link>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
