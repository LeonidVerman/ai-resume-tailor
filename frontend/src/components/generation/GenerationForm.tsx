// frontend/src/components/generation/GenerationForm.tsx
"use client";

import { useState, useEffect } from "react";
import { FileText, Briefcase, Zap, AlertCircle } from "lucide-react";
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
} from "@/types/api";
import { formatDate } from "@/lib/utils";
import { cn } from "@/lib/utils";

interface GenerationFormProps {
  onGenerated?: (result: GenerationResponse) => void;
}

export function GenerationForm({ onGenerated }: GenerationFormProps) {
  const [resumeList, setResumeList] = useState<StructuredResumeSummary[]>([]);
  const [jdList, setJdList] = useState<JobDescriptionSummary[]>([]);
  const [billingStatus, setBillingStatus] = useState<BillingStatus | null>(null);
  const [selectedResume, setSelectedResume] = useState<string | null>(null);
  const [selectedJd, setSelectedJd] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingData, setLoadingData] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
    billingStatus.plan_type === "free" &&
    billingStatus.free_generations_used >= billingStatus.free_generations_limit;

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
      });
      onGenerated?.(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Generation failed.");
    } finally {
      setLoading(false);
    }
  }

  if (loadingData) {
    return <p className="text-sm text-gray-500">Loading your resumes and jobs...</p>;
  }

  return (
    <div className="space-y-6">
      {/* Quota warning */}
      {billingStatus && (
        <div className={cn(
          "flex items-center gap-2 px-4 py-3 rounded-lg text-sm",
          quotaExhausted
            ? "bg-red-50 text-red-700 border border-red-200"
            : "bg-blue-50 text-blue-700 border border-blue-200"
        )}>
          <AlertCircle className="h-4 w-4 shrink-0" />
          {quotaExhausted
            ? `Free quota exhausted (${billingStatus.free_generations_used}/${billingStatus.free_generations_limit} used). Upgrade to continue.`
            : `Free plan: ${billingStatus.free_generations_used}/${billingStatus.free_generations_limit} generations used.`}
        </div>
      )}

      {/* Resume selection */}
      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
          <FileText className="h-4 w-4" /> Select resume
        </h3>
        {resumeList.length === 0 ? (
          <p className="text-sm text-gray-500">No resumes uploaded yet.</p>
        ) : (
          <div className="space-y-2">
            {resumeList.map((r) => (
              <ResumeCard
                key={r.id}
                resume={r}
                selected={selectedResume === r.id}
                onSelect={setSelectedResume}
              />
            ))}
          </div>
        )}
      </div>

      {/* JD selection */}
      <div>
        <h3 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
          <Briefcase className="h-4 w-4" /> Select job description
        </h3>
        {jdList.length === 0 ? (
          <p className="text-sm text-gray-500">No job descriptions saved yet.</p>
        ) : (
          <div className="space-y-2">
            {jdList.map((jd) => (
              <Card
                key={jd.id}
                onClick={() => setSelectedJd(jd.id)}
                className={cn(
                  "cursor-pointer transition-all",
                  selectedJd === jd.id
                    ? "border-indigo-500 ring-2 ring-indigo-200"
                    : "hover:border-gray-300 hover:shadow"
                )}
              >
                <CardBody className="flex items-center gap-3 py-3">
                  <div className="shrink-0 h-9 w-9 rounded-lg bg-amber-50 flex items-center justify-center">
                    <Briefcase className="h-5 w-5 text-amber-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900">
                      {jd.job_title ?? "Untitled role"}{" "}
                      {jd.company && <span className="text-gray-500">@ {jd.company}</span>}
                    </p>
                    <p className="text-xs text-gray-500 mt-0.5">{formatDate(jd.created_at)}</p>
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
                </CardBody>
              </Card>
            ))}
          </div>
        )}
      </div>

      {error && <p className="text-sm text-red-600">✗ {error}</p>}

      <Button
        onClick={handleGenerate}
        loading={loading}
        disabled={!selectedResume || !selectedJd || quotaExhausted}
        size="lg"
        className="w-full"
      >
        <Zap className="h-4 w-4" />
        {loading ? "Generating… (this takes 30–90 seconds)" : "Generate tailored documents"}
      </Button>
    </div>
  );
}
