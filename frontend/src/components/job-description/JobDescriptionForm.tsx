// frontend/src/components/job-description/JobDescriptionForm.tsx
"use client";

import { useState } from "react";
import { Link2, AlignLeft } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { jobDescriptions, ApiError } from "@/lib/api";
import type { JobDescriptionResponse } from "@/types/api";
import { cn } from "@/lib/utils";

type Mode = "url" | "manual";

interface JobDescriptionFormProps {
  onCreated?: (jd: JobDescriptionResponse) => void;
}

export function JobDescriptionForm({ onCreated }: JobDescriptionFormProps) {
  const [mode, setMode] = useState<Mode>("url");
  const [url, setUrl] = useState("");
  const [rawText, setRawText] = useState("");
  const [company, setCompany] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      let result: JobDescriptionResponse;
      if (mode === "url") {
        if (!url.trim()) { setError("URL is required."); setLoading(false); return; }
        result = await jobDescriptions.scrape({ url: url.trim() });
      } else {
        if (!rawText.trim()) { setError("Job description text is required."); setLoading(false); return; }
        result = await jobDescriptions.manual({
          raw_text: rawText.trim(),
          company: company.trim() || undefined,
          job_title: jobTitle.trim() || undefined,
        });
      }
      const label = result.metadata?.job_title ?? result.metadata?.company ?? "job description";
      setSuccess(`Saved: ${label}`);
      onCreated?.(result);
      // Reset
      setUrl(""); setRawText(""); setCompany(""); setJobTitle("");
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to save job description.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-gray-100 rounded-lg w-fit">
        {(["url", "manual"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => { setMode(m); setError(null); setSuccess(null); }}
            className={cn(
              "flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === m
                ? "bg-white text-gray-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            )}
          >
            {m === "url" ? <Link2 className="h-4 w-4" /> : <AlignLeft className="h-4 w-4" />}
            {m === "url" ? "From URL" : "Paste text"}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {mode === "url" ? (
          <Input
            label="Job posting URL"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://linkedin.com/jobs/view/..."
            hint="Supports LinkedIn, Wellfound, and most job boards"
          />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Input
                label="Company (optional)"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="Acme Corp"
              />
              <Input
                label="Job title (optional)"
                value={jobTitle}
                onChange={(e) => setJobTitle(e.target.value)}
                placeholder="Senior Engineer"
              />
            </div>
            <Textarea
              label="Job description"
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="Paste the full job description here..."
              className="min-h-[200px]"
            />
          </>
        )}

        {error && <p className="text-sm text-red-600">✗ {error}</p>}
        {success && <p className="text-sm text-green-600">✓ {success}</p>}

        <Button type="submit" loading={loading}>
          {mode === "url" ? "Fetch job description" : "Save job description"}
        </Button>
      </form>
    </div>
  );
}
