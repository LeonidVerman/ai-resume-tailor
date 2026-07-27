// frontend/src/components/guest/GuestResult.tsx
//
// Result view for the guest /try flow (issue #155).
// Guests get: a text preview of the tailored resume / cover letter and
// PDF downloads only. DOCX/TXT downloads and "View changes" are shown as
// visibly locked rows — the registration incentive.

"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { CheckCircle, Download, FileText, GitCompare, Lock, UserPlus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { documents, ApiError } from "@/lib/api";
import type { GenerationResponse, TailoredDocumentDetail } from "@/types/api";
import { cn } from "@/lib/utils";

interface GuestResultProps {
  result: GenerationResponse;
}

const LOCKED_ROWS = [
  { icon: FileText, label: "Resume DOCX" },
  { icon: FileText, label: "Cover Letter DOCX" },
  { icon: FileText, label: "Resume TXT" },
  { icon: FileText, label: "Cover Letter TXT" },
  { icon: GitCompare, label: "View changes" },
];

type PreviewPart = "resume" | "cover_letter";

export function GuestResult({ result }: GuestResultProps) {
  const [doc, setDoc] = useState<TailoredDocumentDetail | null>(null);
  const [previewPart, setPreviewPart] = useState<PreviewPart>("resume");
  const [downloading, setDownloading] = useState<PreviewPart | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    if (result.tailored_document_id) {
      documents.get(result.tailored_document_id).then(setDoc).catch(() => null);
    }
  }, [result.tailored_document_id]);

  const previewText =
    (previewPart === "resume"
      ? (doc?.resume_json?.text as string | undefined)
      : (doc?.cover_letter_json?.text as string | undefined)) ?? "";

  async function handleDownload(part: PreviewPart) {
    if (!result.tailored_document_id) return;
    setDownloadError(null);
    setDownloading(part);
    try {
      const { blob, filename } = await documents.downloadFormatted(
        result.tailored_document_id,
        part,
        "pdf"
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 403 || e.status === 429)) {
        setDownloadError(
          "This download is not available on the free trial — create a free account to continue."
        );
      } else {
        setDownloadError(e instanceof ApiError ? e.detail : "Download failed.");
      }
    } finally {
      setDownloading(null);
    }
  }

  return (
    <Card className="shadow-md border-gray-200">
      <CardHeader>
        <div className="flex items-center gap-2">
          <CheckCircle className="h-5 w-5 text-green-500 shrink-0" />
          <h2 className="font-semibold text-gray-900">
            Your tailored resume and cover letter are ready
          </h2>
        </div>
        {doc?.company_name && (
          <p className="text-sm text-gray-500 mt-0.5">
            {doc.role_title} @ {doc.company_name}
          </p>
        )}
      </CardHeader>
      <CardBody className="space-y-5 py-5">
        {/* Preview */}
        {doc && previewText && (
          <div>
            <div className="flex gap-1 p-1 bg-gray-100 rounded-lg w-fit mb-2">
              {(["resume", "cover_letter"] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPreviewPart(p)}
                  className={cn(
                    "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
                    previewPart === p
                      ? "bg-white text-gray-900 shadow-sm"
                      : "text-gray-500 hover:text-gray-700"
                  )}
                >
                  {p === "resume" ? "Resume" : "Cover letter"}
                </button>
              ))}
            </div>
            <pre className="whitespace-pre-wrap font-sans text-sm text-gray-700 bg-gray-50 border border-gray-100 rounded-lg p-4 max-h-96 overflow-y-auto">
              {previewText}
            </pre>
          </div>
        )}

        {/* Downloads — PDF only for guests */}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => handleDownload("resume")}
            loading={downloading === "resume"}
            size="lg"
            className="font-semibold shadow-md"
          >
            <Download className="h-5 w-5" />
            Download resume
          </Button>
          <Button
            variant="secondary"
            onClick={() => handleDownload("cover_letter")}
            loading={downloading === "cover_letter"}
          >
            <Download className="h-4 w-4" />
            Cover letter PDF
          </Button>
        </div>
        {downloadError && (
          <p className="text-sm text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
            {downloadError}{" "}
            <Link href="/register" className="font-medium text-indigo-600 hover:underline">
              Create free account
            </Link>
          </p>
        )}

        {/* Locked artifacts — registration incentive */}
        <div className="rounded-lg border border-gray-100 divide-y divide-gray-100">
          {LOCKED_ROWS.map(({ icon: Icon, label }) => (
            <div
              key={label}
              className="flex items-center gap-3 px-4 py-2.5 text-sm text-gray-400"
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="flex-1">{label}</span>
              <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-400">
                <Lock className="h-3.5 w-3.5" />
                Free account required
              </span>
            </div>
          ))}
        </div>

        {/* Secondary CTA */}
        <div className="pt-4 border-t border-gray-100 flex items-center justify-between gap-4 flex-wrap">
          <p className="text-sm text-gray-600">
            Save this application, unlock DOCX downloads and see exactly what changed.
          </p>
          <Link
            href="/register"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-indigo-200 bg-indigo-50 text-sm font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
          >
            <UserPlus className="h-4 w-4" />
            Create free account and save
          </Link>
        </div>
      </CardBody>
    </Card>
  );
}
