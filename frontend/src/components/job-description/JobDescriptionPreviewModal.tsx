// frontend/src/components/job-description/JobDescriptionPreviewModal.tsx
"use client";

import { useEffect, useState } from "react";
import { X, AlertTriangle } from "lucide-react";
import { Spinner } from "@/components/ui/Spinner";
import { jobDescriptions, ApiError } from "@/lib/api";
import type { JobDescriptionResponse } from "@/types/api";

interface Props {
  /** ID of the job description to preview. null = modal closed. */
  jdId: number | null;
  onClose: () => void;
}

export function JobDescriptionPreviewModal({ jdId, onClose }: Props) {
  const [jd, setJd] = useState<JobDescriptionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (jdId === null) {
      setJd(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    jobDescriptions
      .get(jdId)
      .then(setJd)
      .catch((e) => setError(e instanceof ApiError ? e.detail : "Failed to load job description."))
      .finally(() => setLoading(false));
  }, [jdId]);

  if (jdId === null) return null;

  const title = jd?.metadata?.job_title ?? jd?.metadata?.company ?? "Job Description";
  const isPartial = jd?.parse_status === "partial";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 shrink-0">
          <h2 className="text-base font-semibold text-gray-900 truncate pr-4">
            {loading ? "Loading…" : title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors shrink-0"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto px-6 py-4 flex-1 space-y-4">
          {loading && (
            <div className="flex justify-center py-8">
              <Spinner className="h-6 w-6 text-indigo-500" />
            </div>
          )}

          {error && (
            <p className="text-sm text-red-600">{error}</p>
          )}

          {!loading && !error && jd && (
            <>
              {/* Parse failure notice */}
              {isPartial && (
                <div className="flex items-start gap-2 rounded-lg bg-amber-50 border border-amber-200 px-4 py-3">
                  <AlertTriangle className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
                  <p className="text-sm text-amber-700">
                    Job details could not be parsed from the scraped content. Please copy and paste
                    the job description text instead.
                  </p>
                </div>
              )}

              {/* JD content */}
              {jd.raw_text ? (
                <pre className="text-sm text-gray-700 whitespace-pre-wrap font-sans leading-relaxed">
                  {jd.raw_text}
                </pre>
              ) : (
                <div className="py-6 text-center text-sm text-gray-500">
                  <p className="font-medium text-gray-700 mb-1">Job description content is unavailable.</p>
                  <p>The job description may not have been parsed successfully.</p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
