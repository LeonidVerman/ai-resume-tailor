// frontend/src/app/try/page.tsx
//
// Public guest generation flow (issue #155, Phase 3).
//
// Step 1: Turnstile + Terms/Privacy consent → resume upload (PDF/DOCX).
//         The guest session is created lazily on upload initiation — never
//         on page view. Tokens land in the standard art_* storage slots so
//         the whole existing API client works unchanged.
// Step 2: job description — paste only (URL fetch is registered-only).
// Step 3: one synchronous generation → PDF-only result view.

"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { AlertCircle, Rocket, UserPlus, Zap } from "lucide-react";
import { PublicShell } from "@/components/layout/PublicShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { ResumeUpload } from "@/components/resume/ResumeUpload";
import { TurnstileWidget } from "@/components/guest/TurnstileWidget";
import { GuestResult } from "@/components/guest/GuestResult";
import {
  guest,
  legal,
  jobDescriptions,
  generations,
  ApiError,
} from "@/lib/api";
import { setStoredToken, setStoredRefreshToken } from "@/lib/auth";
import type {
  GenerationResponse,
  JobDescriptionResponse,
  LegalCurrentResponse,
  StructuredResumeResponse,
} from "@/types/api";

const GUEST_ACCEPTED_FILES = [".pdf", ".docx"];

function StepHeader({ n, title }: { n: number; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold text-white shrink-0">
        {n}
      </span>
      <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
    </div>
  );
}

