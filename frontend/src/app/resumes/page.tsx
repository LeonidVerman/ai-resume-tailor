// frontend/src/app/resumes/page.tsx
"use client";

import { useState, useEffect } from "react";
import { FileText } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { ResumeUpload } from "@/components/resume/ResumeUpload";
import { ResumeCard } from "@/components/resume/ResumeCard";
import { resumes } from "@/lib/api";
import type { StructuredResumeSummary, StructuredResumeResponse } from "@/types/api";

export default function ResumesPage() {
  const [list, setList] = useState<StructuredResumeSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    resumes
      .list()
      .then(setList)
      .catch(() => [])
      .finally(() => setLoading(false));
  }, []);

  function onUploaded(r: StructuredResumeResponse) {
    setList((prev) => [
      { id: r.id, name: r.resume.name, created_at: r.created_at },
      ...prev,
    ]);
  }

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Resumes</h1>
        <p className="text-gray-500 mt-1">Upload and manage your master resumes.</p>
      </div>

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2 space-y-4">
          <h2 className="text-base font-semibold text-gray-700">Uploaded resumes</h2>
          {loading ? (
            <Spinner label="Loading..." />
          ) : list.length === 0 ? (
            <Card>
              <CardBody className="py-10 text-center">
                <FileText className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-gray-500">No resumes yet. Upload your first one.</p>
              </CardBody>
            </Card>
          ) : (
            <div className="space-y-2">
              {list.map((r) => (
                <ResumeCard key={r.id} resume={r} />
              ))}
            </div>
          )}
        </div>

        <div>
          <Card>
            <CardHeader>
              <h2 className="font-semibold text-gray-900 text-sm">Upload new resume</h2>
            </CardHeader>
            <CardBody>
              <ResumeUpload onUpload={onUploaded} />
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
