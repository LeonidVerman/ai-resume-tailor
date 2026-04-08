// frontend/src/app/jobs/page.tsx
"use client";

import { useState, useEffect } from "react";
import { Briefcase, Trash2 } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { JobDescriptionForm } from "@/components/job-description/JobDescriptionForm";
import { jobDescriptions } from "@/lib/api";
import type { JobDescriptionSummary, JobDescriptionResponse } from "@/types/api";
import { formatDate } from "@/lib/utils";

export default function JobsPage() {
  const [list, setList] = useState<JobDescriptionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  useEffect(() => {
    jobDescriptions
      .list()
      .then(setList)
      .catch(() => [])
      .finally(() => setLoading(false));
  }, []);

  function onCreated(jd: JobDescriptionResponse) {
    setList((prev) => [
      {
        id: jd.id,
        company: jd.metadata?.company,
        job_title: jd.metadata?.job_title,
        source_url: jd.source_url,
        created_at: jd.created_at,
      },
      ...prev,
    ]);
  }

  async function handleDelete(id: number) {
    setDeletingId(id);
    try {
      await jobDescriptions.delete(id);
      setList((prev) => prev.filter((j) => j.id !== id));
    } catch {
      // ignore
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Job Descriptions</h1>
        <p className="text-gray-500 mt-1">Save and manage job postings for generation.</p>
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* List */}
        <div className="col-span-2 space-y-4">
          <h2 className="text-base font-semibold text-gray-700">Saved job descriptions</h2>
          {loading ? (
            <Spinner label="Loading..." />
          ) : list.length === 0 ? (
            <Card>
              <CardBody className="py-10 text-center">
                <Briefcase className="h-8 w-8 text-gray-300 mx-auto mb-3" />
                <p className="text-gray-500">No job descriptions yet.</p>
              </CardBody>
            </Card>
          ) : (
            <div className="space-y-2">
              {list.map((jd) => (
                <Card key={jd.id}>
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
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={deletingId === jd.id}
                      onClick={() => handleDelete(jd.id)}
                      className="text-gray-400 hover:text-red-600"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </CardBody>
                </Card>
              ))}
            </div>
          )}
        </div>

        {/* Add form */}
        <div>
          <Card>
            <CardHeader>
              <h2 className="font-semibold text-gray-900 text-sm">Add job description</h2>
            </CardHeader>
            <CardBody>
              <JobDescriptionForm onCreated={onCreated} />
            </CardBody>
          </Card>
        </div>
      </div>
    </AppShell>
  );
}
