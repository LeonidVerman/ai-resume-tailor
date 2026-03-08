// frontend/src/app/admin/page.tsx
//
// Admin page — shows system stats and allows triggering evaluation runs.
// Only accessible to users with role='admin'.
"use client";

import { useState, useEffect } from "react";
import { ShieldCheck, Users, Zap, FileText, ClipboardCheck } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import { admin, ApiError } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import type { SystemStats, EvaluationResponse } from "@/types/api";
import { formatDateTime } from "@/lib/utils";

export default function AdminPage() {
  const { user, loading: authLoading } = useAuth();
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [loadingStats, setLoadingStats] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);

  const [evalRunId, setEvalRunId] = useState("");
  const [evaluating, setEvaluating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvaluationResponse | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);

  useEffect(() => {
    if (!user || user.role !== "admin") return;
    admin
      .stats()
      .then(setStats)
      .catch((e) => setStatsError(e instanceof ApiError ? e.detail : "Failed to load stats."))
      .finally(() => setLoadingStats(false));
  }, [user]);

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

      {/* Evaluate run */}
      <Card className="max-w-lg">
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
    </AppShell>
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
