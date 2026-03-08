// frontend/src/app/history/page.tsx
"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Clock, CheckCircle, XCircle, Loader2, Download } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { generations } from "@/lib/api";
import type { GenerationRunSummary } from "@/types/api";
import { formatDateTime } from "@/lib/utils";

const STATUS_ICON = {
  succeeded: <CheckCircle className="h-4 w-4 text-green-500" />,
  failed: <XCircle className="h-4 w-4 text-red-500" />,
  running: <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />,
  pending: <Clock className="h-4 w-4 text-gray-400" />,
};

const STATUS_VARIANT: Record<string, "success" | "danger" | "info" | "default"> = {
  succeeded: "success",
  failed: "danger",
  running: "info",
  pending: "default",
};

export default function HistoryPage() {
  const [runs, setRuns] = useState<GenerationRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 20;

  useEffect(() => {
    setLoading(true);
    generations
      .list(PAGE_SIZE, page * PAGE_SIZE)
      .then(setRuns)
      .catch(() => [])
      .finally(() => setLoading(false));
  }, [page]);

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Generation history</h1>
        <p className="text-gray-500 mt-1">All your previous generation runs.</p>
      </div>

      {loading ? (
        <div className="py-12 flex justify-center">
          <Spinner size="lg" label="Loading history..." />
        </div>
      ) : runs.length === 0 ? (
        <Card>
          <CardBody className="py-12 text-center">
            <Clock className="h-8 w-8 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500">No generation runs yet.</p>
            <Link href="/generate" className="mt-4 inline-block">
              <Button variant="primary" size="sm">Generate now</Button>
            </Link>
          </CardBody>
        </Card>
      ) : (
        <div className="space-y-3">
          {runs.map((run) => (
            <Card key={run.id}>
              <CardBody className="flex items-center gap-4 py-4">
                {/* Status icon */}
                <div className="shrink-0">
                  {STATUS_ICON[run.status] ?? STATUS_ICON.pending}
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 font-mono truncate">
                      {run.id.slice(0, 8)}…
                    </span>
                    <Badge variant={STATUS_VARIANT[run.status] ?? "default"}>
                      {run.status}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3 mt-0.5 text-xs text-gray-500">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {formatDateTime(run.started_at)}
                    </span>
                    <span>{run.model_name}</span>
                    {run.cost_estimate != null && (
                      <span>${run.cost_estimate.toFixed(4)}</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                {run.status === "succeeded" && (
                  <div className="shrink-0 flex items-center gap-2">
                    <a
                      href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1"}/documents/${run.id}/download?part=resume`}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 transition-colors"
                    >
                      <Download className="h-3.5 w-3.5" />
                      Resume
                    </a>
                    <a
                      href={`${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1"}/documents/${run.id}/download?part=cover_letter`}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 transition-colors"
                    >
                      <Download className="h-3.5 w-3.5" />
                      Cover letter
                    </a>
                  </div>
                )}
              </CardBody>
            </Card>
          ))}

          {/* Pagination */}
          <div className="flex justify-between items-center pt-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              ← Previous
            </Button>
            <span className="text-sm text-gray-500">Page {page + 1}</span>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPage((p) => p + 1)}
              disabled={runs.length < PAGE_SIZE}
            >
              Next →
            </Button>
          </div>
        </div>
      )}
    </AppShell>
  );
}
