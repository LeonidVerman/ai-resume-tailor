// frontend/src/app/dashboard/page.tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FileText, Briefcase, Zap, Clock, ArrowRight, User } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { useAuth } from "@/hooks/useAuth";
import {
  resumes,
  jobDescriptions,
  generations,
  candidateProfile,
  billing,
} from "@/lib/api";
import type {
  StructuredResumeSummary,
  JobDescriptionSummary,
  GenerationRunSummary,
  BillingStatus,
} from "@/types/api";
import { formatDate } from "@/lib/utils";

const statusVariant = (s: string) => {
  if (s === "succeeded") return "success";
  if (s === "failed") return "danger";
  if (s === "running") return "info";
  return "default";
};

export default function DashboardPage() {
  const { user } = useAuth();
  const [resumeList, setResumeList] = useState<StructuredResumeSummary[]>([]);
  const [jdList, setJdList] = useState<JobDescriptionSummary[]>([]);
  const [runList, setRunList] = useState<GenerationRunSummary[]>([]);
  const [hasProfile, setHasProfile] = useState<boolean | null>(null);
  const [billingStatus, setBillingStatus] = useState<BillingStatus | null>(null);

  useEffect(() => {
    resumes.list().then(setResumeList).catch(() => []);
    jobDescriptions.list().then(setJdList).catch(() => []);
    generations.list(500).then(setRunList).catch(() => []);
    billing.status().then(setBillingStatus).catch(() => null);
    candidateProfile.get().then(() => setHasProfile(true)).catch(() => setHasProfile(false));
  }, []);

  const onboardingComplete =
    resumeList.length > 0 && jdList.length > 0 && hasProfile;

  return (
    <AppShell>
      {/* Page header */}
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900">
          Welcome back{user?.email ? `, ${user.email.split("@")[0]}` : ""}
        </h1>
        <p className="text-gray-500 mt-1">
          Tailor your resume and cover letter for each software engineering role.
        </p>
      </div>

      {/* Onboarding banner */}
      {!onboardingComplete && hasProfile !== null && (
        <Card className="mb-6 border-indigo-200 bg-indigo-50">
          <CardBody className="flex items-center justify-between py-4">
            <div>
              <p className="font-medium text-indigo-900">Finish setting up your account</p>
              <p className="text-sm text-indigo-700 mt-0.5">
                Complete onboarding to generate your first tailored resume.
              </p>
            </div>
            <Link href="/onboarding">
              <Button variant="primary" size="sm">
                Continue setup <ArrowRight className="h-4 w-4" />
              </Button>
            </Link>
          </CardBody>
        </Card>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4 mb-8">
        <StatCard
          icon={<FileText className="h-5 w-5 text-indigo-600" />}
          label="Resumes"
          value={resumeList.length}
          bg="bg-indigo-50"
        />
        <StatCard
          icon={<Briefcase className="h-5 w-5 text-amber-600" />}
          label="Job descriptions"
          value={jdList.length}
          bg="bg-amber-50"
        />
        <StatCard
          icon={<Zap className="h-5 w-5 text-green-600" />}
          label="Generations"
          value={runList.length}
          bg="bg-green-50"
        />
        <StatCard
          icon={<User className="h-5 w-5 text-purple-600" />}
          label="Profile"
          value={hasProfile ? "Complete" : "Pending"}
          bg="bg-purple-50"
        />
      </div>

      {/* Quota */}
      {billingStatus && billingStatus.plan_type === "free" && (
        <Card className="mb-6">
          <CardBody className="py-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="flex-1">
                  {billingStatus.monthly_limit > 0 ? (
                    <>
                      <p className="text-sm font-medium text-gray-700">Free plan quota</p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        {billingStatus.monthly_used} / {billingStatus.monthly_limit} monthly generations used
                        {billingStatus.extra_credits > 0 && (
                          <span className="ml-2 text-emerald-600 font-medium">
                            · {billingStatus.extra_credits} bonus credit{billingStatus.extra_credits !== 1 ? "s" : ""} available
                          </span>
                        )}
                      </p>
                    </>
                  ) : (
                    <>
                      <p className="text-sm font-medium text-gray-700">Generation credits</p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        {billingStatus.extra_credits > 0
                          ? <span className="text-emerald-600 font-medium">{billingStatus.extra_credits} credit{billingStatus.extra_credits !== 1 ? "s" : ""} remaining</span>
                          : "No credits remaining — purchase a pack to continue"}
                      </p>
                    </>
                  )}
                </div>
                {billingStatus.monthly_limit > 0 && (
                  <div className="w-32 h-2 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-indigo-500 rounded-full"
                      style={{
                        width: `${Math.min(100, (billingStatus.monthly_used / billingStatus.monthly_limit) * 100)}%`,
                      }}
                    />
                  </div>
                )}
              </div>
              <Link href="/billing">
                <Button variant="ghost" size="sm">Upgrade</Button>
              </Link>
            </div>
          </CardBody>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-6">
        {/* Quick actions — primary card, stronger emphasis */}
        <Card className="shadow-md border-gray-200">
          <CardHeader>
            <h2 className="font-semibold text-gray-900">Quick actions</h2>
          </CardHeader>
          <CardBody className="space-y-1 py-3">
            <ActionRow href="/generate" icon={<Zap className="h-4 w-4 text-indigo-600" />} label="Generate tailored application materials" highlight />
            <ActionRow href="/resumes" icon={<FileText className="h-4 w-4 text-gray-500" />} label="Upload a resume" />
            <ActionRow href="/jobs" icon={<Briefcase className="h-4 w-4 text-gray-500" />} label="Add a job description" />
            <ActionRow href="/onboarding" icon={<User className="h-4 w-4 text-gray-500" />} label="Update candidate profile" />
          </CardBody>
        </Card>

        {/* Recent runs — secondary card */}
        <Card>
          <CardHeader className="flex items-center justify-between">
            <h2 className="font-semibold text-gray-900">Recent generations</h2>
            <Link href="/history" className="text-xs text-indigo-600 hover:text-indigo-700">
              View all →
            </Link>
          </CardHeader>
          <CardBody className="py-2">
            {runList.length === 0 ? (
              <p className="text-sm text-gray-500 py-2">No generations yet.</p>
            ) : (
              <ul className="divide-y divide-gray-50">
                {runList.slice(0, 5).map((run) => (  // display only 5 most recent
                  <li key={run.id} className="flex items-center justify-between py-2.5">
                    <div>
                      <p className="text-sm text-gray-800 font-medium">
                        {run.company_name && run.role_title
                          ? `${run.company_name} — ${run.role_title}`
                          : run.company_name ?? run.role_title ?? run.model_name}
                      </p>
                      <p className="text-xs text-gray-500 flex items-center gap-1 mt-0.5">
                        <Clock className="h-3 w-3" />
                        {formatDate(run.started_at)}
                      </p>
                    </div>
                    <Badge variant={statusVariant(run.status) as "success" | "danger" | "info" | "default"}>
                      {run.status}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
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
  value: number | string;
  bg: string;
}) {
  return (
    <Card>
      <CardBody className="py-4">
        <div className={`inline-flex h-9 w-9 rounded-lg ${bg} items-center justify-center mb-3`}>
          {icon}
        </div>
        <p className="text-2xl font-bold text-gray-900 tabular-nums">{value}</p>
        <p className="text-xs text-gray-500 mt-0.5">{label}</p>
      </CardBody>
    </Card>
  );
}

function ActionRow({
  href,
  icon,
  label,
  highlight,
}: {
  href: string;
  icon: React.ReactNode;
  label: string;
  highlight?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-3 px-2 py-2 rounded-lg transition-colors group ${
        highlight ? "bg-indigo-50 hover:bg-indigo-100" : "hover:bg-gray-50"
      }`}
    >
      <div className={`h-7 w-7 rounded-md flex items-center justify-center shrink-0 ${
        highlight ? "bg-indigo-100" : "bg-gray-100"
      }`}>
        {icon}
      </div>
      <span className={`text-sm font-medium ${
        highlight ? "text-indigo-700 group-hover:text-indigo-800" : "text-gray-600 group-hover:text-gray-900"
      }`}>{label}</span>
      <ArrowRight className={`h-3.5 w-3.5 ml-auto transition-colors ${
        highlight ? "text-indigo-400 group-hover:text-indigo-600" : "text-gray-300 group-hover:text-gray-500"
      }`} />
    </Link>
  );
}
