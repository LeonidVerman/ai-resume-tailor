// frontend/src/components/generation/GenerationResult.tsx
"use client";

import { useState, useEffect } from "react";
import { CheckCircle, XCircle, Clock, Download, GitCompare } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card, CardBody } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { ResumeDiffModal } from "./ResumeDiffModal";
import { generations, documents } from "@/lib/api";
import type {
  GenerationResponse,
  GenerationRunDetail,
  TailoredDocumentDetail,
} from "@/types/api";
import { formatDateTime } from "@/lib/utils";

interface GenerationResultProps {
  result: GenerationResponse;
}

type Phase = "pending" | "polling" | "done" | "failed";

const ARTIFACTS: Array<{
  part: "resume" | "cover_letter";
  format: "docx" | "pdf" | "txt";
  label: string;
}> = [
  { part: "resume",       format: "pdf",  label: "Resume PDF"         },
  { part: "cover_letter", format: "pdf",  label: "Cover Letter PDF"   },
  { part: "resume",       format: "docx", label: "Resume DOCX"        },
  { part: "cover_letter", format: "docx", label: "Cover Letter DOCX"  },
  { part: "resume",       format: "txt",  label: "Resume TXT"         },
  { part: "cover_letter", format: "txt",  label: "Cover Letter TXT"   },
];

export function GenerationResult({ result }: GenerationResultProps) {
  const [phase, setPhase] = useState<Phase>(
    result.status === "succeeded" ? "done" : result.status === "failed" ? "failed" : "polling"
  );
  const [runDetail, setRunDetail] = useState<GenerationRunDetail | null>(null);
  const [doc, setDoc] = useState<TailoredDocumentDetail | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  useEffect(() => {
    if (result.status === "succeeded" && result.tailored_document_id) {
      documents.get(result.tailored_document_id).then(setDoc).catch(() => null);
      generations.get(result.run_id).then(setRunDetail).catch(() => null);
      return;
    }

    if (phase !== "polling") return;

    // The backend is synchronous — the generation response already has the
    // final status. Poll just in case future phases make it async.
    const poll = async () => {
      try {
        const run = await generations.get(result.run_id);
        setRunDetail(run);
        if (run.status === "succeeded") {
          setPhase("done");
          if (result.tailored_document_id) {
            documents.get(result.tailored_document_id).then(setDoc).catch(() => null);
          }
        } else if (run.status === "failed") {
          setPhase("failed");
        }
        return run.status;
      } catch {
        return "error";
      }
    };

    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;
      const status = await poll();
      if (status === "succeeded" || status === "failed" || attempts > 60) {
        clearInterval(interval);
      }
    }, 3000);

    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result.run_id, result.tailored_document_id]);

  const statusBadge = {
    pending: <Badge variant="default">Pending</Badge>,
    polling: <Badge variant="info">Running</Badge>,
    done: <Badge variant="success">Succeeded</Badge>,
    failed: <Badge variant="danger">Failed</Badge>,
  }[phase];

  const handleDownload = async (
    part: "resume" | "cover_letter",
    format: "docx" | "pdf" | "txt",
    label: string,
  ) => {
    if (!result.tailored_document_id) return;
    try {
      const { blob, filename } = await documents.downloadFormatted(result.tailored_document_id, part, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error(`Download failed (${label}):`, err);
    }
  };

  return (
    <Card>
      <CardBody className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {phase === "polling" && <Spinner size="sm" />}
            {phase === "done" && <CheckCircle className="h-5 w-5 text-green-500" />}
            {phase === "failed" && <XCircle className="h-5 w-5 text-red-500" />}
            {phase === "pending" && <Clock className="h-5 w-5 text-gray-400" />}
            <span className="font-medium text-gray-900">Generation run</span>
          </div>
          {statusBadge}
        </div>

        <div className="text-xs text-gray-500 space-y-0.5">
          <p>Run ID: <span className="font-mono">{result.run_id}</span></p>
          {runDetail?.completed_at && (
            <p>Completed: {formatDateTime(runDetail.completed_at)}</p>
          )}
          {runDetail?.cost_estimate != null && (
            <p>Estimated cost: ${runDetail.cost_estimate.toFixed(4)}</p>
          )}
        </div>

        {phase === "polling" && (
          <p className="text-sm text-gray-500">
            Generating your tailored resume and cover letter... This typically
            takes 30–90 seconds.
          </p>
        )}

        {phase === "failed" && (
          <p className="text-sm text-red-600">
            {runDetail?.error_message ?? "Generation failed. Please try again."}
          </p>
        )}

        {phase === "done" && doc && (
          <div className="space-y-3">
            {doc.company_name && (
              <p className="text-sm text-gray-700 font-medium">
                {doc.role_title} @ {doc.company_name}
              </p>
            )}
            <div className="grid grid-cols-2 gap-2">
              {ARTIFACTS.map(({ part, format, label }) => (
                <button
                  key={`${part}-${format}`}
                  onClick={() => handleDownload(part, format, label)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg text-sm font-medium hover:bg-indigo-100 transition-colors"
                >
                  <Download className="h-4 w-4 shrink-0" />
                  {label}
                </button>
              ))}
            </div>
            {doc.resume_diff && doc.resume_diff.length > 0 && (
              <button
                onClick={() => setShowDiff(true)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-50 text-gray-600 rounded-lg text-sm font-medium hover:bg-gray-100 transition-colors border border-gray-200"
              >
                <GitCompare className="h-4 w-4 shrink-0" />
                View changes
              </button>
            )}
          </div>
        )}

        {showDiff && doc?.resume_diff && (
          <ResumeDiffModal diff={doc.resume_diff} onClose={() => setShowDiff(false)} />
        )}
      </CardBody>
    </Card>
  );
}
