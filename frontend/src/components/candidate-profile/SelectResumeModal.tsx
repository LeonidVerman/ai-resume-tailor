// frontend/src/components/candidate-profile/SelectResumeModal.tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { X, FileText, RefreshCw, CheckCircle, AlertCircle, Upload } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { candidateProfile, resumes as resumesApi, ApiError } from "@/lib/api";
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

const ACCEPTED = ".docx,.pdf,.txt";
const MAX_SIZE_MB = 10;

export function SelectResumeModal({ open, onClose, onApply }: Props) {
  const [resumeList, setResumeList] = useState<StructuredResumeSummary[]>([]);
  const [loadingResumes, setLoadingResumes] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draftState, setDraftState] = useState<DraftState>({ kind: "idle" });
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load resume list when modal opens
  useEffect(() => {
    if (!open) return;
    setSelectedId(null);
    setDraftState({ kind: "idle" });
    setUploadError(null);
    loadResumes();
  }, [open]);

  function loadResumes() {
    setLoadingResumes(true);
    candidateProfile.autofill
      .listResumes()
      .then(setResumeList)
      .catch(() => setResumeList([]))
      .finally(() => setLoadingResumes(false));
  }

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

  async function handleUploadFile(file: File) {
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      setUploadError(`File must be under ${MAX_SIZE_MB} MB.`);
      return;
    }
    setUploadError(null);
    setUploading(true);
    try {
      const result = await resumesApi.upload(file);
      // Refresh the list then auto-select the new resume if it's the only one
      const updated = await candidateProfile.autofill.listResumes();
      setResumeList(updated);
      if (updated.length === 1) {
        setSelectedId(updated[0].id);
      } else {
        // Select the newly uploaded resume
        setSelectedId(result.id);
      }
    } catch (e) {
      setUploadError(e instanceof ApiError ? e.detail : "Upload failed. Please try again.");
    } finally {
      setUploading(false);
      // Reset file input so the same file can be re-selected if needed
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function handleFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleUploadFile(file);
  }

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

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPTED}
          className="hidden"
          onChange={handleFileInputChange}
        />

        {/* Body */}
        <div className="px-6 py-4 space-y-4">
          {loadingResumes ? (
            <div className="flex justify-center py-8">
              <Spinner className="h-6 w-6 text-indigo-500" />
            </div>
          ) : resumeList.length === 0 ? (
            /* ── Empty state ── */
            <div className="text-center py-6 space-y-4">
              <div className="flex justify-center">
                <div className="h-12 w-12 rounded-full bg-gray-100 flex items-center justify-center">
                  <FileText className="h-6 w-6 text-gray-400" />
                </div>
              </div>
              <div className="space-y-1">
                <p className="text-sm font-medium text-gray-800">No resumes available</p>
                <p className="text-sm text-gray-500">
                  Upload a resume to generate candidate profile suggestions.
                </p>
              </div>
              <Button
                variant="primary"
                size="sm"
                loading={uploading}
                onClick={() => fileInputRef.current?.click()}
                className="mx-auto"
              >
                <Upload className="h-4 w-4" />
                Upload resume
              </Button>
              <p className="text-xs text-gray-400">
                DOCX, PDF, or TXT — max {MAX_SIZE_MB} MB
              </p>
              {uploadError && (
                <p className="text-sm text-red-600">{uploadError}</p>
              )}
            </div>
          ) : (
            /* ── Resume list ── */
            <>
              <p className="text-sm text-gray-500">
                Select a resume to generate a profile draft. The draft will pre-fill all sections —
                you can review and edit before saving.
              </p>

              <ul className="space-y-2">
                {resumeList.map(r => (
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

              {/* Upload another */}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={uploading}
                  onClick={() => fileInputRef.current?.click()}
                  className="text-xs text-indigo-600 hover:text-indigo-800 disabled:opacity-50 flex items-center gap-1 transition-colors"
                >
                  {uploading ? (
                    <><Spinner className="h-3 w-3" /> Uploading…</>
                  ) : (
                    <><Upload className="h-3 w-3" /> Upload another resume</>
                  )}
                </button>
              </div>
              {uploadError && (
                <p className="text-sm text-red-600">{uploadError}</p>
              )}

              {/* Draft status for selected resume */}
              {selectedId !== null && (
                <div className="pt-1">
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
            </>
          )}
        </div>
      </div>
    </div>
  );
}
