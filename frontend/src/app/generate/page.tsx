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
          Generate tailored application materials
        </h1>
        <p className="text-gray-500 mt-1">
          Select your resume and a job description to generate a targeted software engineering resume and cover letter.
        </p>
      </div>

      {/* Result card */}
      {generationResult && (
        <div className="mb-6">
          <GenerationResult result={generationResult} />
        </div>
      )}

      <div className="grid grid-cols-3 gap-6">
        {/* Main workflow — primary surface, dominant weight */}
        <div className="col-span-2">
          <Card className="shadow-md border-gray-200">
            <CardHeader>
              <h2 className="font-semibold text-gray-900">Build your application package</h2>
              <p className="text-sm text-gray-500 mt-0.5">
                Select your resume and job, choose a mode, then generate.
              </p>
            </CardHeader>
            <CardBody className="py-6">
              <GenerationForm
                onGenerated={handleGenerated}
                key={newJd?.id ?? "default"}
              />
            </CardBody>
          </Card>
        </div>

        {/* Sidebar — secondary surface, supporting role */}
        <div className="space-y-4">
          <Card className="bg-gray-50/80 border-gray-100 shadow-none">
            <CardHeader className="border-gray-100">
              <h2 className="text-sm font-semibold text-gray-700">Add a job description</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Paste or scrape a new job posting.
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

          <div className="rounded-lg bg-amber-50/60 border border-amber-100 p-4 text-sm text-amber-700">
            <p className="font-medium mb-1 text-amber-800">Generation time</p>
            <p className="text-xs leading-relaxed">Typically 15–30 seconds. Keep this tab open.</p>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
