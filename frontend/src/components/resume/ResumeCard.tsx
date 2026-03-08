// frontend/src/components/resume/ResumeCard.tsx
import { FileText, Clock } from "lucide-react";
import { Card, CardBody } from "@/components/ui/Card";
import { formatDate } from "@/lib/utils";
import type { StructuredResumeSummary } from "@/types/api";
import { cn } from "@/lib/utils";

interface ResumeCardProps {
  resume: StructuredResumeSummary;
  selected?: boolean;
  onSelect?: (id: string) => void;
}

export function ResumeCard({ resume, selected, onSelect }: ResumeCardProps) {
  return (
    <Card
      onClick={() => onSelect?.(resume.id)}
      className={cn(
        "cursor-pointer transition-all",
        selected
          ? "border-indigo-500 ring-2 ring-indigo-200"
          : "hover:border-gray-300 hover:shadow"
      )}
    >
      <CardBody className="flex items-center gap-3 py-3">
        <div className="shrink-0 h-9 w-9 rounded-lg bg-indigo-50 flex items-center justify-center">
          <FileText className="h-5 w-5 text-indigo-600" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 truncate">{resume.name}</p>
          <p className="text-xs text-gray-500 flex items-center gap-1 mt-0.5">
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
      </CardBody>
    </Card>
  );
}
