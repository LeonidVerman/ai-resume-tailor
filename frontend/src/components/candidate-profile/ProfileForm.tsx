// frontend/src/components/candidate-profile/ProfileForm.tsx
"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ArrayListEditor } from "./ArrayListEditor";
import { FieldHelp } from "./FieldHelp";
import { ExperienceHighlightCard } from "./ExperienceHighlightCard";
import { candidateProfile, ApiError } from "@/lib/api";
import type {
  CandidateProfileDocument,
  CandidateProfileResponse,
  ExperienceHighlight,
  TechnicalSkills,
  Leadership,
  AIToolingPractice,
  ConstraintsAndPreferences,
  DomainExperience,
  CandidateIdentity,
  ClaimBoundaries,
} from "@/types/api";

// ── Default v2.0 profile ────────────────────────────────────────────────────

const DEFAULT_PROFILE: CandidateProfileDocument = {
  candidate_profile_version: "2.0",
  candidate: { name: "", headline: "", summary: "" },
  domains: { primary: [], secondary: [] },
  experience_highlights: [],
  technical_skills: {
    languages: [], backend_systems: [], datastores: [],
    infra_devops: [], frontend: [], api_patterns: [], async_messaging: [], observability: [],
    security_auth_patterns: [], scalability_reliability_patterns: [],
  },
  leadership: {
    scope: { team_size_max: null, style_keywords: [] },
    practices: [],
    risk_management: [],
  },
  ai_tooling_practice: {
    hands_on_tools: [], usage_patterns: [], principles: [], concepts_familiarity: [],
  },
  role_fit_themes: [],
  constraints_and_preferences: {
    work_context: [], communication: [], resume_constraint: [],
  },
  claim_boundaries: { security_auth: [], domain_limits: [], employment_constraints: [] },
};

function hydrateProfile(stored?: Partial<CandidateProfileDocument>): CandidateProfileDocument {
  if (!stored) return { ...DEFAULT_PROFILE };
  const d = DEFAULT_PROFILE;
  return {
    candidate_profile_version: "2.0",
    candidate: { ...d.candidate, ...stored.candidate },
    domains: { ...d.domains, ...stored.domains },
    experience_highlights: stored.experience_highlights ?? [],
    technical_skills: { ...d.technical_skills, ...stored.technical_skills },
    leadership: {
      scope: { ...d.leadership.scope, ...(stored.leadership?.scope ?? {}) },
      practices: stored.leadership?.practices ?? [],
      risk_management: stored.leadership?.risk_management ?? [],
    },
    ai_tooling_practice: { ...d.ai_tooling_practice, ...stored.ai_tooling_practice },
    role_fit_themes: stored.role_fit_themes ?? [],
    constraints_and_preferences: {
      ...d.constraints_and_preferences,
      ...stored.constraints_and_preferences,
    },
    claim_boundaries: {
      security_auth: [],
      domain_limits: [],
      employment_constraints: [],
      ...stored.claim_boundaries,
    },
  };
}

// ── Stable-keyed experience highlights (prevents React reuse bugs on remove) ─

let _keyCounter = 0;
type HighlightWithKey = ExperienceHighlight & { _key: string };
const makeKey = () => String(_keyCounter++);
const toKeyed = (h: ExperienceHighlight): HighlightWithKey => ({ ...h, _key: makeKey() });

const EMPTY_HIGHLIGHT: ExperienceHighlight = {
  area: "", impact: [], team_context: [],
  architecture_patterns: [], constraints_and_tradeoffs: [], skills_applied: [],
  security_auth_patterns: [],
};

// ── Section helpers ─────────────────────────────────────────────────────────

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <h3 className="font-semibold text-gray-900">{title}</h3>
      </CardHeader>
      <CardBody className="space-y-4">{children}</CardBody>
    </Card>
  );
}

