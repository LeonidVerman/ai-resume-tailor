// frontend/src/app/generate/page.tsx
"use client";

import { useState } from "react";
import { Zap } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { JobDescriptionForm } from "@/components/job-description/JobDescriptionForm";
import { GenerationForm } from "@/components/generation/GenerationForm";
import { GenerationResult } from "@/components/generation/GenerationResult";
import type { GenerationResponse, JobDescriptionResponse } from "@/types/api";

export default function GeneratePage() {
  const [generationResult, setGenerationResult] = useState<GenerationResponse | null>(null);
  const [newJd, setNewJd] = useState<JobDescriptionResponse | null>(null);

  function handleGenerated(result: GenerationResponse) {
    setGenerationResult(result);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  return (
    <AppShell>
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900 flex items-center gap-2">
          <Zap className="h-6 w-6 text-indigo-600" />
          Generate tailored documents
        </h1>
        <p className="text-gray-500 mt-1">
          Select your resume and a job description, then generate a tailored resume and cover letter.
        </p>
      </div>

      {/* Result card */}
      {generationResult && (
        <div className="mb-6">
          <GenerationResult result={generationResult} />
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        {/* Main: selection + trigger */}
        <div className="col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <h2 className="font-semibold text-gray-900">Select inputs</h2>
              <p className="text-sm text-gray-500 mt-0.5">
                Choose which resume and job description to use.
              </p>
            </CardHeader>
            <CardBody>
              <GenerationForm
                onGenerated={handleGenerated}
                key={newJd?.id ?? "default"}
              />
            </CardBody>
          </Card>
        </div>

        {/* Sidebar: add JD */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <h2 className="font-semibold text-gray-900 text-sm">Add a job description</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Paste or scrape a new job posting to use in generation.
              </p>
            </CardHeader>
            <CardBody>
              <JobDescriptionForm
                onCreated={(jd) => {
                  setNewJd(jd);
                }}
              />
            </CardBody>
          </Card>

          <div className="rounded-lg bg-amber-50 border border-amber-200 p-4 text-sm text-amber-800">
            <p className="font-medium mb-1">Generation time</p>
            <p>Generation typically takes 15–30 seconds. Please keep this tab open.</p>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
