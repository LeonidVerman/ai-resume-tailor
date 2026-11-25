// frontend/src/app/admin/page.tsx
//
// Admin page — shows system stats and allows triggering evaluation runs and benchmarks.
// Only accessible to users with role='admin'.
"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, Users, Zap, FileText, ClipboardCheck, Settings2, Download, BarChart2, Eye, Rocket } from "lucide-react";
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
  GenerationMode,
  BenchmarkRunSummary,
  BenchmarkRunDetail,
  GuestMetricsResponse,
  SignupCreditPolicyResponse,
} from "@/types/api";
import { formatDateTime } from "@/lib/utils";

export default function AdminPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);

  // Signup credit policy state
  const [signupPolicy, setSignupPolicy] = useState<SignupCreditPolicyResponse | null>(null);
  const [initialCredits, setInitialCredits] = useState(3);
  const [loadingSignupPolicy, setLoadingSignupPolicy] = useState(true);
  const [savingSignupPolicy, setSavingSignupPolicy] = useState(false);
  const [signupPolicySaved, setSignupPolicySaved] = useState(false);
  const [signupPolicyError, setSignupPolicyError] = useState<string | null>(null);

  // Generation config state
  const [simpleModel, setSimpleModel] = useState("gpt-5.2");
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

  // Classification tool state
  const [classifySelectedFile, setClassifySelectedFile] = useState<File | null>(null);
  const [classifying, setClassifying] = useState(false);
  const [classifyError, setClassifyError] = useState<string | null>(null);
  const classifyFileRef = useRef<HTMLInputElement>(null);

  // Benchmark state
  const [benchClientId, setBenchClientId] = useState("");
  const [benchAssessModel, setBenchAssessModel] = useState("gpt-5.2");
  const [benchGenerationMode, setBenchGenerationMode] = useState<GenerationMode>("conservative");
  const [startingBenchmark, setStartingBenchmark] = useState(false);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);
  const [benchmarkRuns, setBenchmarkRuns] = useState<BenchmarkRunSummary[]>([]);
  const [loadingBenchmarkRuns, setLoadingBenchmarkRuns] = useState(false);
  const [selectedBenchmarkRun, setSelectedBenchmarkRun] = useState<BenchmarkRunDetail | null>(null);
  const [loadingBenchmarkDetail, setLoadingBenchmarkDetail] = useState(false);
  const [downloadingBenchmarkZip, setDownloadingBenchmarkZip] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Guest generation (issue #155) state
  const [guestMetrics, setGuestMetrics] = useState<GuestMetricsResponse | null>(null);
  const [loadingGuestMetrics, setLoadingGuestMetrics] = useState(true);
  const [guestMetricsError, setGuestMetricsError] = useState<string | null>(null);
  const [guestEnabled, setGuestEnabled] = useState(false);
  const [guestDailyCap, setGuestDailyCap] = useState(25);
  const [guestConcurrentCap, setGuestConcurrentCap] = useState(2);
  const [guestIpDailyLimit, setGuestIpDailyLimit] = useState(3);
  const [guestRetentionDays, setGuestRetentionDays] = useState(7);
  const [savingGuestConfig, setSavingGuestConfig] = useState(false);
  const [guestConfigSaved, setGuestConfigSaved] = useState(false);
  const [guestConfigError, setGuestConfigError] = useState<string | null>(null);

  const [grantUserId, setGrantUserId] = useState("");
  const [grantAmount, setGrantAmount] = useState(10);
  const [granting, setGranting] = useState(false);
  const [grantResult, setGrantResult] = useState<string | null>(null);
  const [grantError, setGrantError] = useState<string | null>(null);

  const hasActiveBenchmark = benchmarkRuns.some(
    (r) => r.status === "queued" || r.status === "running"
  );

  function loadGuestMetrics() {
    setLoadingGuestMetrics(true);
    admin
      .getGuestMetrics()
      .then((m) => {
        setGuestMetrics(m);
        setGuestEnabled(m.config.guest_enabled);
        setGuestDailyCap(m.config.guest_daily_global_cap);
        setGuestConcurrentCap(m.config.guest_concurrent_cap);
        setGuestIpDailyLimit(m.config.guest_ip_daily_limit);
        setGuestRetentionDays(m.config.guest_retention_days);
      })
      .catch((e) =>
        setGuestMetricsError(e instanceof ApiError ? e.detail : "Failed to load guest metrics.")
      )
      .finally(() => setLoadingGuestMetrics(false));
  }

  function loadBenchmarkRuns() {
    setLoadingBenchmarkRuns(true);
    admin
      .listBenchmarkRuns(20)
      .then(setBenchmarkRuns)
      .catch(() => {})
      .finally(() => setLoadingBenchmarkRuns(false));
  }

  // Redirect non-admin authenticated users to dashboard
  useEffect(() => {
    if (!authLoading && user && user.role !== "admin") {
      router.replace("/dashboard");
    }
  }, [authLoading, user, router]);

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
        setSimpleModel(cfg.simple_model);
        setAvailableModels(cfg.available_models);
      })
      .catch(() => {
        // Use defaults on error; don't block the page
        setAvailableModels(["gpt-5.2", "gpt-5.1", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "gpt-4.1-mini"]);
      })
      .finally(() => setLoadingConfig(false));

    admin
      .getSignupCreditPolicy()
      .then((p) => {
        setSignupPolicy(p);
        setInitialCredits(p.initial_credits);
      })
      .catch(() => {})
      .finally(() => setLoadingSignupPolicy(false));

    loadGuestMetrics();
    loadBenchmarkRuns();
  }, [user]);

  // Poll while a benchmark is active
  useEffect(() => {
    if (hasActiveBenchmark) {
      if (!pollRef.current) {
        pollRef.current = setInterval(loadBenchmarkRuns, 5000);
      }
    } else {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [hasActiveBenchmark]);

  async function handleSaveConfig(e: React.FormEvent) {
    e.preventDefault();
    setSavingConfig(true);
    setConfigError(null);
    setConfigSaved(false);
    try {
      await admin.saveGenerationConfig({ simple_model: simpleModel });
      setConfigSaved(true);
      setTimeout(() => setConfigSaved(false), 3000);
    } catch (e) {
      setConfigError(e instanceof ApiError ? e.detail : "Failed to save configuration.");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleSaveSignupPolicy() {
    setSavingSignupPolicy(true);
    setSignupPolicyError(null);
    setSignupPolicySaved(false);
    try {
      await admin.saveSignupCreditPolicy({ initial_credits: initialCredits });
      const updated = await admin.getSignupCreditPolicy();
      setSignupPolicy(updated);
      setInitialCredits(updated.initial_credits);
      setSignupPolicySaved(true);
      setTimeout(() => setSignupPolicySaved(false), 3000);
    } catch (e) {
      setSignupPolicyError(e instanceof ApiError ? e.detail : "Failed to save policy.");
    } finally {
      setSavingSignupPolicy(false);
    }
  }

  async function handleSaveGuestConfig(e: React.FormEvent) {
    e.preventDefault();
    setSavingGuestConfig(true);
    setGuestConfigError(null);
    setGuestConfigSaved(false);
    try {
      await admin.saveGenerationConfig({
        guest_enabled: guestEnabled,
        guest_daily_global_cap: guestDailyCap,
        guest_concurrent_cap: guestConcurrentCap,
        guest_ip_daily_limit: guestIpDailyLimit,
        guest_retention_days: guestRetentionDays,
      });
      setGuestConfigSaved(true);
      setTimeout(() => setGuestConfigSaved(false), 3000);
      loadGuestMetrics();
    } catch (e) {
      setGuestConfigError(e instanceof ApiError ? e.detail : "Failed to save guest settings.");
    } finally {
      setSavingGuestConfig(false);
    }
  }

  async function handleGrantCredits(e: React.FormEvent) {
    e.preventDefault();
    if (!grantUserId.trim()) return;
    setGranting(true);
    setGrantResult(null);
    setGrantError(null);
    try {
      const res = await admin.grantCredits(grantUserId.trim(), grantAmount);
      setGrantResult(`Done — adjusted ${res.credits_adjusted > 0 ? "+" : ""}${res.credits_adjusted} credits for ${res.user_id}`);
      setGrantUserId("");
    } catch (e) {
      setGrantError(e instanceof ApiError ? e.detail : "Failed to grant credits.");
    } finally {
      setGranting(false);
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

  async function handleClassifyFile(e: React.FormEvent) {
    e.preventDefault();
    if (!classifySelectedFile) return;
    setClassifying(true);
    setClassifyError(null);
    try {
      const { classificationBlob, classificationFilename, inputBlob, inputFilename } =
        await admin.classifyFile(classifySelectedFile);
      triggerDownload(classificationBlob, classificationFilename);
      triggerDownload(inputBlob, inputFilename);
    } catch (e) {
      setClassifyError(e instanceof ApiError ? e.detail : "Classification failed.");
    } finally {
      setClassifying(false);
    }
  }

  async function handleStartBenchmark(e: React.FormEvent) {
    e.preventDefault();
    if (!benchClientId.trim()) return;
    setStartingBenchmark(true);
    setBenchmarkError(null);
    try {
      await admin.startBenchmark(benchClientId.trim(), benchAssessModel, benchGenerationMode);
      setBenchClientId("");
      loadBenchmarkRuns();
    } catch (e) {
      setBenchmarkError(e instanceof ApiError ? e.detail : "Failed to start benchmark.");
    } finally {
      setStartingBenchmark(false);
    }
  }

  async function handleViewBenchmark(id: number) {
    setLoadingBenchmarkDetail(true);
    try {
      const detail = await admin.getBenchmarkRun(id);
      setSelectedBenchmarkRun(detail);
    } catch (e) {
      // ignore
    } finally {
      setLoadingBenchmarkDetail(false);
    }
  }

  async function handleDownloadBenchmarkZip(id: number) {
    setDownloadingBenchmarkZip(id);
    try {
      const { blob, filename } = await admin.downloadBenchmarkZip(id);
      triggerDownload(blob, filename);
    } catch (e) {
      // ignore
    } finally {
      setDownloadingBenchmarkZip(null);
    }
  }

  async function handleEvaluate(e: React.FormEvent) {
    e.preventDefault();
    if (!evalRunId.trim()) return;
    setEvaluating(true);
    setEvalError(null);
    setEvalResult(null);
    try {
      const result = await admin.evaluateRun(Number(evalRunId.trim()));
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
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-gray-900 flex items-center gap-2">
            <ShieldCheck className="h-6 w-6 text-indigo-600" />
            Admin dashboard
          </h1>
          <p className="text-gray-500 mt-1">System-wide metrics and admin operations.</p>
        </div>
        {stats && (
          <div className="text-right text-sm text-gray-500 leading-snug">
            <div className="font-semibold text-gray-700">CVRocket {stats.app_version}</div>
            {stats.build_date && <div>Built {stats.build_date}</div>}
          </div>
        )}
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
            Select the OpenAI model used for single-pass resume tailoring.
          </p>
        </CardHeader>
        <CardBody>
          {loadingConfig ? (
            <Spinner label="Loading configuration..." />
          ) : (
            <form onSubmit={handleSaveConfig} className="space-y-5">
              <ModelSelect
                label="Generation model"
                value={simpleModel}
                onChange={setSimpleModel}
                options={modelOptions}
              />

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

      {/* Signup credit policy */}
      <Card className="max-w-lg mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-indigo-600" />
            Initial signup credits
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Controls how many free generation credits newly registered users receive.
            Changes apply only to future signups.
          </p>
        </CardHeader>
        <CardBody>
          {loadingSignupPolicy ? (
            <Spinner label="Loading policy..." />
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Credits per new user
                </label>
                <input
                  type="number"
                  min={0}
                  max={1000}
                  value={initialCredits}
                  onChange={(e) => setInitialCredits(Math.max(0, parseInt(e.target.value) || 0))}
                  className="w-28 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-400"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Currently active: <span className="font-medium">{signupPolicy?.initial_credits ?? "—"}</span>
                </p>
              </div>

              <div className="flex items-center gap-3">
                <Button
                  onClick={handleSaveSignupPolicy}
                  loading={savingSignupPolicy}
                  disabled={initialCredits === signupPolicy?.initial_credits}
                >
                  Save
                </Button>
                {signupPolicySaved && (
                  <span className="text-sm text-green-600">✓ Saved</span>
                )}
                {signupPolicyError && (
                  <span className="text-sm text-red-600">✗ {signupPolicyError}</span>
                )}
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Guest generation (issue #155) */}
      <Card className="mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <Rocket className="h-4 w-4 text-indigo-600" />
            Guest generation
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Kill switch and caps for the public /try flow, plus the guest funnel
            for the last 14 days.
          </p>
        </CardHeader>
        <CardBody className="space-y-6">
          {loadingGuestMetrics && !guestMetrics ? (
            <Spinner label="Loading guest metrics..." />
          ) : guestMetricsError ? (
            <p className="text-sm text-red-600">✗ {guestMetricsError}</p>
          ) : (
            <>
              <form onSubmit={handleSaveGuestConfig} className="space-y-4">
                <label className="flex items-center gap-2.5 text-sm text-gray-700 cursor-pointer w-fit">
                  <input
                    type="checkbox"
                    checked={guestEnabled}
                    onChange={(e) => setGuestEnabled(e.target.checked)}
                    className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                  />
                  <span className="font-medium">
                    Guest trial enabled
                    <span className="font-normal text-gray-500"> (kill switch — off disables /try entirely)</span>
                  </span>
                </label>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 max-w-2xl">
                  <GuestNumField
                    label="Daily global cap"
                    value={guestDailyCap}
                    min={0}
                    onChange={setGuestDailyCap}
                  />
                  <GuestNumField
                    label="Concurrent cap"
                    value={guestConcurrentCap}
                    min={0}
                    onChange={setGuestConcurrentCap}
                  />
                  <GuestNumField
                    label="IP daily limit"
                    value={guestIpDailyLimit}
                    min={0}
                    onChange={setGuestIpDailyLimit}
                  />
                  <GuestNumField
                    label="Retention (days)"
                    value={guestRetentionDays}
                    min={1}
                    onChange={setGuestRetentionDays}
                  />
                </div>
                <div className="flex items-center gap-3">
                  <Button type="submit" loading={savingGuestConfig}>
                    Save guest settings
                  </Button>
                  {guestConfigSaved && (
                    <span className="text-sm text-green-600">✓ Saved</span>
                  )}
                  {guestConfigError && (
                    <span className="text-sm text-red-600">✗ {guestConfigError}</span>
                  )}
                </div>
              </form>

              {guestMetrics && (
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-2">
                    Guest funnel — last 14 days (UTC)
                  </p>
                  <div className="overflow-x-auto rounded border border-gray-200">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-50 border-b border-gray-200">
                        <tr>
                          <th className="text-left px-3 py-2 font-medium text-gray-600">Date</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-600">Sessions</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-600">Profiles</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-600">Started</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-600">Completed</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-600">Failed</th>
                          <th className="text-right px-3 py-2 font-medium text-gray-600">Claims</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100">
                        {guestMetrics.days.map((d) => (
                          <tr key={d.date} className="hover:bg-gray-50">
                            <td className="px-3 py-1.5 text-gray-700 font-mono">{d.date}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{d.sessions}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{d.profiles}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{d.generations_started}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{d.generations_completed}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{d.generations_failed}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{d.claims}</td>
                          </tr>
                        ))}
                        <tr className="bg-gray-50 font-semibold">
                          <td className="px-3 py-2 text-gray-700">Total</td>
                          <td className="px-3 py-2 text-right tabular-nums">{guestMetrics.totals.sessions}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{guestMetrics.totals.profiles}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{guestMetrics.totals.generations_started}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{guestMetrics.totals.generations_completed}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{guestMetrics.totals.generations_failed}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{guestMetrics.totals.claims}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}
        </CardBody>
      </Card>

      {/* Grant credits */}
      <Card className="max-w-lg mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-indigo-600" />
            Grant credits
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Add or deduct generation credits for a specific user. Use a negative amount to deduct.
          </p>
        </CardHeader>
        <CardBody>
          <form onSubmit={handleGrantCredits} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">User ID</label>
              <input
                type="text"
                value={grantUserId}
                onChange={(e) => setGrantUserId(e.target.value)}
                placeholder="UUID"
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-400"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Credits</label>
              <input
                type="number"
                value={grantAmount}
                onChange={(e) => setGrantAmount(parseInt(e.target.value) || 0)}
                className="w-28 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-400"
              />
            </div>
            <div className="flex items-center gap-3">
              <Button type="submit" loading={granting} disabled={!grantUserId.trim()}>
                Grant
              </Button>
              {grantResult && <span className="text-sm text-green-600">✓ {grantResult}</span>}
              {grantError && <span className="text-sm text-red-600">✗ {grantError}</span>}
            </div>
          </form>
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

      {/* Benchmark */}
      <Card className="max-w-lg mb-8">
        <CardHeader>
          <h2 className="font-semibold text-gray-900 flex items-center gap-2">
            <BarChart2 className="h-4 w-4 text-indigo-600" />
            Run Benchmark
          </h2>
          <p className="text-sm text-gray-500 mt-0.5">
            Run the assess pipeline against the fixed positions set using the current
            generation mode and model settings.
          </p>
          <div className="mt-2 flex items-center gap-3 flex-wrap">
            <div className="p-2 bg-gray-50 rounded-lg text-xs text-gray-600 flex-1 min-w-0">
              {loadingConfig ? (
                <span>Loading config…</span>
              ) : (
                <span>Model: <strong>{simpleModel}</strong></span>
              )}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <span className="text-xs text-gray-500 whitespace-nowrap">Assess:</span>
              <select
                value={benchAssessModel}
                onChange={(e) => setBenchAssessModel(e.target.value)}
                disabled={hasActiveBenchmark}
                className="text-xs border border-gray-300 rounded-md px-2 py-1 bg-white text-gray-800 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
              >
                {(availableModels.length > 0 ? availableModels : ["gpt-4o", "gpt-4.1", "gpt-5.2"]).map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <span className="text-xs text-gray-500 whitespace-nowrap">Mode:</span>
              <select
                value={benchGenerationMode}
                onChange={(e) => setBenchGenerationMode(e.target.value as GenerationMode)}
                disabled={hasActiveBenchmark}
                className="text-xs border border-gray-300 rounded-md px-2 py-1 bg-white text-gray-800 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-50"
              >
                <option value="conservative">Conservative</option>
                <option value="normal">Normal</option>
                <option value="aggressive">Aggressive</option>
              </select>
            </div>
          </div>
        </CardHeader>
        <CardBody>
          {hasActiveBenchmark && (
            <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg">
              {benchmarkRuns
                .filter((r) => r.status === "queued" || r.status === "running")
                .map((r) => (
                  <div key={r.id} className="text-sm">
                    <p className="font-medium text-amber-800">
                      A benchmark run is currently active
                    </p>
                    <p className="text-amber-700 mt-0.5">
                      Status: <strong>{r.status}</strong> &nbsp;·&nbsp; Client: <code className="text-xs">{r.client_id}</code>
                    </p>
                    <p className="text-amber-700">
                      Progress: {r.completed_positions} / {r.positions_count ?? "?"} positions
                    </p>
                    {r.started_at && (
                      <p className="text-amber-700">Started: {formatDateTime(r.started_at)}</p>
                    )}
                  </div>
                ))}
            </div>
          )}
          <form onSubmit={handleStartBenchmark} className="space-y-4">
            <Input
              label="Client ID (User ID)"
              value={benchClientId}
              onChange={(e) => setBenchClientId(e.target.value)}
              placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
              disabled={hasActiveBenchmark}
            />
            <Button
              type="submit"
              loading={startingBenchmark}
              disabled={!benchClientId.trim() || hasActiveBenchmark}
            >
              <BarChart2 className="h-4 w-4" />
              {hasActiveBenchmark ? "Benchmark active…" : "Run Benchmark"}
            </Button>
          </form>
          {benchmarkError && (
            <p className="text-sm text-red-600 mt-3">✗ {benchmarkError}</p>
          )}
        </CardBody>
      </Card>

      {/* Recent Benchmark Runs */}
      <Card className="mb-8">
        <CardHeader>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-gray-900">Recent Benchmark Runs</h2>
            <Button variant="ghost" onClick={loadBenchmarkRuns} disabled={loadingBenchmarkRuns}>
              {loadingBenchmarkRuns ? <Spinner size="sm" /> : "Refresh"}
            </Button>
          </div>
        </CardHeader>
        <CardBody className="p-0">
          {loadingBenchmarkRuns && benchmarkRuns.length === 0 ? (
            <div className="p-4"><Spinner label="Loading benchmark runs…" /></div>
          ) : benchmarkRuns.length === 0 ? (
            <p className="p-4 text-sm text-gray-500">No benchmark runs yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Run ID</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Client</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Status</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Mode</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Started</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Completed</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Positions</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Score</th>
                    <th className="text-left px-4 py-2 font-medium text-gray-600">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {benchmarkRuns.map((r) => (
                    <tr key={r.id} className="hover:bg-gray-50">
                      <td className="px-4 py-2 font-mono text-xs text-gray-500 max-w-[120px] truncate">{r.id}</td>
                      <td className="px-4 py-2 font-mono text-xs text-gray-600 max-w-[120px] truncate">{r.client_id}</td>
                      <td className="px-4 py-2">
                        <BenchmarkStatusBadge status={r.status} />
                      </td>
                      <td className="px-4 py-2">
                        <BenchmarkModeBadge mode={r.generation_mode} />
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {r.started_at ? formatDateTime(r.started_at) : "—"}
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {r.completed_at ? formatDateTime(r.completed_at) : "—"}
                      </td>
                      <td className="px-4 py-2 text-xs text-gray-600">
                        {r.status === "running" || r.status === "queued"
                          ? `${r.completed_positions} / ${r.positions_count ?? "?"}`
                          : r.positions_count ?? "—"}
                      </td>
                      <td className="px-4 py-2">
                        {r.integrated_score != null ? (
                          <span className={`font-medium ${r.integrated_score >= 7 ? "text-green-700" : r.integrated_score >= 5 ? "text-amber-700" : "text-red-700"}`}>
                            {r.integrated_score.toFixed(2)}
                          </span>
                        ) : "—"}
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex gap-2">
                          <button
                            onClick={() => handleViewBenchmark(r.id)}
                            className="text-indigo-600 hover:text-indigo-800 text-xs flex items-center gap-1"
                          >
                            <Eye className="h-3 w-3" /> View
                          </button>
                          {r.status === "completed" && (
                            <button
                              onClick={() => handleDownloadBenchmarkZip(r.id)}
                              disabled={downloadingBenchmarkZip === r.id}
                              className="text-indigo-600 hover:text-indigo-800 text-xs flex items-center gap-1 disabled:opacity-50"
                            >
                              <Download className="h-3 w-3" />
                              {downloadingBenchmarkZip === r.id ? "…" : "ZIP"}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Benchmark detail modal */}
      {selectedBenchmarkRun && (
        <BenchmarkDetailModal
          run={selectedBenchmarkRun}
          onClose={() => setSelectedBenchmarkRun(null)}
          onDownload={handleDownloadBenchmarkZip}
          downloading={downloadingBenchmarkZip}
        />
      )}

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

      {/* Resume Classification */}
      <Card>
        <CardHeader>
          <FileText className="h-5 w-5 text-purple-600" />
          <h2 className="text-base font-semibold text-gray-900">Resume Classification</h2>
        </CardHeader>
        <CardBody>
          <p className="text-sm text-gray-500 mb-4">
            Upload a DOCX or PDF resume to run LLM classification. Downloads two files:
            the classification output and the LLM input (for debugging). Nothing is stored.
          </p>
          <form onSubmit={handleClassifyFile} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Resume file</label>
              <input
                ref={classifyFileRef}
                type="file"
                accept=".docx,.pdf"
                className="block w-full text-sm text-gray-700 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border file:border-gray-300 file:bg-white file:text-sm file:font-medium file:text-gray-700 hover:file:bg-gray-50"
                onChange={(e) => {
                  setClassifySelectedFile(e.target.files?.[0] ?? null);
                  setClassifyError(null);
                }}
              />
            </div>
            <Button type="submit" loading={classifying} disabled={!classifySelectedFile}>
              <Download className="h-4 w-4" />
              Classify &amp; download output + input JSON
            </Button>
          </form>
          {classifyError && (
            <p className="text-sm text-red-600 mt-3">✗ {classifyError}</p>
          )}
        </CardBody>
      </Card>
    </AppShell>
  );
}

// ── Benchmark helper components ───────────────────────────────────────────

function BenchmarkModeBadge({ mode }: { mode: GenerationMode | null }) {
  if (!mode || mode === "conservative") return <span className="text-xs text-gray-400">Conservative</span>;
  if (mode === "normal") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">Normal</span>;
  return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-orange-100 text-orange-700">Aggressive</span>;
}

function BenchmarkStatusBadge({ status }: { status: string }) {
  const variants: Record<string, string> = {
    queued: "bg-gray-100 text-gray-700",
    running: "bg-blue-100 text-blue-700",
    completed: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${variants[status] ?? "bg-gray-100 text-gray-700"}`}>
      {status}
    </span>
  );
}

const _SCORE_LABELS: Record<string, string> = {
  truthfulness_score: "Truthfulness",
  role_fit_score: "Role Fit",
  seniority_positioning_score: "Seniority",
  clarity_impact_score: "Clarity / Impact",
  mechanism_quality_score: "Mechanism Quality",
  constraint_compliance_score: "Constraint Compliance",
  cover_letter_effectiveness_score: "Cover Letter",
  overall_readiness_score: "Overall Readiness",
  integrated_score: "Integrated Score",
};

function BenchmarkDetailModal({
  run,
  onClose,
  onDownload,
  downloading,
}: {
  run: BenchmarkRunDetail;
  onClose: () => void;
  onDownload: (id: number) => void;
  downloading: number | null;
}) {
  const scoreKeys = Object.keys(_SCORE_LABELS);
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h3 className="font-semibold text-gray-900">Benchmark Run Details</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-lg leading-none">✕</button>
        </div>
        <div className="p-4 space-y-4">
          {/* Meta */}
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div><span className="text-gray-500">ID:</span> <code className="text-xs">{run.id}</code></div>
            <div><span className="text-gray-500">Client:</span> <code className="text-xs">{run.client_id}</code></div>
            <div><span className="text-gray-500">Status:</span> <BenchmarkStatusBadge status={run.status} /></div>
            {run.simple_model && <div><span className="text-gray-500">Model:</span> {run.simple_model}</div>}
            <div><span className="text-gray-500">Positions:</span> {run.completed_positions} / {run.positions_count ?? "?"}</div>
          </div>

          {run.error_message && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              <strong>Error:</strong> {run.error_message}
            </div>
          )}

          {/* Score grid */}
          {run.integrated_score != null && (
            <div>
              <p className="text-sm font-medium text-gray-700 mb-2">Scores (1–10 scale)</p>
              <div className="grid grid-cols-2 gap-2">
                {scoreKeys.map((key) => {
                  const val = (run as unknown as Record<string, number | null>)[key];
                  const isIntegrated = key === "integrated_score";
                  return (
                    <div
                      key={key}
                      className={`flex items-center justify-between rounded px-2 py-1.5 ${isIntegrated ? "bg-indigo-50 col-span-2" : "bg-gray-50"}`}
                    >
                      <span className="text-xs text-gray-600">{_SCORE_LABELS[key]}</span>
                      <span className={`text-sm font-medium ${val != null && val >= 7 ? "text-green-700" : val != null && val >= 5 ? "text-amber-700" : "text-red-700"}`}>
                        {val != null ? val.toFixed(2) : "—"}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Per-position table */}
          {run.positions.length > 0 && (
            <div>
              <p className="text-sm font-medium text-gray-700 mb-2">Positions ({run.positions.length})</p>
              <div className="overflow-x-auto rounded border border-gray-200">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-2 py-1.5 text-left text-gray-600">Company</th>
                      <th className="px-2 py-1.5 text-left text-gray-600">Role</th>
                      <th className="px-2 py-1.5 text-right text-gray-600">Score</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {run.positions.map((p) => (
                      <tr key={p.id}>
                        <td className="px-2 py-1.5 text-gray-700">{p.company ?? "—"}</td>
                        <td className="px-2 py-1.5 text-gray-600">{p.role_title ?? "—"}</td>
                        <td className="px-2 py-1.5 text-right font-medium">
                          {p.integrated_score != null ? (
                            <span className={p.integrated_score >= 7 ? "text-green-700" : p.integrated_score >= 5 ? "text-amber-700" : "text-red-700"}>
                              {p.integrated_score.toFixed(2)}
                            </span>
                          ) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="flex gap-3 pt-2">
            {run.status === "completed" && (
              <Button
                onClick={() => onDownload(run.id)}
                loading={downloading === run.id}
                variant="secondary"
              >
                <Download className="h-4 w-4" />
                Download ZIP
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>Close</Button>
          </div>
        </div>
      </div>
    </div>
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
  disabled?: boolean;
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

function GuestNumField({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <input
        type="number"
        min={min}
        value={value}
        onChange={(e) => onChange(Math.max(min, parseInt(e.target.value) || 0))}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-400"
      />
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
        <p className="text-2xl font-bold text-gray-900 tabular-nums">{value.toLocaleString()}</p>
        <p className="text-xs text-gray-500 mt-0.5">{label}</p>
      </CardBody>
    </Card>
  );
}
