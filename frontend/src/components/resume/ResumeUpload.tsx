// frontend/src/components/resume/ResumeUpload.tsx
"use client";

import { useState, useRef, DragEvent } from "react";
import { Upload, FileText } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { resumes } from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { StructuredResumeResponse } from "@/types/api";
import { cn } from "@/lib/utils";

interface ResumeUploadProps {
  onUpload?: (resume: StructuredResumeResponse) => void;
}

const MAX_SIZE_MB = 10;
const ACCEPTED = [".docx", ".pdf", ".txt"];

export function ResumeUpload({ onUpload }: ResumeUploadProps) {
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File) {
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      setError(`File must be under ${MAX_SIZE_MB} MB.`);
      return;
    }
    setError(null);
    setSuccess(null);
    setLoading(true);
    try {
      const result = await resumes.upload(file);
      setSuccess(`Uploaded: ${result.resume.name}`);
      onUpload?.(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Upload failed.");
    } finally {
      setLoading(false);
    }
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }

  function onInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={cn(
          "border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors",
          dragging
            ? "border-indigo-400 bg-indigo-50"
            : "border-gray-300 hover:border-indigo-400 hover:bg-gray-50"
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED.join(",")}
          className="hidden"
          onChange={onInputChange}
        />
        <Upload className="h-8 w-8 mx-auto mb-3 text-gray-400" />
        <p className="text-sm font-medium text-gray-700">
          Drag & drop your resume here
        </p>
        <p className="text-xs text-gray-500 mt-1">
          DOCX, PDF, or TXT — max {MAX_SIZE_MB} MB
        </p>
        <Button
          variant="secondary"
          size="sm"
          className="mt-4"
          loading={loading}
          onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}
        >
          <FileText className="h-4 w-4" />
          Choose file
        </Button>
      </div>

      {error && (
        <p className="text-sm text-red-600 flex items-center gap-1">
          <span>✗</span> {error}
        </p>
      )}
      {success && (
        <p className="text-sm text-green-600 flex items-center gap-1">
          <span>✓</span> {success}
        </p>
      )}
    </div>
  );
}
