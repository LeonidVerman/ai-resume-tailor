// frontend/src/components/generation/GenerationForm.tsx
"use client";

import { useState, useEffect } from "react";
import { FileText, Briefcase, Zap, AlertCircle, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ResumeCard } from "@/components/resume/ResumeCard";
import { Card, CardBody } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { resumes, jobDescriptions, generations, billing, ApiError } from "@/lib/api";
import type {
  StructuredResumeSummary,
  JobDescriptionSummary,
  BillingStatus,
  GenerationResponse,
  GenerationMode,
} from "@/types/api";
import { formatDate } from "@/lib/utils";
import { cn } from "@/lib/utils";

interface GenerationFormProps {
  onGenerated?: (result: GenerationResponse) => void;
}

const MODE_OPTIONS: { value: GenerationMode; label: string; description: string }[] = [
  {
    value: "conservative",
    label: "Conservative",
    description: "Light tailoring — preserves original phrasing, minimal rewrites.",
  },
  {
    value: "normal",
    label: "Normal",
    description: "Balanced — stronger JD alignment with controlled rewrites.",
  },
  {
    value: "aggressive",
    label: "Aggressive",
    description: "Maximum role fit — assertive rewrites, dense JD anchoring.",
  },
];

export function GenerationForm({ onGenerated }: GenerationFormProps) {
  const [resumeList, setResumeList] = useState<StructuredResumeSummary[]>([]);
  const [jdList, setJdList] = useState<JobDescriptionSummary[]>([]);
  const [billingStatus, setBillingStatus] = useState<BillingStatus | null>(null);
  const [selectedResume, setSelectedResume] = useState<number | null>(null);
  const [selectedJd, setSelectedJd] = useState<number | null>(null);
  const [generationMode, setGenerationMode] = useState<GenerationMode>("conservative");
  const [loading, setLoading] = useState(false);
  const [loadingData, setLoadingData] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingResumeId, setDeletingResumeId] = useState<number | null>(null);
  const [deletingJdId, setDeletingJdId] = useState<number | null>(null);

  useEffect(() => {
    Promise.all([
      resumes.list().catch(() => [] as StructuredResumeSummary[]),
      jobDescriptions.list().catch(() => [] as JobDescriptionSummary[]),
      billing.status().catch(() => null),
    ]).then(([r, j, b]) => {
      setResumeList(r);
      setJdList(j);
      setBillingStatus(b);
      if (r.length === 1) setSelectedResume(r[0].id);
      if (j.length === 1) setSelectedJd(j[0].id);
    }).finally(() => setLoadingData(false));
  }, []);

  const quotaExhausted =
    billingStatus !== null &&
    billingStatus.monthly_used >= billingStatus.monthly_limit &&
    billingStatus.extra_credits === 0;

  async function handleDeleteResume(id: number) {
    setDeletingResumeId(id);
    try {
      await resumes.delete(id);
      setResumeList((prev) => prev.filter((r) => r.id !== id));
      if (selectedResume === id) setSelectedResume(null);
    } catch {
      // ignore
    } finally {
      setDeletingResumeId(null);
    }
  }

  async function handleDeleteJd(id: number) {
    setDeletingJdId(id);
    try {
      await jobDescriptions.delete(id);
      setJdList((prev) => prev.filter((j) => j.id !== id));
      if (selectedJd === id) setSelectedJd(null);
    } catch {
      // ignore
    } finally {
      setDeletingJdId(null);
    }
  }

  async function handleGenerate() {
    if (!selectedResume || !selectedJd) {
      setError("Select a resume and a job description first.");
      return;
    }
    if (quotaExhausted) {
      setError("Free generation quota exhausted. Please upgrade your plan.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const result = await generations.create({
        job_description_id: selectedJd,
        structured_resume_id: selectedResume,
        generation_mode: generationMode,
      });
      onGenerated?.(result);
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setError("Generation quota exhausted. Go to Billing to upgrade or buy extra credits.");
      } else {
        setError(e instanceof ApiError ? e.detail : "Generation failed.");
      }
    } finally {
      setLoading(false);
    }
  }

  if (loadingData) {
    return <p className="text-sm text-gray-500">Loading your resumes and jobs...</p>;
  }

  return (
    <div className="space-y-8">
      {/* Quota warning */}
      {billingStatus && (
        <div className={cn(
          "flex items-center gap-2 px-4 py-3 rounded-lg text-sm",
          quotaExhausted
            ? "bg-red-50 text-red-700 border border-red-200"
            : billingStatus.monthly_used >= billingStatus.monthly_limit
            ? "bg-amber-50 text-amber-700 border border-amber-200"
            : "bg-blue-50 text-blue-700 border border-blue-200"
        )}>
          <AlertCircle className="h-4 w-4 shrink-0" />
          {quotaExhausted
            ? `Quota exhausted (${billingStatus.monthly_used}/${billingStatus.monthly_limit} used, ${billingStatus.extra_credits} credits). Go to Billing to upgrade.`
            : billingStatus.monthly_used >= billingStatus.monthly_limit
            ? `Monthly quota reached — using extra credits (${billingStatus.extra_credits} remaining).`
            : `${billingStatus.monthly_used}/${billingStatus.monthly_limit} generations used this month.`}
        </div>
      )}

      {/* Step 1: Resume selection */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold text-white shrink-0">1</span>
          <h3 className="text-sm font-semibold text-gray-900">Select resume</h3>
        </div>
        {resumeList.length === 0 ? (
          <p className="text-sm text-gray-400 pl-10">No resumes uploaded yet.</p>
        ) : (
          <div className="space-y-2 pl-10">
            {resumeList.map((r) => (
              <ResumeCard
                key={r.id}
                resume={r}
                selected={selectedResume === r.id}
                onSelect={setSelectedResume}
                onDelete={handleDeleteResume}
                deleting={deletingResumeId === r.id}
              />
            ))}
          </div>
        )}
      </div>

      {/* Step 2: JD selection */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold text-white shrink-0">2</span>
          <h3 className="text-sm font-semibold text-gray-900">Select job description</h3>
        </div>
        {jdList.length === 0 ? (
          <p className="text-sm text-gray-400 pl-10">No job descriptions saved yet.</p>
        ) : (
          <div className="space-y-2 pl-10">
            {jdList.map((jd) => (
              <Card
                key={jd.id}
                onClick={() => setSelectedJd(jd.id)}
                className={cn(
                  "cursor-pointer transition-all",
                  selectedJd === jd.id
                    ? "border-indigo-300 bg-indigo-50 shadow-sm"
                    : "border-gray-100 hover:border-gray-200 hover:bg-gray-50 hover:shadow-sm"
                )}
              >
                <CardBody className="flex items-center gap-3 py-3.5">
                  <div className={cn(
                    "shrink-0 h-9 w-9 rounded-lg flex items-center justify-center transition-colors",
                    selectedJd === jd.id ? "bg-indigo-100" : "bg-amber-50"
                  )}>
                    <Briefcase className={cn("h-5 w-5", selectedJd === jd.id ? "text-indigo-600" : "text-amber-600")} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900">
                      {jd.job_title ?? "Untitled role"}{" "}
                      {jd.company && <span className="text-gray-500">@ {jd.company}</span>}
                    </p>
                    <p className="text-xs text-gray-400 mt-0.5">{formatDate(jd.created_at)}</p>
                  </div>
                  <Badge variant="default">
                    {jd.source_url ? "scraped" : "manual"}
                  </Badge>
                  {selectedJd === jd.id && (
                    <div className="shrink-0 h-5 w-5 rounded-full bg-indigo-600 flex items-center justify-center">
                      <svg className="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    </div>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); handleDeleteJd(jd.id); }}
                    disabled={deletingJdId === jd.id}
                    className="shrink-0 p-1 text-gray-300 hover:text-red-600 disabled:opacity-40 transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </CardBody>
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Step 3: Generation mode selector */}
      <div>
        <div className="flex items-center gap-3 mb-3">
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold text-white shrink-0">3</span>
          <h3 className="text-sm font-semibold text-gray-900">Generation mode</h3>
        </div>
        <div className="grid grid-cols-3 gap-2 pl-10">
          {MODE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setGenerationMode(opt.value)}
              className={cn(
                "rounded-lg border px-3 py-2.5 text-left transition-all",
                generationMode === opt.value
                  ? opt.value === "conservative"
                    ? "border-gray-300 bg-gray-50 ring-2 ring-gray-300/50"
                    : opt.value === "normal"
                    ? "border-blue-300 bg-blue-50 ring-2 ring-blue-300/50"
                    : "border-orange-300 bg-orange-50 ring-2 ring-orange-300/50"
                  : "border-gray-100 bg-white hover:border-gray-200 hover:shadow-sm"
              )}
            >
              <span className={cn(
                "block text-xs font-semibold",
                generationMode === opt.value
                  ? opt.value === "conservative"
                    ? "text-gray-700"
                    : opt.value === "normal"
                    ? "text-blue-700"
                    : "text-orange-700"
                  : "text-gray-600"
              )}>
                {opt.label}
              </span>
              <span className="mt-0.5 block text-xs text-gray-500 leading-tight">
                {opt.description}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Generate action — primary CTA, visually dominant */}
      <div className="pt-6 border-t border-gray-100">
        {error && <p className="text-sm text-red-600 mb-4">✗ {error}</p>}
        <Button
          onClick={handleGenerate}
          loading={loading}
          disabled={!selectedResume || !selectedJd || quotaExhausted}
          size="lg"
          className="w-full px-5 py-3 text-base font-semibold shadow-md disabled:shadow-none"
        >
          <Zap className="h-5 w-5" />
          {loading ? "Generating… (this takes 15–30 seconds)" : "Generate tailored application materials"}
        </Button>
      </div>
    </div>
  );
}
