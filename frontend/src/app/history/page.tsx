// frontend/src/app/history/page.tsx
"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Clock, CheckCircle, XCircle, Loader2, Download, Trash2 } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { generations, documents } from "@/lib/api";
import type { GenerationRunSummary, TailoredDocumentDetail } from "@/types/api";
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

// Artifact descriptors for the 4-link download panel.
const ARTIFACTS: Array<{
  part: "resume" | "cover_letter";
  format: "docx" | "pdf";
  label: string;
}> = [
  { part: "resume",       format: "docx", label: "Resume DOCX" },
  { part: "resume",       format: "pdf",  label: "Resume PDF"  },
  { part: "cover_letter", format: "docx", label: "Cover Letter DOCX" },
  { part: "cover_letter", format: "pdf",  label: "Cover Letter PDF"  },
];

/** Displays the run ID; click copies it. */
function RunIdBadge({ id }: { id: number }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(String(id)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      onClick={copy}
      title={`Run ID: ${id} — click to copy`}
      className="font-mono text-gray-400 hover:text-gray-700 transition-colors"
    >
      {copied ? "✓ copied" : `#${id}`}
    </button>
  );
}

// Cache of fetched TailoredDocumentDetail keyed by document id.
type DocCache = Record<number, TailoredDocumentDetail | null>;

export default function HistoryPage() {
  const [runs, setRuns] = useState<GenerationRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  // Expanded row id (show download panel) — null = none expanded.
  const [expanded, setExpanded] = useState<number | null>(null);
  const [docCache, setDocCache] = useState<DocCache>({});
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const PAGE_SIZE = 20;

  const handleDownload = async (
    docId: number,
    part: "resume" | "cover_letter",
    format: "docx" | "pdf",
  ) => {
    try {
      const { blob, filename } = await documents.downloadFormatted(docId, part, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Download failed:", err);
    }
  };

  const handleDelete = async (runId: number) => {
    setDeletingId(runId);
    try {
      await generations.delete(runId);
      setRuns((prev) => prev.filter((r) => r.id !== runId));
      if (expanded === runId) setExpanded(null);
    } catch {
      // ignore
    } finally {
      setDeletingId(null);
    }
  };

  // When a row is expanded, fetch the TailoredDocumentDetail once (cached).
  const handleExpand = async (run: GenerationRunSummary) => {
    const docId = run.tailored_document_id;
    if (!docId) return;
    const next = expanded === run.id ? null : run.id;
    setExpanded(next);
    if (next && !(docId in docCache)) {
      try {
        const doc = await documents.get(docId);
        setDocCache((prev) => ({ ...prev, [docId]: doc }));
      } catch {
        setDocCache((prev) => ({ ...prev, [docId]: null }));
      }
    }
  };

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
          {runs.map((run) => {
            const docId = run.tailored_document_id;
            const isExpanded = expanded === run.id;
            const doc = docId ? docCache[docId] : undefined;

            // Determine which parts have content (for disabling unavailable links).
            // If doc is not yet fetched, assume both parts available for new records.
            const hasResume = doc ? Boolean(doc.resume_json) : true;
            const hasCoverLetter = doc ? Boolean(doc.cover_letter_json) : true;

            const partAvailable = (part: "resume" | "cover_letter") =>
              part === "resume" ? hasResume : hasCoverLetter;

            return (
              <Card key={run.id}>
                <CardBody className="py-4 space-y-3">
                  {/* Row summary */}
                  <div className="flex items-center gap-4">
                    {/* Status icon */}
                    <div className="shrink-0">
                      {STATUS_ICON[run.status] ?? STATUS_ICON.pending}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-gray-900 truncate">
                          {run.company_name && run.role_title
                            ? `${run.company_name} / ${run.role_title}`
                            : run.company_name ?? run.role_title ?? (
                                <span className="font-mono">#{run.id}</span>
                              )}
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
                        <RunIdBadge id={run.id} />
                        <span>{run.model_name}</span>
                        {run.cost_estimate != null && (
                          <span>${run.cost_estimate.toFixed(4)}</span>
                        )}
                      </div>
                    </div>

                    {/* Expand/collapse downloads */}
                    {run.status === "succeeded" && docId && (
                      <div className="shrink-0">
                        <button
                          onClick={() => handleExpand(run)}
                          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 transition-colors"
                        >
                          <Download className="h-3.5 w-3.5" />
                          {isExpanded ? "Hide" : "Downloads"}
                        </button>
                      </div>
                    )}

                    {/* Delete */}
                    <div className="shrink-0">
                      <button
                        onClick={() => handleDelete(run.id)}
                        disabled={deletingId === run.id}
                        className="p-1.5 text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </div>

                  {/* Expanded download panel — 4 artifact links */}
                  {isExpanded && docId && (
                    <div className="pt-1 border-t border-gray-100">
                      {doc === null ? (
                        // Document fetch failed — graceful degradation
                        <p className="text-xs text-gray-400">Artifact details unavailable.</p>
                      ) : (
                        <div className="grid grid-cols-2 gap-2">
                          {ARTIFACTS.map(({ part, format, label }) => {
                            const available = partAvailable(part);
                            return (
                              <button
                                key={`${part}-${format}`}
                                onClick={() => available && handleDownload(docId, part, format)}
                                disabled={!available}
                                className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg transition-colors ${
                                  available
                                    ? "bg-indigo-50 text-indigo-700 hover:bg-indigo-100 cursor-pointer"
                                    : "bg-gray-50 text-gray-300 cursor-not-allowed"
                                }`}
                              >
                                <Download className="h-3.5 w-3.5 shrink-0" />
                                {label}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </CardBody>
              </Card>
            );
          })}

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
