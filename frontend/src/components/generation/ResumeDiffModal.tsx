// frontend/src/components/generation/ResumeDiffModal.tsx
"use client";

import { useState } from "react";
import { X, ChevronDown, ChevronRight } from "lucide-react";
import type {
  ResumeDiffSection,
  ResumeDiffExperienceSection,
  ResumeDiffRole,
} from "@/types/api";

interface ResumeDiffModalProps {
  diff: ResumeDiffSection[];
  onClose: () => void;
}

function isExperienceSection(s: ResumeDiffSection): s is ResumeDiffExperienceSection {
  return "roles" in s;
}

function hasChanges(role: ResumeDiffRole): boolean {
  return role.added.length > 0 || role.removed.length > 0 || role.changed.length > 0;
}

function RoleBlock({ role }: { role: ResumeDiffRole }) {
  const [open, setOpen] = useState(true);
  if (!hasChanges(role)) return null;

  return (
    <div className="mb-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-sm font-medium text-gray-800 hover:text-gray-600 w-full text-left"
      >
        {open ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
        {role.name}
      </button>

      {open && (
        <div className="mt-1.5 space-y-1 pl-5">
          {role.added.map((bullet, i) => (
            <div key={`add-${i}`} className="flex gap-2">
              <span className="text-green-600 font-bold shrink-0 select-none">+</span>
              <span className="text-xs text-green-800 leading-snug">{bullet}</span>
            </div>
          ))}
          {role.removed.map((bullet, i) => (
            <div key={`rm-${i}`} className="flex gap-2">
              <span className="text-red-500 font-bold shrink-0 select-none">−</span>
              <span className="text-xs text-red-700 leading-snug line-through">{bullet}</span>
            </div>
          ))}
          {role.changed.map((change, i) => (
            <div key={`ch-${i}`} className="space-y-0.5">
              <div className="flex gap-2">
                <span className="text-red-500 font-bold shrink-0 select-none">−</span>
                <span className="text-xs text-red-700 leading-snug line-through">{change.before}</span>
              </div>
              <div className="flex gap-2">
                <span className="text-green-600 font-bold shrink-0 select-none">+</span>
                <span className="text-xs text-green-800 leading-snug">{change.after}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TextSectionBlock({ name, before, after }: { name: string; before: string; after: string }) {
  if (before === after) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex gap-2">
        <span className="text-red-500 font-bold shrink-0 select-none text-xs">−</span>
        <p className="text-xs text-red-700 leading-snug line-through whitespace-pre-wrap">{before}</p>
      </div>
      <div className="flex gap-2">
        <span className="text-green-600 font-bold shrink-0 select-none text-xs">+</span>
        <p className="text-xs text-green-800 leading-snug whitespace-pre-wrap">{after}</p>
      </div>
    </div>
  );
}

export function ResumeDiffModal({ diff, onClose }: ResumeDiffModalProps) {
  const hasAnything = diff.some((section) => {
    if (isExperienceSection(section)) {
      return section.roles.some(hasChanges);
    }
    return (section as { before: string; after: string }).before !== (section as { before: string; after: string }).after;
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[85vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="font-semibold text-gray-900">Resume changes</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-5 py-4 space-y-5">
          {!hasAnything && (
            <p className="text-sm text-gray-500">No changes detected between the source and tailored resume.</p>
          )}

          {diff.map((section, idx) => {
            if (isExperienceSection(section)) {
              const changed = section.roles.filter(hasChanges);
              if (changed.length === 0) return null;
              return (
                <div key={idx}>
                  <h3 className="text-sm font-semibold text-gray-700 mb-2 uppercase tracking-wide text-xs">
                    {section.name}
                  </h3>
                  {changed.map((role, rIdx) => (
                    <RoleBlock key={rIdx} role={role} />
                  ))}
                </div>
              );
            }

            const { name, before, after } = section as { name: string; before: string; after: string };
            if (before === after) return null;
            return (
              <div key={idx}>
                <h3 className="text-sm font-semibold text-gray-700 mb-2 uppercase tracking-wide text-xs">
                  {name}
                </h3>
                <TextSectionBlock name={name} before={before} after={after} />
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-100 bg-gray-50 rounded-b-xl flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 text-sm text-gray-600 hover:text-gray-900 font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
