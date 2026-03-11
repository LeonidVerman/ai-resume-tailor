// frontend/src/app/profile/page.tsx
"use client";
import { useState, useEffect } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Spinner } from "@/components/ui/Spinner";
import { ProfileForm } from "@/components/candidate-profile/ProfileForm";
import { candidateProfile } from "@/lib/api";
import type { CandidateProfileResponse } from "@/types/api";

export default function ProfilePage() {
  const [existing, setExisting] = useState<CandidateProfileResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    candidateProfile
      .get()
      .then(setExisting)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <AppShell>
      <div className="max-w-3xl mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Candidate profile</h1>
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
    </AppShell>
  );
}
