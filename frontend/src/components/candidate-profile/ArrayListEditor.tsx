// frontend/src/components/candidate-profile/ArrayListEditor.tsx
"use client";
import { useState } from "react";
import { cn } from "@/lib/utils";

interface Props {
  value: string[];
  onChange: (items: string[]) => void;
  placeholder?: string;
  rows?: number;
  className?: string;
}

/**
 * One-item-per-line list editor backed by a textarea.
 * Each non-empty line maps to one array item.
 * Paste of multiline text is supported natively.
 * Keeps local text state so empty lines can exist while typing.
 */
export function ArrayListEditor({ value, onChange, placeholder, rows = 4, className }: Props) {
  const [text, setText] = useState(() => value.join("\n"));

  function handleChange(e: React.ChangeEvent<HTMLTextAreaElement>) {
    const raw = e.target.value;
    setText(raw);
    onChange(raw.split("\n").map(l => l.trim()).filter(Boolean));
  }

  return (
    <textarea
      value={text}
      onChange={handleChange}
      placeholder={placeholder ?? "One item per line"}
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
