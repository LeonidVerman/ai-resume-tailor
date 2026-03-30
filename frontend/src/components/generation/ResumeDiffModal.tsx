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

// ---------------------------------------------------------------------------
// Inline word-level diff
// ---------------------------------------------------------------------------

type InlineOp = { type: "equal" | "remove" | "add"; text: string };

/**
 * Compute a word-level diff between two strings using LCS.
 * Tokens are whitespace-delimited words and the whitespace runs between them,
 * so spacing is preserved naturally in the output.
 */
function computeInlineDiff(before: string, after: string): InlineOp[] {
  const tokenize = (s: string): string[] => s.match(/\S+|\s+/g) ?? [];
  const a = tokenize(before);
  const b = tokenize(after);
  const m = a.length;
  const n = b.length;

  // Build LCS DP table
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] =
        a[i - 1] === b[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // Backtrack to reconstruct ops
  const raw: InlineOp[] = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) {
      raw.unshift({ type: "equal", text: a[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      raw.unshift({ type: "add", text: b[j - 1] });
      j--;
    } else {
      raw.unshift({ type: "remove", text: a[i - 1] });
      i--;
    }
  }

  // Merge adjacent ops of the same type
  const merged = raw.reduce<InlineOp[]>((acc, op) => {
    if (acc.length > 0 && acc[acc.length - 1].type === op.type) {
      acc[acc.length - 1] = { type: op.type, text: acc[acc.length - 1].text + op.text };
    } else {
      acc.push({ ...op });
    }
    return acc;
  }, []);

  return ensureWordBoundaries(clusterIntoPhraseOps(merged));
}

/**
 * Final pass: when a remove and add span are directly adjacent and neither
 * provides boundary whitespace, insert an equal " " between them.
 *
 * Root cause: the LCS algorithm can place the inter-word space AFTER both
 * edits (as part of the next equal segment) rather than between them.
 * Example: [equal "with "][remove "Senior"][add "15+"][equal " backend"]
 * renders as "with ~~Senior~~15+ backend" — "Senior" and "15+" glue together.
 * This pass inserts the missing separator so it reads "with ~~Senior~~ 15+".
 */
function ensureWordBoundaries(ops: InlineOp[]): InlineOp[] {
  const out: InlineOp[] = [];
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    const prev = out.length > 0 ? out[out.length - 1] : null;
    if (
      prev !== null &&
      prev.type !== "equal" &&
      op.type !== "equal" &&
      prev.type !== op.type &&
      /\S$/.test(prev.text) &&
      /^\S/.test(op.text)
    ) {
      out.push({ type: "equal", text: " " });
    }
    out.push(op);
  }
  return out;
}

/**
 * Bridge small equal gaps (≤ BRIDGE_MAX_CHARS) between adjacent edits so that
 * runs like [remove "foo"][equal " ""][add "bar"] collapse into a single
 * [remove "foo "][add "bar"] phrase span instead of token soup.
 */
const BRIDGE_MAX_CHARS = 3;

function clusterIntoPhraseOps(ops: InlineOp[]): InlineOp[] {
  const result: InlineOp[] = [];
  let i = 0;
  const n = ops.length;

  while (i < n) {
    if (ops[i].type === "equal") {
      result.push(ops[i++]);
      continue;
    }

    // Start of a non-equal cluster — scan forward, bridging small equal gaps
    let j = i;
    let clusterEndJ = i;

    while (j < n) {
      if (ops[j].type !== "equal") {
        j++;
        clusterEndJ = j;
      } else {
        // Measure how many chars until the next non-equal op
        let gapChars = 0;
        let k = j;
        while (k < n && ops[k].type === "equal") {
          gapChars += ops[k].text.length;
          k++;
        }
        // Bridge only if the gap is small AND there is a non-equal op after it
        if (gapChars <= BRIDGE_MAX_CHARS && k < n) {
          j = k; // jump past the gap and continue extending the cluster
        } else {
          break; // gap too wide (or end of ops) — stop
        }
      }
    }

    // Build before/after text for this cluster
    let beforeText = "";
    let afterText = "";
    for (let k = i; k < clusterEndJ; k++) {
      const op = ops[k];
      if (op.type !== "add") beforeText += op.text;
      if (op.type !== "remove") afterText += op.text;
    }

    if (beforeText) result.push({ type: "remove", text: beforeText });
    if (afterText) result.push({ type: "add", text: afterText });

    i = clusterEndJ;
  }

  return result;
}

function InlineDiffView({ before, after }: { before: string; after: string }) {
  const ops = computeInlineDiff(before, after);
  return (
    <>
      {ops.map((op, i) => {
        if (op.type === "equal") return <span key={i}>{op.text}</span>;
        if (op.type === "remove")
          return (
            <span key={i} className="bg-red-100 text-red-700 line-through rounded-sm">
              {op.text}
            </span>
          );
        return (
          <span key={i} className="bg-green-100 text-green-800 rounded-sm">
            {op.text}
          </span>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Block components
// ---------------------------------------------------------------------------

function isExperienceSection(s: ResumeDiffSection): s is ResumeDiffExperienceSection {
  return "roles" in s;
}

function hasChanges(role: ResumeDiffRole): boolean {
  return (role.added?.length ?? 0) > 0 || (role.removed?.length ?? 0) > 0 || (role.changed?.length ?? 0) > 0;
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
          {/* Added bullets */}
          {(role.added ?? []).map((bullet, i) => (
            <div key={`add-${i}`} className="flex gap-2">
              <span className="text-green-600 font-bold shrink-0 select-none">+</span>
              <span className="text-xs text-green-800 leading-snug">{bullet}</span>
            </div>
          ))}

          {/* Removed bullets */}
          {(role.removed ?? []).map((bullet, i) => (
            <div key={`rm-${i}`} className="flex gap-2">
              <span className="text-red-500 font-bold shrink-0 select-none">−</span>
              <span className="text-xs text-red-700 leading-snug line-through">{bullet}</span>
            </div>
          ))}

          {/* Modified bullets — inline word-level diff */}
          {(role.changed ?? []).map((change, i) => (
            <div key={`ch-${i}`} className="flex gap-2">
              <span className="text-amber-500 font-bold shrink-0 select-none">~</span>
              <span className="text-xs text-gray-700 leading-snug">
                <InlineDiffView before={change.before} after={change.after} />
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TextSectionBlock({ before, after }: { name: string; before: string; after: string }) {
  if (before === after) return null;
  return (
    <div className="flex gap-2">
      <span className="text-amber-500 font-bold shrink-0 select-none text-xs">~</span>
      <p className="text-xs text-gray-700 leading-snug whitespace-pre-wrap">
        <InlineDiffView before={before} after={after} />
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

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
