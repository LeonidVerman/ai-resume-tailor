// frontend/src/components/candidate-profile/ProfileForm.tsx
"use client";

import { useState } from "react";
import { Plus, Wand2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { ArrayListEditor } from "./ArrayListEditor";
import { FieldHelp } from "./FieldHelp";
import { ExperienceHighlightCard } from "./ExperienceHighlightCard";
import { SelectResumeModal } from "./SelectResumeModal";
import { candidateProfile, ApiError } from "@/lib/api";
import {
  EXAMPLE_CANDIDATE,
  EXAMPLE_DOMAINS,
  EXAMPLE_HIGHLIGHTS,
  EXAMPLE_TECHNICAL_SKILLS,
  EXAMPLE_LEADERSHIP,
  EXAMPLE_AI_TOOLING,
  EXAMPLE_ROLE_THEMES,
  EXAMPLE_CONSTRAINTS,
  EXAMPLE_CLAIM_BOUNDARIES,
} from "@/config/exampleProfileData";
import type {
  CandidateProfileDocument,
  CandidateProfileResponse,
  CandidateContacts,
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
  contacts: { email: "", phone: "", linkedin_url: null, location: null },
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
    contacts: { ...d.contacts, ...stored.contacts },
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

function LoadExampleButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-xs text-indigo-500 hover:text-indigo-700 hover:underline underline-offset-2 transition-colors"
    >
      Load from example
    </button>
  );
}

