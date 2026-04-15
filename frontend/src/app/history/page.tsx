// frontend/src/app/history/page.tsx
"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Clock, CheckCircle, XCircle, Loader2, Download, Trash2, GitCompare } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { ResumeDiffModal } from "@/components/generation/ResumeDiffModal";
import { generations, documents } from "@/lib/api";
import type { GenerationRunSummary, GenerationMode, TailoredDocumentDetail } from "@/types/api";
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

// Artifact descriptors for the download panel.
const ARTIFACTS: Array<{
  part: "resume" | "cover_letter";
  format: "docx" | "pdf" | "txt";
  label: string;
}> = [
  { part: "resume",       format: "docx", label: "Resume DOCX" },
  { part: "resume",       format: "pdf",  label: "Resume PDF"  },
  { part: "resume",       format: "txt",  label: "Resume TXT"  },
  { part: "cover_letter", format: "docx", label: "Cover Letter DOCX" },
  { part: "cover_letter", format: "pdf",  label: "Cover Letter PDF"  },
  { part: "cover_letter", format: "txt",  label: "Cover Letter TXT"  },
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

function ModeBadge({ mode }: { mode: GenerationMode }) {
  if (mode === "normal") {
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">Normal</span>;
  }
  if (mode === "aggressive") {
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-orange-100 text-orange-700">Aggressive</span>;
  }
  return null; // conservative is the default — no badge
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
  // Diff modal: docId of the currently open diff (null = closed).
  const [diffDocId, setDiffDocId] = useState<number | null>(null);
  // docId currently being fetched for the diff (shows spinner on button).
  const [diffFetchingId, setDiffFetchingId] = useState<number | null>(null);
  const PAGE_SIZE = 20;

  const handleDownload = async (
    docId: number,
    part: "resume" | "cover_letter",
    format: "docx" | "pdf" | "txt",
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

  const handleViewDiff = async (run: GenerationRunSummary) => {
    const docId = run.tailored_document_id;
    if (!docId) return;
    if (!(docId in docCache)) {
      setDiffFetchingId(docId);
      try {
        const doc = await documents.get(docId);
        setDocCache((prev) => ({ ...prev, [docId]: doc }));
      } catch {
        setDocCache((prev) => ({ ...prev, [docId]: null }));
      } finally {
        setDiffFetchingId(null);
      }
    }
    setDiffDocId(docId);
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
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Generation history</h1>
        <p className="text-gray-500 mt-1">All your tailored resumes and cover letters for software engineering roles.</p>
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
        <div className="space-y-4">
        <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden">
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
              <div key={run.id} className="border-t border-gray-100 first:border-t-0 hover:bg-gray-50/50 transition-colors">
                <div className="px-5 py-5 space-y-3">
                  {/* Row summary */}
                  <div className="flex items-center gap-4">
                    {/* Status icon */}
                    <div className="shrink-0">
                      {STATUS_ICON[run.status] ?? STATUS_ICON.pending}
                    </div>

                    {/* Info — title is primary, metadata is secondary */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold text-gray-900 truncate">
                          {run.company_name && run.role_title
                            ? `${run.company_name} — ${run.role_title}`
                            : run.company_name ?? run.role_title ?? (
                                <span className="font-mono text-gray-500">#{run.id}</span>
                              )}
                        </span>
                        <Badge variant={STATUS_VARIANT[run.status] ?? "default"}>
                          {run.status}
                        </Badge>
                        <ModeBadge mode={run.generation_mode} />
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-sm text-gray-500">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3.5 w-3.5" />
                          {formatDateTime(run.started_at)}
                        </span>
                        <RunIdBadge id={run.id} />
                        <span className="text-gray-400">{run.model_name}</span>
                        {run.cost_estimate != null && (
                          <span className="text-gray-400">${run.cost_estimate.toFixed(4)}</span>
                        )}
                      </div>
                    </div>

                    {/* Actions — grouped on the right */}
                    {run.status === "succeeded" && docId && (
                      <div className="shrink-0 flex items-center gap-1.5">
                        <button
                          onClick={() => handleViewDiff(run)}
                          disabled={diffFetchingId === docId}
                          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 text-gray-500 rounded-lg hover:bg-gray-100 hover:text-gray-700 disabled:opacity-50 transition-colors"
                        >
                          {diffFetchingId === docId
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            : <GitCompare className="h-3.5 w-3.5" />}
                          View changes
                        </button>
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
                        className="p-1.5 text-gray-300 hover:text-red-500 disabled:opacity-40 transition-colors"
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
                </div>
              </div>
            );
          })}

        </div>
        {/* Pagination */}
        <div className="flex justify-between items-center pt-4">
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
      {/* Diff modal */}
      {diffDocId !== null && (() => {
        const diffDoc = docCache[diffDocId];
        // Fetch failed or no diff was stored (run predates the diff feature)
        if (diffDoc === null || (diffDoc && diffDoc.resume_diff == null)) {
          return (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
              onClick={() => setDiffDocId(null)}
            >
              <div className="bg-white rounded-xl shadow-xl w-full max-w-sm mx-4 p-6 text-center space-y-4">
                <GitCompare className="h-8 w-8 text-gray-300 mx-auto" />
                <p className="text-sm text-gray-600">No diff available for this run.</p>
                <button
                  onClick={() => setDiffDocId(null)}
                  className="px-4 py-1.5 text-sm text-gray-600 hover:text-gray-900 font-medium transition-colors"
                >
                  Close
                </button>
              </div>
            </div>
          );
        }
        if (diffDoc && diffDoc.resume_diff != null) {
          return (
            <ResumeDiffModal
              diff={diffDoc.resume_diff}
              onClose={() => setDiffDocId(null)}
            />
          );
        }
        return null;
      })()}
    </AppShell>
  );
}