export default function TryPage() {
  // Consent gate (step 1 prerequisites)
  const [legalDocs, setLegalDocs] = useState<LegalCurrentResponse | null>(null);
  const [legalError, setLegalError] = useState(false);
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);

  // Guest session is created at most once per page visit.
  const sessionCreatedRef = useRef(false);

  // Flow state
  const [resume, setResume] = useState<StructuredResumeResponse | null>(null);
  const [jd, setJd] = useState<JobDescriptionResponse | null>(null);
  const [result, setResult] = useState<GenerationResponse | null>(null);
  const [trialUnavailable, setTrialUnavailable] = useState(false);

  // Step 2 (job description) state
  const [jdText, setJdText] = useState("");
  const [jdSaving, setJdSaving] = useState(false);
  const [jdError, setJdError] = useState<string | null>(null);
  const [jobTitle, setJobTitle] = useState("");
  const [company, setCompany] = useState("");

  // Step 3 (generation) state
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [genRegisterCta, setGenRegisterCta] = useState(false);

  useEffect(() => {
    legal
      .current()
      .then(setLegalDocs)
      .catch(() => setLegalError(true));
  }, []);

  /** Detect the backend kill switch and switch to the fatal message. */
  function handleGuestError(e: unknown): void {
    if (
      e instanceof ApiError &&
      e.status === 403 &&
      /not available/i.test(e.detail)
    ) {
      setTrialUnavailable(true);
    }
  }

  // Called by ResumeUpload right before the first upload request.
  async function ensureGuestSession() {
    if (sessionCreatedRef.current) return;
    if (!legalDocs || !turnstileToken || !consent) {
      throw new ApiError(
        0,
        "Complete the verification and accept the terms first."
      );
    }
    try {
      const session = await guest.createSession({
        turnstile_token: turnstileToken,
        terms_document_id: legalDocs.terms.id,
        privacy_document_id: legalDocs.privacy.id,
      });
      setStoredToken(session.access_token);
      setStoredRefreshToken(session.refresh_token);
      sessionCreatedRef.current = true;
    } catch (e) {
      handleGuestError(e);
      throw e;
    }
  }

  async function handleJdSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!jdText.trim()) {
      setJdError("Paste the job description text first.");
      return;
    }
    setJdError(null);
    setJdSaving(true);
    try {
      const created = await jobDescriptions.manual({ raw_text: jdText.trim() });
      setJd(created);
      setJobTitle(created.metadata?.job_title ?? "");
      setCompany(created.metadata?.company ?? "");
    } catch (err) {
      handleGuestError(err);
      setJdError(
        err instanceof ApiError ? err.detail : "Could not save the job description."
      );
    } finally {
      setJdSaving(false);
    }
  }

  const jdCorrected =
    jd !== null &&
    (jobTitle.trim() !== (jd.metadata?.job_title ?? "") ||
      company.trim() !== (jd.metadata?.company ?? ""));

  // Inline correction: re-create the JD with the corrected title/company
  // (there is no update endpoint; the raw text is unchanged).
  async function handleJdCorrection() {
    if (!jd) return;
    setJdError(null);
    setJdSaving(true);
    try {
      const recreated = await jobDescriptions.manual({
        raw_text: jd.raw_text,
        job_title: jobTitle.trim() || undefined,
        company: company.trim() || undefined,
      });
      await jobDescriptions.delete(jd.id).catch(() => undefined);
      setJd(recreated);
      setJobTitle(recreated.metadata?.job_title ?? jobTitle.trim());
      setCompany(recreated.metadata?.company ?? company.trim());
    } catch (err) {
      handleGuestError(err);
      setJdError(
        err instanceof ApiError ? err.detail : "Could not update the job details."
      );
    } finally {
      setJdSaving(false);
    }
  }

  async function handleJdReset() {
    if (jd) await jobDescriptions.delete(jd.id).catch(() => undefined);
    setJd(null);
    setJdText("");
    setJobTitle("");
    setCompany("");
    setJdError(null);
  }

  async function handleGenerate() {
    if (!resume || !jd) return;
    setGenError(null);
    setGenRegisterCta(false);
    setGenerating(true);
    try {
      const res = await generations.create({
        job_description_id: jd.id,
        structured_resume_id: resume.id,
      });
      if (res.status === "failed") {
        setGenError(res.message ?? "Generation failed. Please try again.");
      } else {
        setResult(res);
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    } catch (e) {
      handleGuestError(e);
      if (e instanceof ApiError && e.status === 429) {
        setGenError(
          e.detail || "The free trial limit was reached — create a free account to continue."
        );
        setGenRegisterCta(true);
      } else if (e instanceof ApiError && e.status === 403) {
        if (!/not available/i.test(e.detail)) {
          setGenError(e.detail.replace(/^[A-Z_]+:\s*/, ""));
          setGenRegisterCta(true);
        }
      } else {
        setGenError(e instanceof ApiError ? e.detail : "Generation failed. Please try again.");
      }
    } finally {
      setGenerating(false);
    }
  }

  // ── Kill switch / trial unavailable ─────────────────────────────────────
  if (trialUnavailable) {
    return (
      <PublicShell>
        <Card>
          <CardBody className="py-10 text-center space-y-3">
            <AlertCircle className="h-8 w-8 text-amber-500 mx-auto" />
            <h1 className="text-lg font-semibold text-gray-900">
              The free trial is currently unavailable
            </h1>
            <p className="text-sm text-gray-500 max-w-md mx-auto">
              We have temporarily paused guest generations. Create a free
              account to tailor your resume, or check back later.
            </p>
            <div className="pt-2">
              <Link
                href="/register"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-indigo-600 text-sm font-medium text-white hover:bg-indigo-500 transition-colors"
              >
                <UserPlus className="h-4 w-4" />
                Create free account
              </Link>
            </div>
          </CardBody>
        </Card>
      </PublicShell>
    );
  }

  // ── Result view ─────────────────────────────────────────────────────────
  if (result) {
    return (
      <PublicShell>
        <GuestResult result={result} />
      </PublicShell>
    );
  }

  const uploadReady = Boolean(legalDocs && turnstileToken && consent);

  return (
    <PublicShell>
      {/* Hero */}
      <div className="text-center mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900 flex items-center justify-center gap-2">
          <Rocket className="h-6 w-6 text-indigo-600" />
          Try CVRocket free — no account needed
        </h1>
        <p className="text-gray-500 mt-2 max-w-xl mx-auto">
          Upload your resume, paste a job description, and get a tailored
          resume and cover letter in about a minute. One free generation,
          no sign-up.
        </p>
      </div>

      <div className="space-y-6">
        {/* Step 1: verification, consent, upload */}
        <Card className="shadow-sm">
          <CardHeader>
            <StepHeader n={1} title="Upload your resume" />
          </CardHeader>
          <CardBody className="space-y-4 py-5">
            {resume ? (
              <div className="text-sm text-green-700 bg-green-50 border border-green-100 rounded-lg px-4 py-3">
                <span className="font-medium">Resume processed</span> —{" "}
                {resume.resume.name}
                {resume.resume.experience?.[0]?.role
                  ? `, ${resume.resume.experience[0].role}`
                  : ""}
                <p className="text-xs text-green-600 mt-1">
                  Wrong file? Upload another below — it replaces this one.
                </p>
              </div>
            ) : null}

            {legalError && (
              <p className="text-sm text-red-600">
                Could not load the Terms and Privacy documents. Please reload
                the page.
              </p>
            )}

            {!sessionCreatedRef.current && (
              <>
                <label className="flex items-start gap-2.5 text-sm text-gray-600 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    className="mt-0.5 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <span>
                    I agree to the{" "}
                    <Link
                      href="/legal/terms"
                      target="_blank"
                      className="text-indigo-600 hover:underline"
                    >
                      Terms of Service
                    </Link>{" "}
                    and{" "}
                    <Link
                      href="/legal/privacy"
                      target="_blank"
                      className="text-indigo-600 hover:underline"
                    >
                      Privacy Notice
                    </Link>
                    . My uploaded files are deleted after 7 days unless I
                    create an account.
                  </span>
                </label>
                <TurnstileWidget onToken={setTurnstileToken} />
              </>
            )}

            <ResumeUpload
              accept={GUEST_ACCEPTED_FILES}
              disabled={!uploadReady}
              beforeUpload={ensureGuestSession}
              onUpload={setResume}
            />
            {!uploadReady && !legalError && (
              <p className="text-xs text-gray-400">
                Accept the terms and complete the verification to enable the
                upload.
              </p>
            )}
          </CardBody>
        </Card>

        {/* Step 2: job description (paste only for guests) */}
        <Card className="shadow-sm">
          <CardHeader>
            <StepHeader n={2} title="Paste the job description" />
          </CardHeader>
          <CardBody className="space-y-4 py-5">
            {!resume ? (
              <p className="text-sm text-gray-400">Upload your resume first.</p>
            ) : jd ? (
              <div className="space-y-3">
                <div className="text-sm text-green-700 bg-green-50 border border-green-100 rounded-lg px-4 py-3">
                  <span className="font-medium">Job description saved</span>
                  {jd.metadata?.job_title || jd.metadata?.company ? (
                    <>
                      {" "}
                      — detected {jd.metadata?.job_title ?? "role"}
                      {jd.metadata?.company ? ` @ ${jd.metadata.company}` : ""}
                    </>
                  ) : (
                    " — we could not detect the role details; correct them below."
                  )}
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <Input
                    label="Job title"
                    value={jobTitle}
                    onChange={(e) => setJobTitle(e.target.value)}
                    placeholder="Senior Engineer"
                  />
                  <Input
                    label="Company"
                    value={company}
                    onChange={(e) => setCompany(e.target.value)}
                    placeholder="Acme Corp"
                  />
                </div>
                <div className="flex items-center gap-3">
                  {jdCorrected && (
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={jdSaving}
                      onClick={handleJdCorrection}
                    >
                      Save corrections
                    </Button>
                  )}
                  <button
                    type="button"
                    onClick={handleJdReset}
                    className="text-sm text-gray-500 hover:text-gray-700 hover:underline"
                  >
                    Use a different job description
                  </button>
                </div>
                {jdError && <p className="text-sm text-red-600">✗ {jdError}</p>}
              </div>
            ) : (
              <form onSubmit={handleJdSubmit} className="space-y-4">
                <Textarea
                  label="Job description"
                  value={jdText}
                  onChange={(e) => setJdText(e.target.value)}
                  placeholder="Paste the full job description text here..."
                  className="min-h-[180px]"
                  hint="Copy the posting text from the job board and paste it here."
                />
                {jdError && <p className="text-sm text-red-600">✗ {jdError}</p>}
                <Button type="submit" loading={jdSaving}>
                  Analyze job description
                </Button>
              </form>
            )}
          </CardBody>
        </Card>

        {/* Step 3: generate */}
        <Card className="shadow-sm">
          <CardHeader>
            <StepHeader n={3} title="Generate" />
          </CardHeader>
          <CardBody className="space-y-4 py-5">
            {!resume || !jd ? (
              <p className="text-sm text-gray-400">
                Complete the steps above to generate.
              </p>
            ) : (
              <>
                {genError && (
                  <div className="text-sm text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-4 py-3 space-y-2">
                    <p>{genError}</p>
                    {genRegisterCta && (
                      <Link
                        href="/register"
                        className="inline-flex items-center gap-2 text-indigo-600 font-medium hover:underline"
                      >
                        <UserPlus className="h-4 w-4" />
                        Create free account
                      </Link>
                    )}
                  </div>
                )}
                <Button
                  onClick={handleGenerate}
                  loading={generating}
                  size="lg"
                  className="w-full px-5 py-3 text-base font-semibold shadow-md disabled:shadow-none"
                >
                  <Zap className="h-5 w-5" />
                  {generating
                    ? "Generating… this takes 30–90 seconds"
                    : "Generate tailored resume and cover letter"}
                </Button>
                {generating && (
                  <p className="text-sm text-gray-500 text-center">
                    Tailoring your resume and writing the cover letter — keep
                    this tab open.
                  </p>
                )}
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </PublicShell>
  );
}
