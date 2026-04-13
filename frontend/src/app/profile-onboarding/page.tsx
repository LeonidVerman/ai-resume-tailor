// frontend/src/app/profile-onboarding/page.tsx
//
// Candidate Profile Onboarding Wizard
//
// 8-step wizard that guides a new user through filling out their candidate
// profile before they can access generation.  Saves profile data to the
// existing PUT /candidate-profile endpoint on each step transition (autosave).
// On the final step it calls POST /candidate-profile/complete-onboarding to
// mark onboarding done, then navigates to /generate.
//
// Existing users (onboarding_completed=true) are never redirected here;
// they land on /dashboard normally.  AppShell handles the redirect.

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ArrayListEditor } from "@/components/candidate-profile/ArrayListEditor";
import { ExperienceHighlightCard } from "@/components/candidate-profile/ExperienceHighlightCard";
import { candidateProfile, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import {
  EXAMPLE_CANDIDATE,
  EXAMPLE_DOMAINS,
  EXAMPLE_HIGHLIGHTS,
  EXAMPLE_TECHNICAL_SKILLS,
  EXAMPLE_LEADERSHIP,
  EXAMPLE_AI_TOOLING,
  EXAMPLE_ROLE_THEMES,
  EXAMPLE_CLAIM_BOUNDARIES,
} from "@/config/exampleProfileData";
import type {
  CandidateProfileDocument,
  ExperienceHighlight,
  TechnicalSkills,
  Leadership,
  AIToolingPractice,
  ClaimBoundaries,
} from "@/types/api";

// ── Default document ────────────────────────────────────────────────────────

const EMPTY_DOC: CandidateProfileDocument = {
  candidate_profile_version: "2.0",
  candidate: { name: "", headline: "", summary: "" },
  domains: { primary: [], secondary: [] },
  experience_highlights: [],
  technical_skills: {
    languages: [], backend_systems: [], datastores: [],
    infra_devops: [], frontend: [], api_patterns: [], async_messaging: [],
    observability: [], security_auth_patterns: [], scalability_reliability_patterns: [],
  },
  leadership: {
    scope: { team_size_max: null, style_keywords: [] },
    practices: [],
    risk_management: [],
  },
  ai_tooling_practice: { hands_on_tools: [], usage_patterns: [], principles: [], concepts_familiarity: [] },
  role_fit_themes: [],
  constraints_and_preferences: { work_context: [], communication: [], resume_constraint: [] },
  claim_boundaries: { security_auth: [], domain_limits: [], employment_constraints: [] },
};

// ── Keyed highlights ────────────────────────────────────────────────────────

let _k = 0;
type KeyedHighlight = ExperienceHighlight & { _key: string };
const keyed = (h: ExperienceHighlight): KeyedHighlight => ({ ...h, _key: String(_k++) });

const EMPTY_HIGHLIGHT: ExperienceHighlight = {
  area: "", impact: [], team_context: [], architecture_patterns: [],
  constraints_and_tradeoffs: [], skills_applied: [], security_auth_patterns: [],
};

// ── Step metadata ───────────────────────────────────────────────────────────

const STEPS = [
  "Candidate basics",
  "Domain expertise",
  "Experience highlights",
  "Technical skills",
  "Leadership",
  "AI tools",
  "Role themes",
  "Claim boundaries",
] as const;

// ── Helper components ───────────────────────────────────────────────────────

function StepHeader({ step, title }: { step: number; title: string }) {
  return (
    <div className="mb-6">
      <p className="text-sm text-indigo-600 font-medium mb-1">Step {step} of {STEPS.length}</p>
      <h2 className="text-2xl font-bold text-gray-900">{title}</h2>
      <div className="mt-3 flex gap-1">
        {STEPS.map((_, i) => (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-full ${
              i < step ? "bg-indigo-600" : i === step - 1 ? "bg-indigo-400" : "bg-gray-200"
            }`}
          />
        ))}
      </div>
    </div>
  );
}

function FieldRow({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
    </div>
  );
}

// ── Main wizard ─────────────────────────────────────────────────────────────

export default function ProfileOnboardingPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  // Redirect unauthenticated users to login.
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login");
    }
  }, [authLoading, user, router]);

  // Redirect users who have already completed onboarding.
  useEffect(() => {
    if (!authLoading && user?.onboarding_completed) {
      router.replace("/generate");
    }
  }, [authLoading, user, router]);

  const [step, setStep] = useState(1);
  const [doc, setDoc] = useState<CandidateProfileDocument>(EMPTY_DOC);
  const [highlights, setHighlights] = useState<KeyedHighlight[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Incrementing this forces ArrayListEditors on the current step to re-sync
  // their local text state after example data is loaded.
  const [exampleLoadKey, setExampleLoadKey] = useState(0);

  // ── Save current state to backend ────────────────────────────────────────

  async function saveProgress() {
    const payload: CandidateProfileDocument = {
      ...doc,
      experience_highlights: highlights.map(({ _key, ...h }) => h),
    };
    await candidateProfile.upsert({ profile: payload, profile_version: "1" });
  }

  // ── Per-step validation ───────────────────────────────────────────────────

  function validateStep(): string | null {
    switch (step) {
      case 1:
        if (!doc.candidate.name.trim()) return "Name is required.";
        if (!doc.candidate.headline?.trim()) return "Headline is required.";
        if (!doc.candidate.summary?.trim()) return "Summary is required.";
        break;
      case 2:
        if (!doc.domains.primary.length) return "At least one primary domain is required.";
        break;
      case 3:
        if (!highlights.length) return "Add at least one experience highlight.";
        for (let i = 0; i < highlights.length; i++) {
          if (!highlights[i].area.trim()) return `Highlight ${i + 1}: Area is required.`;
        }
        break;
    }
    return null;
  }

  // ── Navigation ────────────────────────────────────────────────────────────

  async function handleNext() {
    const err = validateStep();
    if (err) { setError(err); return; }
    setError(null);
    setSaving(true);
    try {
      await saveProgress();
      setStep(s => s + 1);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to save. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  function handleBack() {
    setError(null);
    setStep(s => s - 1);
  }

  async function handleFinish() {
    setError(null);
    setSaving(true);
    try {
      await saveProgress();
      await candidateProfile.completeOnboarding();
      router.replace("/generate");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to complete onboarding.");
    } finally {
      setSaving(false);
    }
  }

  // ── Field helpers ─────────────────────────────────────────────────────────

  function setTech(field: keyof TechnicalSkills, val: string[]) {
    setDoc(d => ({ ...d, technical_skills: { ...d.technical_skills, [field]: val } }));
  }
  function setLeadership(upd: (prev: Leadership) => Leadership) {
    setDoc(d => ({ ...d, leadership: upd(d.leadership) }));
  }
  function setAI(field: keyof AIToolingPractice, val: string[]) {
    setDoc(d => ({ ...d, ai_tooling_practice: { ...d.ai_tooling_practice, [field]: val } }));
  }
  function setClaimBoundaries(field: keyof ClaimBoundaries, val: string[]) {
    setDoc(d => ({ ...d, claim_boundaries: { ...d.claim_boundaries, [field]: val } }));
  }

  // ── Example loading ───────────────────────────────────────────────────────

  function stepHasContent(): boolean {
    switch (step) {
      case 1: return !!(doc.candidate.name || doc.candidate.headline || doc.candidate.summary);
      case 2: return doc.domains.primary.length > 0 || doc.domains.secondary.length > 0;
      case 3: return highlights.length > 0;
      case 4: return Object.values(doc.technical_skills).some(arr => arr.length > 0);
      case 5: return !!(
        doc.leadership.scope.team_size_max ||
        doc.leadership.scope.style_keywords.length ||
        doc.leadership.practices.length ||
        doc.leadership.risk_management.length
      );
      case 6: return Object.values(doc.ai_tooling_practice).some(arr => arr.length > 0);
      case 7: return doc.role_fit_themes.length > 0;
      case 8: return !!(
        doc.claim_boundaries.security_auth.length ||
        doc.claim_boundaries.domain_limits.length ||
        doc.claim_boundaries.employment_constraints.length
      );
      default: return false;
    }
  }

  function handleLoadExample() {
    if (stepHasContent()) {
      const ok = window.confirm(
        "Replace this page with example content?\nThis will update only the fields on the current page."
      );
      if (!ok) return;
    }
    switch (step) {
      case 1: setDoc(d => ({ ...d, candidate: EXAMPLE_CANDIDATE })); break;
      case 2: setDoc(d => ({ ...d, domains: EXAMPLE_DOMAINS })); break;
      case 3: setHighlights(EXAMPLE_HIGHLIGHTS.map(keyed)); break;
      case 4: setDoc(d => ({ ...d, technical_skills: EXAMPLE_TECHNICAL_SKILLS })); break;
      case 5: setDoc(d => ({ ...d, leadership: EXAMPLE_LEADERSHIP })); break;
      case 6: setDoc(d => ({ ...d, ai_tooling_practice: EXAMPLE_AI_TOOLING })); break;
      case 7: setDoc(d => ({ ...d, role_fit_themes: EXAMPLE_ROLE_THEMES })); break;
      case 8: setDoc(d => ({ ...d, claim_boundaries: EXAMPLE_CLAIM_BOUNDARIES })); break;
    }
    setExampleLoadKey(k => k + 1);
  }

  const { candidate, domains, technical_skills: ts, leadership, ai_tooling_practice: ai, claim_boundaries: cb } = doc;

  // Show nothing while checking auth (prevents a flash of the form).
  if (authLoading || !user) return null;

  // ── Step render ───────────────────────────────────────────────────────────

  function renderStep() {
    switch (step) {
      // ── Step 1: Candidate basics ──────────────────────────────────────────
      case 1:
        return (
          <div className="space-y-4">
            <FieldRow label="Name" required>
              <Input
                value={candidate.name}
                onChange={e => setDoc(d => ({ ...d, candidate: { ...d.candidate, name: e.target.value } }))}
                placeholder="Jane Smith"
              />
            </FieldRow>
            <FieldRow label="Headline" required>
              <Input
                value={candidate.headline ?? ""}
                onChange={e => setDoc(d => ({ ...d, candidate: { ...d.candidate, headline: e.target.value } }))}
                placeholder="Software engineer, backend systems and cloud applications"
              />
            </FieldRow>
            <FieldRow label="Summary" required>
              <Textarea
                value={candidate.summary ?? ""}
                onChange={e => setDoc(d => ({ ...d, candidate: { ...d.candidate, summary: e.target.value } }))}
                placeholder="Software engineer with experience building and maintaining backend and full-stack systems. Focused on scalability, reliability, and delivering production-quality features."
                className="min-h-[120px]"
              />
            </FieldRow>
          </div>
        );

      // ── Step 2: Domain expertise ──────────────────────────────────────────
      case 2:
        return (
          <div className="space-y-4">
            <FieldRow label="Primary domains" required>
              <ArrayListEditor
                value={domains.primary}
                onChange={v => setDoc(d => ({ ...d, domains: { ...d.domains, primary: v } }))}
                placeholder={"Web applications\nBackend systems\nCloud infrastructure"}
                rows={4}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
            <FieldRow label="Secondary domains">
              <ArrayListEditor
                value={domains.secondary}
                onChange={v => setDoc(d => ({ ...d, domains: { ...d.domains, secondary: v } }))}
                placeholder={"Enterprise SaaS\nFinancial technology"}
                rows={3}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
          </div>
        );

      // ── Step 3: Experience highlights ────────────────────────────────────
      case 3:
        return (
          <div className="space-y-4">
            {highlights.map((h, i) => (
              <ExperienceHighlightCard
                key={h._key}
                value={h}
                index={i}
                onChange={updated =>
                  setHighlights(prev => prev.map((p, pi) => (pi === i ? { ...updated, _key: h._key } : p)))
                }
                onRemove={() => setHighlights(prev => prev.filter((_, pi) => pi !== i))}
              />
            ))}
            <button
              type="button"
              onClick={() => setHighlights(prev => [...prev, keyed({ ...EMPTY_HIGHLIGHT })])}
              className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 border-2 border-dashed border-indigo-200 hover:border-indigo-400 rounded-lg w-full justify-center py-2.5 transition-colors"
            >
              <Plus className="h-4 w-4" />
              Add experience highlight
            </button>
          </div>
        );

      // ── Step 4: Technical skills ──────────────────────────────────────────
      case 4:
        return (
          <div className="grid grid-cols-2 gap-4">
            {(
              [
                ["languages", "Languages"],
                ["backend_systems", "Backend systems"],
                ["datastores", "Datastores"],
                ["infra_devops", "Infra / DevOps"],
                ["frontend", "Frontend"],
                ["api_patterns", "API patterns"],
                ["async_messaging", "Async messaging"],
                ["observability", "Observability"],
                ["security_auth_patterns", "Security / auth"],
                ["scalability_reliability_patterns", "Scalability / reliability"],
              ] as [keyof TechnicalSkills, string][]
            ).map(([field, label]) => (
              <div key={field}>
                <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
                <ArrayListEditor
                  value={ts[field]}
                  onChange={v => setTech(field, v)}
                  rows={3}
                  resetKey={exampleLoadKey}
                />
              </div>
            ))}
          </div>
        );

      // ── Step 5: Leadership ────────────────────────────────────────────────
      case 5:
        return (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <FieldRow label="Largest team led">
                <Input
                  type="number"
                  min={0}
                  value={leadership.scope.team_size_max ?? ""}
                  onChange={e => {
                    const n = e.target.value === "" ? null : parseInt(e.target.value, 10);
                    setLeadership(l => ({ ...l, scope: { ...l.scope, team_size_max: isNaN(n as number) ? null : n } }));
                  }}
                  placeholder="8"
                />
              </FieldRow>
              <FieldRow label="Leadership style keywords">
                <ArrayListEditor
                  value={leadership.scope.style_keywords}
                  onChange={v => setLeadership(l => ({ ...l, scope: { ...l.scope, style_keywords: v } }))}
                  placeholder={"Ownership\nCollaboration\nExecution"}
                  rows={3}
                  resetKey={exampleLoadKey}
                />
              </FieldRow>
            </div>
            <FieldRow label="Leadership practices">
              <ArrayListEditor
                value={leadership.practices}
                onChange={v => setLeadership(l => ({ ...l, practices: v }))}
                rows={4}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
            <FieldRow label="Risk management">
              <ArrayListEditor
                value={leadership.risk_management}
                onChange={v => setLeadership(l => ({ ...l, risk_management: v }))}
                rows={3}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
          </div>
        );

      // ── Step 6: AI tools ──────────────────────────────────────────────────
      case 6:
        return (
          <div className="grid grid-cols-2 gap-4">
            {(
              [
                ["hands_on_tools", "Hands-on tools"],
                ["usage_patterns", "Usage patterns"],
                ["principles", "Principles"],
                ["concepts_familiarity", "Concepts familiarity"],
              ] as [keyof AIToolingPractice, string][]
            ).map(([field, label]) => (
              <FieldRow key={field} label={label}>
                <ArrayListEditor
                  value={ai[field]}
                  onChange={v => setAI(field, v)}
                  rows={3}
                  resetKey={exampleLoadKey}
                />
              </FieldRow>
            ))}
          </div>
        );

      // ── Step 7: Role fit themes ───────────────────────────────────────────
      case 7:
        return (
          <FieldRow label="Strong-fit role themes">
            <ArrayListEditor
              value={doc.role_fit_themes}
              onChange={v => setDoc(d => ({ ...d, role_fit_themes: v }))}
              placeholder={"Backend development\nScalable system design\nAPI development"}
              rows={6}
              resetKey={exampleLoadKey}
            />
          </FieldRow>
        );

      // ── Step 8: Claim boundaries ──────────────────────────────────────────
      case 8:
        return (
          <div className="space-y-4">
            <FieldRow label="Security / auth boundaries">
              <ArrayListEditor
                value={cb.security_auth}
                onChange={v => setClaimBoundaries("security_auth", v)}
                placeholder={"Do not claim ownership of large-scale security architecture unless supported"}
                rows={4}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
            <FieldRow label="Domain limits">
              <ArrayListEditor
                value={cb.domain_limits}
                onChange={v => setClaimBoundaries("domain_limits", v)}
                placeholder={"Avoid overstating leadership scope\nKeep domain claims aligned with actual experience"}
                rows={4}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
            <FieldRow label="Employment constraints">
              <ArrayListEditor
                value={cb.employment_constraints}
                onChange={v => setClaimBoundaries("employment_constraints", v)}
                placeholder={"Maintain accuracy of roles and dates"}
                rows={3}
                resetKey={exampleLoadKey}
              />
            </FieldRow>
          </div>
        );

      default:
        return null;
    }
  }

  // ── Layout ────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-50 flex items-start justify-center py-12 px-4">
      <div className="w-full max-w-3xl">
        <div className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-gray-900">Set up your candidate profile</h1>
          <p className="mt-2 text-gray-500">
            This takes about 5 minutes. Your answers power every resume tailoring run.
          </p>
        </div>

        <Card>
          <CardHeader>
            <StepHeader step={step} title={STEPS[step - 1]} />
          </CardHeader>
          <CardBody className="space-y-6">
            <div className="flex justify-end -mt-2">
              <button
                type="button"
                onClick={handleLoadExample}
                className="text-xs text-indigo-500 hover:text-indigo-700 hover:underline underline-offset-2 transition-colors"
              >
                Load from example profile
              </button>
            </div>

            {renderStep()}

            {error && <p className="text-sm text-red-600">✗ {error}</p>}

            <div className="flex items-center justify-between pt-2">
              <Button
                type="button"
                variant="secondary"
                onClick={handleBack}
                disabled={step === 1 || saving}
              >
                Back
              </Button>

              {step < STEPS.length ? (
                <Button type="button" onClick={handleNext} loading={saving}>
                  Next
                </Button>
              ) : (
                <Button type="button" onClick={handleFinish} loading={saving}>
                  Finish setup
                </Button>
              )}
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
