// frontend/src/components/resume/ResumeCard.tsx
import { FileText, Clock, Trash2 } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { formatDate } from "@/lib/utils";
import type { StructuredResumeSummary } from "@/types/api";
import { cn } from "@/lib/utils";

interface ResumeCardProps {
  resume: StructuredResumeSummary;
  selected?: boolean;
  onSelect?: (id: number) => void;
  onDelete?: (id: number) => void;
  deleting?: boolean;
}

export function ResumeCard({ resume, selected, onSelect, onDelete, deleting }: ResumeCardProps) {
  return (
    <Card
      onClick={() => onSelect?.(resume.id)}
      className={cn(
        "transition-all",
        onSelect ? "cursor-pointer" : "",
        selected
          ? "border-indigo-300 bg-indigo-50 shadow-sm"
          : onSelect ? "border-gray-100 hover:border-gray-200 hover:bg-gray-50 hover:shadow-sm" : ""
      )}
    >
      <CardBody className="flex items-center gap-3 py-3.5">
        <div className={`shrink-0 h-9 w-9 rounded-lg flex items-center justify-center transition-colors ${selected ? "bg-indigo-100" : "bg-indigo-50"}`}>
          <FileText className="h-5 w-5 text-indigo-600" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 truncate">{resume.name}</p>
          <p className="text-xs text-gray-400 flex items-center gap-1 mt-0.5">
            <Clock className="h-3 w-3" />
            {formatDate(resume.created_at)}
          </p>
        </div>
        {selected && (
          <div className="shrink-0 h-5 w-5 rounded-full bg-indigo-600 flex items-center justify-center">
            <svg className="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
            </svg>
          </div>
        )}
        {onDelete && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(resume.id); }}
            disabled={deleting}
            className="shrink-0 p-1 text-gray-400 hover:text-red-600 disabled:opacity-40 transition-colors"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        )}
      </CardBody>
    </Card>
  );
}