function SectionCard({
  title, children, actions,
}: {
  title: string; children: React.ReactNode; actions?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          {actions}
        </div>
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
  const [fillModalOpen, setFillModalOpen] = useState(false);

  // Per-section reset keys: incrementing forces ArrayListEditors in that section
  // to re-sync their local textarea state from the updated value prop.
  const [resetKeys, setResetKeys] = useState<Record<string, number>>({});
  const bumpKey = (section: string) =>
    setResetKeys(k => ({ ...k, [section]: (k[section] ?? 0) + 1 }));
  const rk = (section: string) => resetKeys[section] ?? 0;

  // ── Field setters ────────────────────────────────────────────────────────

  function setCandidate(field: keyof CandidateIdentity, val: string) {
    setDoc(d => ({ ...d, candidate: { ...d.candidate, [field]: val } }));
  }
  function setContacts(field: keyof CandidateContacts, val: string) {
    setDoc(d => ({ ...d, contacts: { ...d.contacts, [field]: val } }));
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

  // ── Example loading ──────────────────────────────────────────────────────

  function confirmLoad(hasContent: boolean): boolean {
    if (!hasContent) return true;
    return window.confirm(
      "Replace this section with example content?\nThis will update only the fields on the current section."
    );
  }

  function loadCandidateExample() {
    const has = !!(doc.candidate.name || doc.candidate.headline || doc.candidate.summary);
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, candidate: EXAMPLE_CANDIDATE }));
    // candidate fields are controlled inputs — no resetKey needed
  }

  function loadDomainsExample() {
    const has = doc.domains.primary.length > 0 || doc.domains.secondary.length > 0;
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, domains: EXAMPLE_DOMAINS }));
    bumpKey("domains");
  }

  function loadHighlightsExample() {
    if (!confirmLoad(highlights.length > 0)) return;
    setHighlights(EXAMPLE_HIGHLIGHTS.map(toKeyed));
    // ExperienceHighlightCard remounts on key change — no extra resetKey needed
  }

  function loadTechExample() {
    const has = Object.values(doc.technical_skills).some(arr => arr.length > 0);
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, technical_skills: EXAMPLE_TECHNICAL_SKILLS }));
    bumpKey("tech");
  }

  function loadLeadershipExample() {
    const has = !!(
      doc.leadership.scope.team_size_max ||
      doc.leadership.scope.style_keywords.length ||
      doc.leadership.practices.length ||
      doc.leadership.risk_management.length
    );
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, leadership: EXAMPLE_LEADERSHIP }));
    bumpKey("leadership");
  }

  function loadAIExample() {
    const has = Object.values(doc.ai_tooling_practice).some(arr => arr.length > 0);
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, ai_tooling_practice: EXAMPLE_AI_TOOLING }));
    bumpKey("ai");
  }

  function loadThemesExample() {
    if (!confirmLoad(doc.role_fit_themes.length > 0)) return;
    setDoc(d => ({ ...d, role_fit_themes: EXAMPLE_ROLE_THEMES }));
    bumpKey("themes");
  }

  function loadConstraintsExample() {
    const cx = doc.constraints_and_preferences;
    const has = cx.work_context.length > 0 || cx.communication.length > 0 || cx.resume_constraint.length > 0;
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, constraints_and_preferences: EXAMPLE_CONSTRAINTS }));
    bumpKey("constraints");
  }

  function loadClaimsExample() {
    const has = !!(
      doc.claim_boundaries.security_auth.length ||
      doc.claim_boundaries.domain_limits.length ||
      doc.claim_boundaries.employment_constraints.length
    );
    if (!confirmLoad(has)) return;
    setDoc(d => ({ ...d, claim_boundaries: EXAMPLE_CLAIM_BOUNDARIES }));
    bumpKey("claims");
  }

  // ── Fill from resume draft ───────────────────────────────────────────────

  function fillFromDraft(draft: CandidateProfileDocument) {
    const hasContent = !!(
      doc.candidate.name || highlights.length > 0 ||
      doc.domains.primary.length || Object.values(doc.technical_skills).some(a => a.length > 0)
    );
    if (hasContent && !window.confirm(
      "This will replace all profile fields with data from the resume draft.\nContinue?"
    )) {
      return;
    }
    const hydrated = hydrateProfile(draft);
    setDoc(hydrated);
    setHighlights(hydrated.experience_highlights.map(toKeyed));
    // Bump all section reset keys so ArrayListEditors re-sync
    const sections = ["domains", "tech", "leadership", "ai", "themes", "constraints", "claims"];
    setResetKeys(k => {
      const next = { ...k };
      sections.forEach(s => { next[s] = (next[s] ?? 0) + 1; });
      return next;
    });
  }

  // ── Validation ───────────────────────────────────────────────────────────

  function validate(): string | null {
    if (!doc.candidate.name.trim()) return "Name is required.";
    if (!doc.candidate.headline?.trim()) return "Headline is required.";
    if (!doc.candidate.summary?.trim()) return "Summary is required.";
    if (!doc.contacts.email.trim()) return "Email is required.";
    if (!doc.contacts.phone.trim()) return "Phone is required.";
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

  const { candidate, contacts, domains, technical_skills: ts, leadership, ai_tooling_practice: ai,
    role_fit_themes, constraints_and_preferences: cx, claim_boundaries: cb } = doc;

  // ── Render ───────────────────────────────────────────────────────────────

  return (
    <>
    <SelectResumeModal
      open={fillModalOpen}
      onClose={() => setFillModalOpen(false)}
      onApply={(draft) => fillFromDraft(draft)}
    />
    <form onSubmit={handleSubmit} className="space-y-6">

      {/* Fill from resume toolbar */}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setFillModalOpen(true)}
          className="flex items-center gap-1.5 text-sm text-indigo-600 hover:text-indigo-800 transition-colors"
        >
          <Wand2 className="h-4 w-4" />
          Fill from resume
        </button>
      </div>

      {/* 1 — Candidate basics */}
      <SectionCard
        title="Candidate basics"
        actions={<LoadExampleButton onClick={loadCandidateExample} />}
      >
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
            placeholder="Software engineer, backend systems and cloud applications"
          />
        </Field>
        <Field label="Summary" configKey="candidate.summary" required>
          <Textarea
            value={candidate.summary ?? ""}
            onChange={e => setCandidate("summary", e.target.value)}
            placeholder="Software engineer with experience building and maintaining backend and full-stack systems. Focused on scalability, reliability, and delivering production-quality features."
            className="min-h-[100px]"
          />
        </Field>
        <Field label="Email" configKey="contacts.email" required>
          <Input
            type="email"
            value={contacts.email}
            onChange={e => setContacts("email", e.target.value)}
            placeholder="jane@example.com"
          />
        </Field>
        <Field label="Phone" configKey="contacts.phone" required>
          <Input
            type="tel"
            value={contacts.phone}
            onChange={e => setContacts("phone", e.target.value)}
            placeholder="+1 604 123 4567"
          />
        </Field>
        <Field label="LinkedIn URL" configKey="contacts.linkedin_url">
          <Input
            type="url"
            value={contacts.linkedin_url ?? ""}
            onChange={e => setContacts("linkedin_url", e.target.value)}
            placeholder="https://linkedin.com/in/your-profile"
          />
        </Field>
      </SectionCard>

      {/* 2 — Domain expertise */}
      <SectionCard
        title="Domain expertise"
        actions={<LoadExampleButton onClick={loadDomainsExample} />}
      >
        <Field label="Primary domains" configKey="domains.primary" required>
          <ArrayListEditor
            value={domains.primary}
            onChange={v => setDomains("primary", v)}
            placeholder={"Web applications\nBackend systems\nCloud infrastructure"}
            rows={4}
            resetKey={rk("domains")}
          />
        </Field>
        <Field label="Secondary domains" configKey="domains.secondary">
          <ArrayListEditor
            value={domains.secondary}
            onChange={v => setDomains("secondary", v)}
            placeholder={"Enterprise SaaS\nFinancial technology"}
            rows={3}
            resetKey={rk("domains")}
          />
        </Field>
      </SectionCard>

      {/* 3 — Experience highlights */}
      <SectionCard
        title="Key experience highlights"
        actions={<LoadExampleButton onClick={loadHighlightsExample} />}
      >
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
      <SectionCard
        title="Technical skills"
        actions={<LoadExampleButton onClick={loadTechExample} />}
      >
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
                resetKey={rk("tech")}
              />
            </div>
          ))}
        </div>
      </SectionCard>

      {/* 5 — Leadership */}
      <SectionCard
        title="Leadership style and scope"
        actions={<LoadExampleButton onClick={loadLeadershipExample} />}
      >
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
              placeholder="8"
              className="mt-1"
            />
          </div>
          <Field label="Leadership style keywords" configKey="leadership.scope.style_keywords">
            <ArrayListEditor
              value={leadership.scope.style_keywords}
              onChange={v => setLeadership(l => ({ ...l, scope: { ...l.scope, style_keywords: v } }))}
              placeholder={"Ownership\nCollaboration\nExecution"}
              rows={3}
              resetKey={rk("leadership")}
            />
          </Field>
        </div>
        <Field label="Leadership practices" configKey="leadership.practices">
          <ArrayListEditor
            value={leadership.practices}
            onChange={v => setLeadership(l => ({ ...l, practices: v }))}
            rows={4}
            resetKey={rk("leadership")}
          />
        </Field>
        <Field label="Risk management" configKey="leadership.risk_management">
          <ArrayListEditor
            value={leadership.risk_management}
            onChange={v => setLeadership(l => ({ ...l, risk_management: v }))}
            rows={3}
            resetKey={rk("leadership")}
          />
        </Field>
      </SectionCard>

      {/* 6 — AI tooling */}
      <SectionCard
        title="AI tools and practices"
        actions={<LoadExampleButton onClick={loadAIExample} />}
      >
        <div className="grid grid-cols-2 gap-4">
          {(
            [
              ["hands_on_tools", "Hands-on tools", "ChatGPT\nGitHub Copilot\nAI-assisted IDE tools"],
              ["usage_patterns", "Usage patterns", "Debugging issues\nLearning new frameworks and APIs\nImproving development workflows"],
              ["principles", "Principles", "Engineer remains accountable for code quality\nAvoid sharing sensitive data with AI tools"],
              ["concepts_familiarity", "Concepts familiarity", "Large language models\nRAG systems\nAI-assisted development"],
            ] as [keyof AIToolingPractice, string, string][]
          ).map(([field, label, placeholder]) => (
            <Field key={field} label={label} configKey={`ai_tooling_practice.${field}`}>
              <ArrayListEditor
                value={ai[field]}
                onChange={v => setAI(field, v)}
                placeholder={placeholder}
                rows={3}
                resetKey={rk("ai")}
              />
            </Field>
          ))}
        </div>
      </SectionCard>

      {/* 7 — Role fit themes */}
      <SectionCard
        title="Strong-fit role themes"
        actions={<LoadExampleButton onClick={loadThemesExample} />}
      >
        <Field label="Role fit themes" configKey="role_fit_themes">
          <ArrayListEditor
            value={role_fit_themes}
            onChange={v => setDoc(d => ({ ...d, role_fit_themes: v }))}
            placeholder={"Backend development\nScalable system design\nAPI development"}
            rows={5}
            resetKey={rk("themes")}
          />
        </Field>
      </SectionCard>

      {/* 8 — Resume constraints */}
      <SectionCard
        title="Resume constraints and preferences"
        actions={<LoadExampleButton onClick={loadConstraintsExample} />}
      >
        <div className="grid grid-cols-2 gap-4">
          <Field label="Work context" configKey="constraints_and_preferences.work_context">
            <ArrayListEditor
              value={cx.work_context}
              onChange={v => setConstraints("work_context", v)}
              rows={3}
              resetKey={rk("constraints")}
            />
          </Field>
          <Field label="Communication" configKey="constraints_and_preferences.communication">
            <ArrayListEditor
              value={cx.communication}
              onChange={v => setConstraints("communication", v)}
              rows={3}
              resetKey={rk("constraints")}
            />
          </Field>
        </div>
        <Field label="Resume constraints" configKey="constraints_and_preferences.resume_constraint">
          <ArrayListEditor
            value={cx.resume_constraint}
            onChange={v => setConstraints("resume_constraint", v)}
            placeholder={"Maintain accuracy of roles and dates\nKeep claims aligned with actual experience"}
            rows={3}
            resetKey={rk("constraints")}
          />
        </Field>
      </SectionCard>

      {/* 9 — Claim boundaries */}
      <SectionCard
        title="Claim boundaries"
        actions={<LoadExampleButton onClick={loadClaimsExample} />}
      >
        <Field label="Security / auth boundaries" configKey="claim_boundaries.security_auth">
          <ArrayListEditor
            value={cb.security_auth}
            onChange={v => setClaimBoundaries("security_auth", v)}
            placeholder={"Do not claim ownership of large-scale security architecture unless supported"}
            rows={4}
            resetKey={rk("claims")}
          />
        </Field>
        <Field label="Domain limits" configKey="claim_boundaries.domain_limits">
          <ArrayListEditor
            value={cb.domain_limits}
            onChange={v => setClaimBoundaries("domain_limits", v)}
            placeholder={"Avoid overstating leadership scope\nKeep domain claims aligned with actual experience"}
            rows={4}
            resetKey={rk("claims")}
          />
        </Field>
        <Field label="Employment constraints" configKey="claim_boundaries.employment_constraints">
          <ArrayListEditor
            value={cb.employment_constraints}
            onChange={v => setClaimBoundaries("employment_constraints", v)}
            placeholder={"Maintain accuracy of roles and dates\nAvoid implying a different employment status than what applies"}
            rows={3}
            resetKey={rk("claims")}
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
    </>
  );
}
