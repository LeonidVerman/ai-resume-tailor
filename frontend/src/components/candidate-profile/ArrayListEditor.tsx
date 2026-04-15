// frontend/src/components/candidate-profile/ArrayListEditor.tsx
"use client";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

interface Props {
  value: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
  rows?: number;
  className?: string;
  /**
   * Increment this to force the textarea to re-sync from the `value` prop.
   * Use when loading example data or programmatically replacing the field value.
   */
  resetKey?: string | number;
}

/**
 * List editor backed by a textarea.
 * Accepts items separated by line breaks or commas; each non-empty token
 * maps to one array item. Paste of multiline or comma-separated text is
 * supported natively. Keeps local text state so partial input can exist
 * while typing.
 */
export function ArrayListEditor({
  value, onChange, placeholder, rows = 4, className, resetKey,
}: Props) {
  const [text, setText] = useState(() => value.join("\n"));

  // Re-sync textarea when external value is replaced (e.g. loading example data).
  // Only fires when resetKey changes, not on every parent re-render, so normal
  // typing is never interrupted.
  useEffect(() => {
    setText(value.join("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const raw = e.target.value;
    setText(raw);
    // Accept items separated by newlines OR commas.
    onChange(raw.split(/[\n,]/).map(l => l.trim()).filter(Boolean));
  }

  return (
    <textarea
      value={text}
      onChange={handleChange}
      placeholder={placeholder ?? "One item per row or separated by commas"}
      rows={rows}
      className={cn(
        "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm shadow-sm resize-y",
        "focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent",
        "hover:border-gray-400 placeholder:text-gray-400",
        className
      )}
    />
  );
}
