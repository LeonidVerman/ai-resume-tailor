// frontend/src/app/help/page.tsx
"use client";

import Link from "next/link";
import {
  HelpCircle,
  UploadCloud,
  Briefcase,
  User,
  Sliders,
  Download,
  CheckCircle2,
  Zap,
  Mail,
} from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

// ── Quick-start steps ──────────────────────────────────────────────────────

const STEPS = [
  {
    n: 1,
    icon: UploadCloud,
    title: "Upload your resume",
    body: "Go to Resumes and upload your base resume in DOCX or PDF format. This is the template CVRocket tailors.",
  },
  {
    n: 2,
    icon: Briefcase,
    title: "Add a job description",
    body: "Paste a job posting URL to fetch it automatically, or paste the text directly if scraping is unavailable.",
  },
  {
    n: 3,
    icon: User,
    title: "Complete your profile",
    body: "Add your domain experience, strengths, and background. CVRocket uses profile data in addition to your resume — a complete profile improves generation quality significantly.",
  },
  {
    n: 4,
    icon: Sliders,
    title: "Choose a generation mode",
    body: "Pick Conservative for minimal edits, Normal for balanced tailoring, or Aggressive for stronger alignment to the job description.",
  },
  {
    n: 5,
    icon: Download,
    title: "Generate, review, and download",
    body: "Generate a tailored resume and cover letter together, review what changed, then download as DOCX or PDF.",
  },
];

// ── Differentiators ────────────────────────────────────────────────────────

const DIFF = [
  {
    title: "Built for software engineers",
    body: "Tailoring logic is optimized for technical roles and engineering resumes.",
  },
  {
    title: "Uses full profile data",
    body: "Goes beyond your resume — uses your structured profile to tailor based on real background details.",
  },
  {
    title: "Preserves layout",
    body: "Generated content is applied into your existing resume structure, not a blank template.",
  },
  {
    title: "Three generation modes",
    body: "Choose how aggressively the content is tailored to the role.",
  },
  {
    title: "Resume + cover letter together",
    body: "Both documents are generated in one flow, saving time per application.",
  },
  {
    title: "URL or paste workflow",
    body: "Fetch job postings from most job boards, with copy/paste fallback when scraping is blocked.",
  },
  {
    title: "Review changes",
    body: "Inspect exactly what changed between your base and tailored resume before downloading.",
  },
  {
    title: "DOCX and PDF output",
    body: "Download in the format each employer prefers.",
  },
];

// ── Generation modes ───────────────────────────────────────────────────────

const MODES = [
  {
    label: "Conservative",
    color: "border-blue-200 bg-blue-50",
    badge: "bg-blue-100 text-blue-700",
    body: "Minimal rewriting. Stronger fidelity to your original resume. Best when you need small targeted edits.",
  },
  {
    label: "Normal",
    color: "border-indigo-200 bg-indigo-50",
    badge: "bg-indigo-100 text-indigo-700",
    recommended: true,
    body: "Balanced tailoring. Good default choice for most applications. Recommended if you are unsure.",
  },
  {
    label: "Aggressive",
    color: "border-violet-200 bg-violet-50",
    badge: "bg-violet-100 text-violet-700",
    body: "Stronger alignment to the job description. Larger content changes. Best when you want maximum fit.",
  },
];

// ── FAQ items ──────────────────────────────────────────────────────────────

const FAQ = [
  {
    q: "What if job scraping doesn't work?",
    a: "Paste the job description text manually using the text input option on the Generate page.",
  },
  {
    q: "Why does profile data matter?",
    a: "CVRocket uses your profile to tailor content using real experience details that may not be fully captured in the resume.",
  },
  {
    q: "Will my resume format change?",
    a: "CVRocket is designed to preserve your resume structure and layout. Changes are applied to content, not to formatting.",
  },
  {
    q: "What should I review before submitting?",
    a: "Always review the tailored resume and cover letter for accuracy, tone, and job fit before sending.",
  },
];

// ── Page ───────────────────────────────────────────────────────────────────

