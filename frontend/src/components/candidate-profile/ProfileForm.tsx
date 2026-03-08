// frontend/src/components/candidate-profile/ProfileForm.tsx
//
// Simplified candidate profile editor.
// The full schema is complex JSONB — this form covers the most important
// fields. Full structured editing can be added in a later phase.
"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { candidateProfile, ApiError } from "@/lib/api";
import type {
  CandidateProfileDocument,
  CandidateProfileResponse,
} from "@/types/api";

interface ProfileFormProps {
  initial?: CandidateProfileResponse | null;
  onSaved?: (profile: CandidateProfileResponse) => void;
}

export function ProfileForm({ initial, onSaved }: ProfileFormProps) {
  const existing = initial?.profile;

  const [name, setName] = useState(existing?.candidate.name ?? "");
  const [headline, setHeadline] = useState(existing?.candidate.headline ?? "");
  const [summary, setSummary] = useState(existing?.candidate.summary ?? "");
  const [primaryDomains, setPrimaryDomains] = useState(
    (existing?.domains?.primary ?? []).join(", ")
  );
  const [roleFitThemes, setRoleFitThemes] = useState(
    (existing?.role_fit_themes ?? []).join(", ")
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  function toList(csv: string): string[] {
    return csv
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Name is required."); return; }
    setError(null);
    setSuccess(false);
    setLoading(true);

    const doc: CandidateProfileDocument = {
      candidate_profile_version: "1.0",
      candidate: {
        name: name.trim(),
        headline: headline.trim() || undefined,
        summary: summary.trim() || undefined,
      },
      domains: {
        primary: toList(primaryDomains),
        secondary: [],
      },
      experience_highlights: existing?.experience_highlights ?? [],
      technical_skills: existing?.technical_skills,
      role_fit_themes: toList(roleFitThemes),
      scalability_reliability_patterns: existing?.scalability_reliability_patterns ?? [],
    };

    try {
      const result = await candidateProfile.upsert({
        profile: doc,
        profile_version: "1",
      });
      setSuccess(true);
      onSaved?.(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to save profile.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <Input
          label="Full name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Jane Smith"
          required
        />
        <Input
          label="Headline"
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          placeholder="Senior Software Engineer"
        />
      </div>

      <Textarea
        label="Professional summary"
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        placeholder="Brief summary of your background and key strengths..."
        className="min-h-[120px]"
      />

      <Input
        label="Primary domains"
        value={primaryDomains}
        onChange={(e) => setPrimaryDomains(e.target.value)}
        placeholder="FinTech, B2B SaaS, Developer Tooling"
        hint="Comma-separated list of your main industry domains"
      />

      <Input
        label="Role fit themes"
        value={roleFitThemes}
        onChange={(e) => setRoleFitThemes(e.target.value)}
        placeholder="Distributed systems, API design, Team leadership"
        hint="Comma-separated key themes that describe your fit"
      />

      {error && <p className="text-sm text-red-600">✗ {error}</p>}
      {success && <p className="text-sm text-green-600">✓ Profile saved successfully.</p>}

      <Button type="submit" loading={loading}>
        Save profile
      </Button>
    </form>
  );
}
