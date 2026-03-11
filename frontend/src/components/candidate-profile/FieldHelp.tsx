// frontend/src/components/candidate-profile/FieldHelp.tsx
import fieldConfig from "@/config/candidateProfileFieldConfig.json";

interface FieldMeta {
  description: string;
  examples: string[];
}

interface Props {
  configKey: string;
}

/**
 * Renders description and example values for a form field.
 * Config is sourced from candidateProfileFieldConfig.json — not hardcoded here.
 */
export function FieldHelp({ configKey }: Props) {
  const cfg = (fieldConfig as Record<string, FieldMeta>)[configKey];
  if (!cfg) return null;
  return (
    <p className="text-xs text-gray-400 mt-0.5">
      {cfg.description}
      {cfg.examples?.length > 0 && (
        <span className="ml-1 italic">
          {" e.g. "}{cfg.examples.slice(0, 2).map(e => `"${e}"`).join(", ")}
        </span>
      )}
    </p>
  );
}