export default function HelpPage() {
  return (
    <AppShell>
      {/* Page header */}
      <div className="mb-8">
        <h1 className="text-xl font-semibold tracking-tight text-gray-900 flex items-center gap-2">
          <HelpCircle className="h-5 w-5 text-indigo-500" />
          Help &amp; Quick Start
        </h1>
        <p className="text-gray-500 mt-1 text-sm">
          Learn how to create your first tailored resume and cover letter.
          Built for software engineers — designed to preserve your resume structure while tailoring
          content to each role.
        </p>
      </div>

      <div className="space-y-10">

        {/* ── Quick start ─────────────────────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Get started in 2 minutes
          </h2>
          <div className="space-y-3">
            {STEPS.map(({ n, icon: Icon, title, body }) => (
              <div key={n} className="flex gap-4">
                <div className="flex-shrink-0 flex items-center justify-center h-8 w-8 rounded-full bg-indigo-100 text-indigo-600 text-sm font-semibold mt-0.5">
                  {n}
                </div>
                <Card className="flex-1">
                  <CardBody className="py-3">
                    <div className="flex items-center gap-2 mb-0.5">
                      <Icon className="h-4 w-4 text-indigo-400 shrink-0" />
                      <p className="text-sm font-semibold text-gray-900">{title}</p>
                    </div>
                    <p className="text-sm text-gray-500 pl-6">{body}</p>
                  </CardBody>
                </Card>
              </div>
            ))}
          </div>

          {/* Callout */}
          <div className="mt-4 flex items-start gap-2 bg-indigo-50 border border-indigo-100 rounded-lg px-4 py-3">
            <CheckCircle2 className="h-4 w-4 text-indigo-400 mt-0.5 shrink-0" />
            <p className="text-sm text-indigo-700">
              Best results come from combining a strong resume, a complete profile, and a clear job description.
            </p>
          </div>

          <div className="mt-5">
            <Link href="/generate">
              <Button className="flex items-center gap-2">
                <Zap className="h-4 w-4" />
                Go to Generate
              </Button>
            </Link>
          </div>
        </section>

        {/* ── Why CVRocket ─────────────────────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Why CVRocket is different
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {DIFF.map(({ title, body }) => (
              <Card key={title}>
                <CardBody className="py-3">
                  <p className="text-sm font-semibold text-gray-900 mb-0.5">{title}</p>
                  <p className="text-sm text-gray-500">{body}</p>
                </CardBody>
              </Card>
            ))}
          </div>
        </section>

        {/* ── Generation modes ─────────────────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Choosing the right mode
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {MODES.map(({ label, color, badge, recommended, body }) => (
              <div
                key={label}
                className={`rounded-xl border px-5 py-4 ${color}`}
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${badge}`}>
                    {label}
                  </span>
                  {recommended && (
                    <span className="text-xs text-gray-400 font-medium">recommended</span>
                  )}
                </div>
                <p className="text-sm text-gray-600">{body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ── Tips ─────────────────────────────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Tips for best results
          </h2>
          <Card>
            <CardBody>
              <ul className="space-y-2">
                {[
                  "Keep your profile complete and up to date.",
                  "Start with a strong, well-structured base resume.",
                  "Prefer clean, complete job descriptions.",
                  "Review the generated output before submitting.",
                  "Use Conservative mode when you need smaller, safer edits.",
                  "Use Aggressive mode when you want stronger alignment to the job description.",
                ].map((tip) => (
                  <li key={tip} className="flex items-start gap-2 text-sm text-gray-600">
                    <CheckCircle2 className="h-4 w-4 text-emerald-500 mt-0.5 shrink-0" />
                    {tip}
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        </section>

        {/* ── FAQ ──────────────────────────────────────────────────────── */}
        <section>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-4">
            Common questions
          </h2>
          <div className="space-y-3">
            {FAQ.map(({ q, a }) => (
              <Card key={q}>
                <CardBody className="py-3">
                  <p className="text-sm font-semibold text-gray-900 mb-1">{q}</p>
                  <p className="text-sm text-gray-500">{a}</p>
                </CardBody>
              </Card>
            ))}
          </div>
        </section>

        {/* ── Support ──────────────────────────────────────────────────── */}
        <section>
          <Card>
            <CardBody>
              <div className="flex items-start gap-3">
                <Mail className="h-5 w-5 text-indigo-400 mt-0.5 shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-gray-900 mb-1">Need help?</p>
                  <p className="text-sm text-gray-500">
                    Email us at{" "}
                    <a
                      href="mailto:support@cvrocket.io"
                      className="text-indigo-600 hover:underline font-medium"
                    >
                      support@cvrocket.io
                    </a>{" "}
                    for questions, bug reports, or feedback.
                  </p>
                </div>
              </div>
            </CardBody>
          </Card>
        </section>

      </div>
    </AppShell>
  );
}
