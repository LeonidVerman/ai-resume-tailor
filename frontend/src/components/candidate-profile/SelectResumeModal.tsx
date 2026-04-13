// frontend/src/components/candidate-profile/SelectResumeModal.tsx
"use client";
import { useEffect, useState } from "react";
import { X, FileText, RefreshCw, CheckCircle, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { candidateProfile } from "@/lib/api";
import type { AutofillDraftResponse, CandidateProfileDocument, StructuredResumeSummary } from "@/types/api";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called with the generated/cached draft when the user confirms fill. */
  onApply: (draft: CandidateProfileDocument, resumeId: number) => void;
}

type DraftState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: AutofillDraftResponse }
  | { kind: "generating" }
  | { kind: "error"; message: string };

export function SelectResumeModal({ open, onClose, onApply }: Props) {
  const [resumes, setResumes] = useState<StructuredResumeSummary[]>([]);
  const [loadingResumes, setLoadingResumes] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draftState, setDraftState] = useState<DraftState>({ kind: "idle" });

  // Load resume list when modal opens
  useEffect(() => {
    if (!open) return;
    setSelectedId(null);
    setDraftState({ kind: "idle" });
    setLoadingResumes(true);
    candidateProfile.autofill
      .listResumes()
      .then(setResumes)
      .catch(() => setResumes([]))
      .finally(() => setLoadingResumes(false));
  }, [open]);

  // Load draft info when a resume is selected
  useEffect(() => {
    if (selectedId === null) return;
    setDraftState({ kind: "loading" });
    candidateProfile.autofill
      .getDraft(selectedId)
      .then(data => setDraftState({ kind: "ready", data }))
      .catch(err => {
        // 404 = no draft yet — that's fine, show generate button
        if (err?.status === 404 || err?.response?.status === 404) {
          setDraftState({ kind: "idle" });
        } else {
          setDraftState({ kind: "error", message: "Failed to load draft info." });
        }
      });
  }, [selectedId]);

  function handleGenerate() {
    if (selectedId === null) return;
    setDraftState({ kind: "generating" });
    candidateProfile.autofill
      .generate({ resume_id: selectedId })
      .then(data => setDraftState({ kind: "ready", data }))
      .catch(() =>
        setDraftState({ kind: "error", message: "Generation failed. Please try again." })
      );
  }

  function handleApply(draft: CandidateProfileDocument) {
    if (selectedId === null) return;
    candidateProfile.autofill.selectSource(selectedId).catch(() => {
      // Non-fatal: remember source resume is a best-effort UI hint
    });
    onApply(draft, selectedId);
    onClose();
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-base font-semibold text-gray-900">Fill from resume</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-4">
          <p className="text-sm text-gray-500">
            Select a resume to generate a profile draft. The draft will pre-fill all sections —
            you can review and edit before saving.
          </p>

          {loadingResumes ? (
            <div className="flex justify-center py-6">
              <Spinner className="h-6 w-6 text-indigo-500" />
            </div>
          ) : resumes.length === 0 ? (
            <div className="text-sm text-gray-500 py-4 text-center">
              No resumes found. Upload a resume first.
            </div>
          ) : (
            <ul className="space-y-2">
              {resumes.map(r => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(r.id)}
                    className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl border text-left transition-colors ${
                      selectedId === r.id
                        ? "border-indigo-500 bg-indigo-50"
                        : "border-gray-200 hover:border-indigo-300 hover:bg-gray-50"
                    }`}
                  >
                    <FileText className="h-4 w-4 text-gray-400 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-gray-800 truncate">{r.name}</p>
                      <p className="text-xs text-gray-400">
                        {new Date(r.created_at).toLocaleDateString()}
                      </p>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}

          {/* Draft status for selected resume */}
          {selectedId !== null && (
            <div className="pt-2">
              {draftState.kind === "loading" && (
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <Spinner className="h-4 w-4" /> Checking for existing draft…
                </div>
              )}

              {draftState.kind === "generating" && (
                <div className="flex items-center gap-2 text-sm text-indigo-600">
                  <Spinner className="h-4 w-4" /> Generating profile from resume…
                </div>
              )}

              {draftState.kind === "error" && (
                <div className="flex items-center gap-2 text-sm text-red-600">
                  <AlertCircle className="h-4 w-4" />
                  {draftState.message}
                </div>
              )}

              {draftState.kind === "idle" && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleGenerate}
                  className="w-full"
                >
                  Generate profile from this resume
                </Button>
              )}

              {draftState.kind === "ready" && (
                <div className="space-y-3">
                  <div className="flex items-start gap-2 text-sm">
                    {draftState.data.is_stale ? (
                      <>
                        <AlertCircle className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
                        <span className="text-amber-700">
                          This draft was generated from an older version of the resume.
                        </span>
                      </>
                    ) : (
                      <>
                        <CheckCircle className="h-4 w-4 text-green-500 mt-0.5 shrink-0" />
                        <span className="text-gray-600">
                          Draft available — generated{" "}
                          {new Date(draftState.data.generated_at).toLocaleDateString()}.
                        </span>
                      </>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => handleApply(draftState.data.draft)}
                      className="flex-1"
                    >
                      Use this draft
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleGenerate}
                      title="Regenerate from current resume"
                    >
                      <RefreshCw className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
