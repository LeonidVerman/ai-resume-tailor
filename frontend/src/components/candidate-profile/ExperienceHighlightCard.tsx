// frontend/src/components/candidate-profile/ExperienceHighlightCard.tsx
"use client";
import { useState } from "react";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { ArrayListEditor } from "./ArrayListEditor";
import { FieldHelp } from "./FieldHelp";
import type { ExperienceHighlight } from "@/types/api";

interface Props {
  value: ExperienceHighlight;
  index: number;
  onChange: (h: ExperienceHighlight) => void;
  onRemove: () => void;
  resetKey?: string | number;
}

const OPTIONAL_LIST_FIELDS: Array<[keyof ExperienceHighlight, string]> = [
  ["team_context", "Team context"],
  ["architecture_patterns", "Architecture patterns"],
  ["constraints_and_tradeoffs", "Constraints and tradeoffs"],
  ["skills_applied", "Skills applied"],
  ["security_auth_patterns", "Security / auth patterns"],
];

export function ExperienceHighlightCard({ value, index, onChange, onRemove, resetKey }: Props) {
  const [showDetails, setShowDetails] = useState(false);

  function set<K extends keyof ExperienceHighlight>(field: K, val: ExperienceHighlight[K]) {
    onChange({ ...value, [field]: val });
  }

  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-700">Highlight {index + 1}</span>
        <button
          type="button"
          onClick={onRemove}
          className="text-gray-400 hover:text-red-500 transition-colors"
          title="Remove highlight"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {/* Required: Area */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-0.5">
          Area <span className="text-red-500">*</span>
        </label>
        <FieldHelp configKey="experience_highlights.area" />
        <Input
          value={value.area}
          onChange={e => set("area", e.target.value)}
          placeholder="Backend platform development"
          className="mt-1"
        />
      </div>

      {/* Required: Impact */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-0.5">
          Impact <span className="text-red-500">*</span>
        </label>
        <FieldHelp configKey="experience_highlights.impact" />
        <ArrayListEditor
          value={value.impact}
          onChange={v => set("impact", v)}
          placeholder={"Improved application performance and reliability\nResolved production issues and improved system stability"}
          rows={4}
          className="mt-1"
          resetKey={resetKey}
        />
      </div>

      {/* Toggle optional details */}
      <button
        type="button"
        onClick={() => setShowDetails(p => !p)}
        className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 transition-colors"
      >
        {showDetails
          ? <ChevronDown className="h-3.5 w-3.5" />
          : <ChevronRight className="h-3.5 w-3.5" />}
        {showDetails ? "Hide" : "Show"} additional details
      </button>

      {showDetails && (
        <div className="space-y-4 pt-2 border-t border-gray-200">
          {/* Market + Employer relationship */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-0.5">Market</label>
              <FieldHelp configKey="experience_highlights.market" />
              <Input
                value={value.market ?? ""}
                onChange={e => set("market", e.target.value || undefined)}
                placeholder="Japan"
                className="mt-1"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-0.5">Employer relationship</label>
              <FieldHelp configKey="experience_highlights.employer_relationship" />
              <Input
                value={value.employer_relationship ?? ""}
                onChange={e => set("employer_relationship", e.target.value || undefined)}
                placeholder="Independent contractor"
                className="mt-1"
              />
            </div>
          </div>

          {/* Optional list fields */}
          {OPTIONAL_LIST_FIELDS.map(([field, label]) => (
            <div key={field}>
              <label className="block text-sm font-medium text-gray-700 mb-0.5">{label}</label>
              <FieldHelp configKey={`experience_highlights.${field}`} />
              <ArrayListEditor
                value={value[field] as string[]}
                onChange={v => set(field, v)}
                rows={3}
                className="mt-1"
                resetKey={resetKey}
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
