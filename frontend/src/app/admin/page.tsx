// frontend/src/app/admin/page.tsx
//
// Admin page — shows system stats and allows triggering evaluation runs.
// Only accessible to users with role='admin'.
"use client";

import { useState, useEffect } from "react";
import { ShieldCheck, Users, Zap, FileText, ClipboardCheck, Settings2, Download } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { admin, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import type {
  SystemStats,
  EvaluationResponse,
  GenerationConfigResponse,
  GenerationMode,
} from "@/types/api";
import { formatDateTime } from "@/lib/utils";

const DEFAULT_CONFIG: Omit<GenerationConfigResponse, "available_models"> = {
  generation_mode: "simple",
  simple_model: "gpt-5.2",
  phase1_model: "gpt-4.1",
  phase2_model: "gpt-4.1",
};

export default function AdminPage() {
  const { user, loading: authLoading } = useAuth();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);

  // Generation config state
  const [genMode, setGenMode] = useState<GenerationMode>(DEFAULT_CONFIG.generation_mode);
  const [simpleModel, setSimpleModel] = useState(DEFAULT_CONFIG.simple_model);
  const [phase1Model, setPhase1Model] = useState(DEFAULT_CONFIG.phase1_model);
  const [phase2Model, setPhase2Model] = useState(DEFAULT_CONFIG.phase2_model);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [savingConfig, setSavingConfig] = useState(false);
  const [configSaved, setConfigSaved] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // Evaluate run state
  const [evalRunId, setEvalRunId] = useState("");
  const [evaluating, setEvaluating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvaluationResponse | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);

  // Log download state
  const [logFrom, setLogFrom] = useState("");
  const [logTo, setLogTo] = useState("");
  const [downloadingLogs, setDownloadingLogs] = useState(false);
  const [logDownloadError, setLogDownloadError] = useState<string | null>(null);

  // Run data download by date state
  const [rdFrom, setRdFrom] = useState("");
  const [rdTo, setRdTo] = useState("");
  const [downloadingRd, setDownloadingRd] = useState(false);
  const [rdDownloadError, setRdDownloadError] = useState<string | null>(null);

  // Run data download by run ID state
  const [rdRunId, setRdRunId] = useState("");
  const [downloadingRdById, setDownloadingRdById] = useState(false);
  const [rdByIdError, setRdByIdError] = useState<string | null>(null);

  useEffect(() => {
    if (!user || user.role !== "admin") return;
    admin
      .stats()
      .then(setStats)
      .catch((e) => setStatsError(e instanceof ApiError ? e.detail : "Failed to load stats."))
      .finally(() => setLoadingStats(false));

    admin
      .getGenerationConfig()
      .then((cfg) => {
        setGenMode(cfg.generation_mode);
        setSimpleModel(cfg.simple_model);
        setPhase1Model(cfg.phase1_model);
        setPhase2Model(cfg.phase2_model);
        setAvailableModels(cfg.available_models);
      })
      .catch(() => {
        // Use defaults on error; don't block the page
        setAvailableModels(["gpt-5.2", "gpt-5.1", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"]);
      })
      .finally(() => setLoadingConfig(false));
  }, [user]);

  async function handleSaveConfig(e: React.FormEvent) {
    e.preventDefault();
    setSavingConfig(true);
    setConfigError(null);
    setConfigSaved(false);
    try {
      await admin.saveGenerationConfig({
        generation_mode: genMode,
        simple_model: simpleModel,
        phase1_model: phase1Model,
        phase2_model: phase2Model,
      });
      setConfigSaved(true);
      setTimeout(() => setConfigSaved(false), 3000);
    } catch (e) {
      setConfigError(e instanceof ApiError ? e.detail : "Failed to save configuration.");
    } finally {
      setSavingConfig(false);
    }
  }

  function triggerDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleDownloadLogs(e: React.FormEvent) {
    e.preventDefault();
    if (!logFrom) return;
    setDownloadingLogs(true);
    setLogDownloadError(null);
    try {
      const { blob, filename } = await admin.downloadLogs(logFrom, logTo || undefined);
      triggerDownload(blob, filename);
    } catch (e) {
      setLogDownloadError(e instanceof ApiError ? e.detail : "Download failed.");
    } finally {
      setDownloadingLogs(false);
    }
  }

  async function handleDownloadRunData(e: React.FormEvent) {
    e.preventDefault();
    if (!rdFrom) return;
    setDownloadingRd(true);
    setRdDownloadError(null);
    try {
      const { blob, filename } = await admin.downloadRunData(rdFrom, rdTo || undefined);
      triggerDownload(blob, filename);
    } catch (e) {
      setRdDownloadError(e instanceof ApiError ? e.detail : "Download failed.");
    } finally {
      setDownloadingRd(false);
    }
  }

  async function handleDownloadRunDataById(e: React.FormEvent) {
    e.preventDefault();
    if (!rdRunId.trim()) return;
    setDownloadingRdById(true);
    setRdByIdError(null);
    try {
      const { blob, filename } = await admin.downloadRunDataById(rdRunId.trim());
      triggerDownload(blob, filename);
    } catch (e) {
      setRdByIdError(e instanceof ApiError ? e.detail : "Download failed.");
    } finally {
      setDownloadingRdById(false);
    }
  }

  async function handleEvaluate(e: React.FormEvent) {
    e.preventDefault();
    if (!evalRunId.trim()) return;
    setEvaluating(true);
    setEvalError(null);
    setEvalResult(null);
    try {
      const result = await admin.evaluateRun(evalRunId.trim());
      setEvalResult(result);
    } catch (e) {
      setEvalError(e instanceof ApiError ? e.detail : "Evaluation failed.");
    } finally {
      setEvaluating(false);
    }
  }

  if (authLoading) {
    return (
      <AppShell>
        <Spinner size="lg" label="Loading..." />
      </AppShell>
    );
  }

  if (!user || user.role !== "admin") {
    return (
      <AppShell>
        <div className="bg-red-50 border border-red-200 rounded-xl p-8 text-center">
          <ShieldCheck className="h-10 w-10 text-red-400 mx-auto mb-3" />
          <p className="font-semibold text-red-700">Admin access required</p>
          <p className="text-sm text-red-600 mt-1">Your account does not have admin privileges.</p>
        </div>
      </AppShell>
    );
  }

  const modelOptions = availableModels.length > 0
    ? availableModels
    : ["gpt-5.2", "gpt-5.1", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"];

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-indigo-600" />
          Admin dashboard
        </h1>
        <p className="text-gray-500 mt-1">System-wide metrics and admin operations.</p>
      </div>

      {/* System stats */}
      <div className="mb-8">
        <h2 className="text-base font-semibold text-gray-700 mb-3">System statistics</h2>
        {loadingStats ? (
          <Spinner label="Loading stats..." />
        ) : statsError ? (
          <p className="text-sm text-red-600">✗ {statsError}</p>
        ) : stats ? (
          <div className="grid grid-cols-3 gap-4">
            <StatCard icon={<Users className="h-5 w-5 text-blue-600" />} label="Total users" value={stats.total_users} bg="bg-blue-50" />
            <StatCard icon={<Zap className="h-5 w-5 text-indigo-600" />} label="Generation runs" value={stats.total_generation_runs} bg="bg-indigo-50" />
            <StatCard icon={<FileText className="h-5 w-5 text-green-600" />} label="Succeeded" value={stats.total_succeeded_runs} bg="bg-green-50" />
            <StatCard icon={<FileText className="h-5 w-5 text-red-500" />} label="Failed" value={stats.total_failed_runs} bg="bg-red-50" />
            <StatCard icon={<FileText className="h-5 w-5 text-amber-600" />} label="Documents" value={stats.total_tailored_documents} bg="bg-amber-50" />
            <StatCard icon={<ClipboardCheck className="h-5 w-5 text-purple-600" />} label="Evaluations" value={stats.total_evaluation_runs} bg="bg-purple-50" />
          </div>
        ) : null}
      </div>

      {/* Generation configuration */}
      <Card className="max-w-lg mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-indigo-600" />
            Generation configuration
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Choose whether resume tailoring runs use a single-pass flow or a two-phase
            planning&nbsp;+&nbsp;generation flow, and select the OpenAI model(s) used for each mode.
          </p>
        </CardHeader>
        <CardBody>
          {loadingConfig ? (
            <Spinner label="Loading configuration..." />
          ) : (
            <form onSubmit={handleSaveConfig} className="space-y-5">
              {/* Mode radio group */}
              <fieldset>
                <legend className="text-sm font-medium text-gray-700 mb-2">Generation mode</legend>
                <div className="flex gap-4">
                  {(["simple", "two_phase"] as GenerationMode[]).map((m) => (
                    <label
                      key={m}
                      className={`flex items-start gap-2.5 p-3 rounded-lg border cursor-pointer transition-colors flex-1 ${
                        genMode === m
                          ? "border-indigo-500 bg-indigo-50"
                          : "border-gray-200 hover:border-gray-300"
                      }`}
                    >
                      <input
                        type="radio"
                        name="gen_mode"
                        value={m}
                        checked={genMode === m}
                        onChange={() => setGenMode(m)}
                        className="mt-0.5 accent-indigo-600"
                      />
                      <span>
                        <span className="block text-sm font-medium text-gray-900">
                          {m === "simple" ? "Simple" : "Two-phase"}
                        </span>
                        <span className="block text-xs text-gray-500 mt-0.5">
                          {m === "simple"
                            ? "Single-pass generation"
                            : "Planning followed by generation"}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>

              {/* Model selectors */}
              <div className="space-y-3">
                <ModelSelect
                  label="Simple run model"
                  value={simpleModel}
                  onChange={setSimpleModel}
                  options={modelOptions}
                  disabled={genMode !== "simple"}
                />
                <ModelSelect
                  label="Phase 1 model"
                  value={phase1Model}
                  onChange={setPhase1Model}
                  options={modelOptions}
                  disabled={genMode !== "two_phase"}
                />
                <ModelSelect
                  label="Phase 2 model"
                  value={phase2Model}
                  onChange={setPhase2Model}
                  options={modelOptions}
                  disabled={genMode !== "two_phase"}
                />
              </div>

              <div className="flex items-center gap-3">
                <Button type="submit" loading={savingConfig}>
                  Save generation settings
                </Button>
                {configSaved && (
                  <span className="text-sm text-green-600">✓ Saved</span>
                )}
                {configError && (
                  <span className="text-sm text-red-600">✗ {configError}</span>
                )}
              </div>
            </form>
          )}
        </CardBody>
      </Card>

      {/* Evaluate run */}
      <Card className="max-w-lg mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900">Evaluate a generation run</h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Score a completed generation run using the assess pipeline.
            Run must have status&nbsp;<code className="text-xs bg-gray-100 px-1 rounded">succeeded</code>.
          </p>
        </CardHeader>
        <CardBody>
          <form onSubmit={handleEvaluate} className="space-y-4">
            <Input
              label="Generation Run ID"
              value={evalRunId}
              onChange={(e) => setEvalRunId(e.target.value)}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            />
            <Button type="submit" loading={evaluating} disabled={!evalRunId.trim()}>
              <ClipboardCheck className="h-4 w-4" />
              Run evaluation
            </Button>
          </form>

          {evalError && <p className="text-sm text-red-600 mt-3">✗ {evalError}</p>}

          {evalResult && (
            <div className="mt-4 p-4 bg-green-50 border border-green-200 rounded-lg space-y-2">
              <p className="text-sm font-medium text-green-800">Evaluation complete</p>
              <p className="text-xs text-green-700">
                ID: <span className="font-mono">{evalResult.id}</span>
              </p>
              <p className="text-xs text-green-700">
                Created: {formatDateTime(evalResult.created_at)}
              </p>
              <div className="grid grid-cols-2 gap-2 mt-2">
                {Object.entries(evalResult.scores).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between bg-white rounded px-2 py-1.5">
                    <span className="text-xs text-gray-600 capitalize">{k.replace(/_score$/, "")}</span>
                    <Badge variant={v != null ? "success" : "default"}>
                      {v != null ? `${(v * 100).toFixed(0)}%` : "—"}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Log downloader */}
      <Card className="max-w-lg mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <Download className="h-4 w-4 text-indigo-600" />
            Download logs
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Download a ZIP of daily log files for the selected date range.
            Requires <code className="text-xs bg-gray-100 px-1 rounded">LOG_DIR</code> to be configured on the server.
          </p>
        </CardHeader>
        <CardBody>
          <form onSubmit={handleDownloadLogs} className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <DateField label="From date" value={logFrom} onChange={setLogFrom} required />
              <DateField label="To date (optional)" value={logTo} onChange={setLogTo} />
            </div>
            <Button type="submit" loading={downloadingLogs} disabled={!logFrom}>
              <Download className="h-4 w-4" />
              Download logs
            </Button>
          </form>
          {logDownloadError && (
            <p className="text-sm text-red-600 mt-3">✗ {logDownloadError}</p>
          )}
        </CardBody>
      </Card>

      {/* Run data downloader */}
      <Card className="max-w-lg">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <Download className="h-4 w-4 text-indigo-600" />
            Download run data
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Download generation run debug JSON files. Requires{" "}
            <code className="text-xs bg-gray-100 px-1 rounded">RUN_DATA_DIR</code> to be configured on the server.
          </p>
        </CardHeader>
        <CardBody className="space-y-6">
          {/* By date range */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">By date range</p>
            <form onSubmit={handleDownloadRunData} className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <DateField label="From date" value={rdFrom} onChange={setRdFrom} required />
                <DateField label="To date (optional)" value={rdTo} onChange={setRdTo} />
              </div>
              <Button type="submit" loading={downloadingRd} disabled={!rdFrom}>
                <Download className="h-4 w-4" />
                Download run data
              </Button>
            </form>
            {rdDownloadError && (
              <p className="text-sm text-red-600 mt-3">✗ {rdDownloadError}</p>
            )}
          </div>

          <hr className="border-gray-100" />

          {/* By run ID */}
          <div>
            <p className="text-sm font-medium text-gray-700 mb-3">By generation run ID</p>
            <form onSubmit={handleDownloadRunDataById} className="space-y-4">
              <Input
                label="Generation Run ID"
                value={rdRunId}
                onChange={(e) => setRdRunId(e.target.value)}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              />
              <Button type="submit" loading={downloadingRdById} disabled={!rdRunId.trim()}>
                <Download className="h-4 w-4" />
                Download run data
              </Button>
            </form>
            {rdByIdError && (
              <p className="text-sm text-red-600 mt-3">✗ {rdByIdError}</p>
            )}
          </div>
        </CardBody>
      </Card>
    </AppShell>
  );
}

function ModelSelect({
  label,
  value,
  onChange,
  options,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  disabled: boolean;
}) {
  // Ensure current value is shown even if not in the list
  const allOptions = options.includes(value) ? options : [value, ...options];

  return (
    <div>
      <label className={`block text-sm font-medium mb-1 ${disabled ? "text-gray-400" : "text-gray-700"}`}>
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className={`w-full rounded-lg border px-3 py-2 text-sm transition-colors ${
          disabled
            ? "border-gray-200 bg-gray-50 text-gray-400 cursor-not-allowed"
            : "border-gray-300 bg-white text-gray-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
        }`}
      >
        {allOptions.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}

function DateField({
  label,
  value,
  onChange,
  required,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  required?: boolean;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        type="date"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
      />
    </div>
  );
}

function StatCard({
  icon,
  label,
  value,
  bg,
}: {
  icon: React.ReactNode;
  label: string;
  value: number;
  bg: string;
}) {
  return (
    <Card>
      <CardBody className="py-4">
        <div className={`inline-flex h-9 w-9 rounded-lg ${bg} items-center justify-center mb-3`}>
          {icon}
        </div>
        <p className="text-2xl font-bold text-gray-900">{value.toLocaleString()}</p>
        <p className="text-xs text-gray-500 mt-0.5">{label}</p>
      </CardBody>
    </Card>
  );
}