function Field({
  label, configKey, required, children,
}: {
  label: string; configKey: string; required?: boolean; children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-0.5">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      <FieldHelp configKey={configKey} />
      <div className="mt-1">{children}</div>
    </div>
  );
}

// ── Main form ───────────────────────────────────────────────────────────────

interface ProfileFormProps {
  initial?: CandidateProfileResponse | null;
  onSaved?: (profile: CandidateProfileResponse) => void;
}

export function ProfileForm({ initial, onSaved }: ProfileFormProps) {
  const initDoc = hydrateProfile(initial?.profile);

  const [doc, setDoc] = useState<CandidateProfileDocument>(initDoc);
  const [highlights, setHighlights] = useState<HighlightWithKey[]>(
    () => initDoc.experience_highlights.map(toKeyed)
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // ── Field setters ────────────────────────────────────────────────────────

  function setCandidate(field: keyof CandidateIdentity, val: string) {
    setDoc(d => ({ ...d, candidate: { ...d.candidate, [field]: val } }));
  }
  function setDomains(field: keyof DomainExperience, val: string[]) {
    setDoc(d => ({ ...d, domains: { ...d.domains, [field]: val } }));
  }
  function setTech(field: keyof TechnicalSkills, val: string[]) {
    setDoc(d => ({ ...d, technical_skills: { ...d.technical_skills, [field]: val } }));
  }
  function setLeadership(upd: (prev: Leadership) => Leadership) {
    setDoc(d => ({ ...d, leadership: upd(d.leadership) }));
  }
  function setAI(field: keyof AIToolingPractice, val: string[]) {
    setDoc(d => ({ ...d, ai_tooling_practice: { ...d.ai_tooling_practice, [field]: val } }));
  }
  function setConstraints(field: keyof ConstraintsAndPreferences, val: string[]) {
    setDoc(d => ({
      ...d,
      constraints_and_preferences: { ...d.constraints_and_preferences, [field]: val },
    }));
  }
  function setClaimBoundaries(field: keyof ClaimBoundaries, val: string[]) {
    setDoc(d => ({
      ...d,
      claim_boundaries: { ...d.claim_boundaries, [field]: val },
    }));
  }

  // ── Validation ───────────────────────────────────────────────────────────

  function validate(): string | null {
    if (!doc.candidate.name.trim()) return "Name is required.";
    if (!doc.candidate.headline?.trim()) return "Headline is required.";
    if (!doc.candidate.summary?.trim()) return "Summary is required.";
    if (!doc.domains.primary.length) return "At least one primary domain is required.";
    for (let i = 0; i < highlights.length; i++) {
      const h = highlights[i];
      if (!h.area.trim()) return `Experience highlight ${i + 1}: Area is required.`;
      if (!h.impact.length) return `Experience highlight ${i + 1}: At least one impact item is required.`;
    }
    return null;
  }

  // ── Submit ───────────────────────────────────────────────────────────────

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const err = validate();
    if (err) { setError(err); return; }
    setError(null);
    setSuccess(false);
    setLoading(true);

    const payload: CandidateProfileDocument = {
      ...doc,
      // Strip internal _key before saving
      experience_highlights: highlights.map(({ _key, ...h }) => h),
    };

    try {
      const result = await candidateProfile.upsert({
        profile: payload,
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

  const { candidate, domains, technical_skills: ts, leadership, ai_tooling_practice: ai,
    role_fit_themes, constraints_and_preferences: cx, claim_boundaries: cb } = doc;

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <form onSubmit={handleSubmit} className="space-y-6">

      {/* 1 — Candidate basics */}
      <SectionCard title="Candidate basics">
        <Field label="Name" configKey="candidate.name" required>
          <Input
            value={candidate.name}
            onChange={e => setCandidate("name", e.target.value)}
            placeholder="Jane Smith"
          />
        </Field>
        <Field label="Headline" configKey="candidate.headline" required>
          <Input
            value={candidate.headline ?? ""}
            onChange={e => setCandidate("headline", e.target.value)}
            placeholder="Senior backend engineer & engineering leader"
          />
        </Field>
        <Field label="Summary" configKey="candidate.summary" required>
          <Textarea
            value={candidate.summary ?? ""}
            onChange={e => setCandidate("summary", e.target.value)}
            placeholder="Brief factual summary of your background and key strengths..."
            className="min-h-[100px]"
          />
        </Field>
      </SectionCard>

      {/* 2 — Domain expertise */}
      <SectionCard title="Domain expertise">
        <Field label="Primary domains" configKey="domains.primary" required>
          <ArrayListEditor
            value={domains.primary}
            onChange={v => setDomains("primary", v)}
            placeholder={"fintech\ncrypto_exchange\nblockchain"}
            rows={4}
          />
        </Field>
        <Field label="Secondary domains" configKey="domains.secondary">
          <ArrayListEditor
            value={domains.secondary}
            onChange={v => setDomains("secondary", v)}
            placeholder={"payments\nrisk_controls\nregulated_systems"}
            rows={3}
          />
        </Field>
      </SectionCard>

      {/* 3 — Experience highlights */}
      <SectionCard title="Key experience highlights">
        <div className="space-y-4">
          {highlights.map((h, i) => (
            <ExperienceHighlightCard
              key={h._key}
              value={h}
              index={i}
              onChange={updated =>
                setHighlights(prev =>
                  prev.map((p, pi) => (pi === i ? { ...updated, _key: h._key } : p))
                )
              }
              onRemove={() => setHighlights(prev => prev.filter((_, pi) => pi !== i))}
            />
          ))}
          <button
            type="button"
            onClick={() => setHighlights(prev => [...prev, toKeyed({ ...EMPTY_HIGHLIGHT })])}
            className="flex items-center gap-2 text-sm text-indigo-600 hover:text-indigo-800 border-2 border-dashed border-indigo-200 hover:border-indigo-400 rounded-lg w-full justify-center py-2.5 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Add experience highlight
          </button>
        </div>
      </SectionCard>

      {/* 4 — Technical skills */}
      <SectionCard title="Technical skills">
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
              ["security_auth_patterns", "Security / auth patterns"],
              ["scalability_reliability_patterns", "Scalability / reliability patterns"],
            ] as [keyof TechnicalSkills, string][]
          ).map(([field, label]) => (
            <div key={field}>
              <label className="block text-sm font-medium text-gray-700 mb-0.5">{label}</label>
              <FieldHelp configKey={`technical_skills.${field}`} />
              <ArrayListEditor
                value={ts[field]}
                onChange={v => setTech(field, v)}
                rows={3}
                className="mt-1"
              />
            </div>
          ))}
        </div>
      </SectionCard>

      {/* 5 — Leadership */}
      <SectionCard title="Leadership style and scope">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-0.5">Largest team led</label>
            <FieldHelp configKey="leadership.scope.team_size_max" />
            <Input
              type="number"
              min={0}
              value={leadership.scope.team_size_max ?? ""}
              onChange={e => {
                const n = e.target.value === "" ? null : parseInt(e.target.value, 10);
                setLeadership(l => ({
                  ...l,
                  scope: { ...l.scope, team_size_max: isNaN(n as number) ? null : n },
                }));
              }}
              placeholder="20"
              className="mt-1"
            />
          </div>
          <Field label="Leadership style keywords" configKey="leadership.scope.style_keywords">
            <ArrayListEditor
              value={leadership.scope.style_keywords}
              onChange={v => setLeadership(l => ({ ...l, scope: { ...l.scope, style_keywords: v } }))}
              placeholder={"results\nclarity\naccountability"}
              rows={3}
            />
          </Field>
        </div>
        <Field label="Leadership practices" configKey="leadership.practices">
          <ArrayListEditor
            value={leadership.practices}
            onChange={v => setLeadership(l => ({ ...l, practices: v }))}
            rows={4}
          />
        </Field>
        <Field label="Risk management" configKey="leadership.risk_management">
          <ArrayListEditor
            value={leadership.risk_management}
            onChange={v => setLeadership(l => ({ ...l, risk_management: v }))}
            rows={3}
          />
        </Field>
      </SectionCard>

      {/* 6 — AI tooling */}
      <SectionCard title="AI tools and practices">
        <div className="grid grid-cols-2 gap-4">
          {(
            [
              ["hands_on_tools", "Hands-on tools"],
              ["usage_patterns", "Usage patterns"],
              ["principles", "Principles"],
              ["concepts_familiarity", "Concepts familiarity"],
            ] as [keyof AIToolingPractice, string][]
          ).map(([field, label]) => (
            <Field key={field} label={label} configKey={`ai_tooling_practice.${field}`}>
              <ArrayListEditor
                value={ai[field]}
                onChange={v => setAI(field, v)}
                rows={3}
              />
            </Field>
          ))}
        </div>
      </SectionCard>

      {/* 7 — Role fit themes */}
      <SectionCard title="Strong-fit role themes">
        <Field label="Role fit themes" configKey="role_fit_themes">
          <ArrayListEditor
            value={role_fit_themes}
            onChange={v => setDoc(d => ({ ...d, role_fit_themes: v }))}
            placeholder={"fintech_backend\ncrypto_and_blockchain_infrastructure\nhigh_throughput_low_latency_systems"}
            rows={5}
          />
        </Field>
      </SectionCard>

      {/* 8 — Resume constraints */}
      <SectionCard title="Resume constraints and preferences">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Work context" configKey="constraints_and_preferences.work_context">
            <ArrayListEditor
              value={cx.work_context}
              onChange={v => setConstraints("work_context", v)}
              rows={3}
            />
          </Field>
          <Field label="Communication" configKey="constraints_and_preferences.communication">
            <ArrayListEditor
              value={cx.communication}
              onChange={v => setConstraints("communication", v)}
              rows={3}
            />
          </Field>
        </div>
        <Field label="Resume constraints" configKey="constraints_and_preferences.resume_constraint">
          <ArrayListEditor
            value={cx.resume_constraint}
            onChange={v => setConstraints("resume_constraint", v)}
            placeholder={"when describing current contracting work, clearly mark as independent contractor\navoid implying employee status where relevant"}
            rows={3}
          />
        </Field>
      </SectionCard>

      {/* 9 — Claim boundaries */}
      <SectionCard title="Claim boundaries">
        <Field label="Security / auth boundaries" configKey="claim_boundaries.security_auth">
          <ArrayListEditor
            value={cb.security_auth}
            onChange={v => setClaimBoundaries("security_auth", v)}
            placeholder={"Not a direct OAuth2/OIDC/SAML implementation\nArchitecture aligns with JWT-style principles"}
            rows={4}
          />
        </Field>
        <Field label="Domain limits" configKey="claim_boundaries.domain_limits">
          <ArrayListEditor
            value={cb.domain_limits}
            onChange={v => setClaimBoundaries("domain_limits", v)}
            placeholder={"Do not imply direct ownership of unrelated SaaS business domains unless explicitly supported\nDo not claim unsupported protocol or compliance framework implementation"}
            rows={4}
          />
        </Field>
        <Field label="Employment constraints" configKey="claim_boundaries.employment_constraints">
          <ArrayListEditor
            value={cb.employment_constraints}
            onChange={v => setClaimBoundaries("employment_constraints", v)}
            placeholder={"Current AI-lab work must be clearly labeled as independent contractor work\nAvoid implying employee status where not applicable"}
            rows={3}
          />
        </Field>
      </SectionCard>

      {/* Footer */}
      {error && <p className="text-sm text-red-600">✗ {error}</p>}
      {success && <p className="text-sm text-green-600">✓ Profile saved successfully.</p>}

      <Button type="submit" loading={loading}>
        Save profile
      </Button>
    </form>
  );
}
