// frontend/src/app/onboarding/page.tsx
//
// Wizard-style onboarding: Resume → Profile → (Go generate)
"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle, FileText, User, Zap } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader, CardFooter } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ResumeUpload } from "@/components/resume/ResumeUpload";
import { ProfileForm } from "@/components/candidate-profile/ProfileForm";
import { resumes, candidateProfile } from "@/lib/api";
import type { CandidateProfileResponse, StructuredResumeResponse } from "@/types/api";
import { cn } from "@/lib/utils";

type Step = 0 | 1 | 2;

const steps = [
  { label: "Upload resume", icon: FileText },
  { label: "Candidate profile", icon: User },
  { label: "You're ready!", icon: Zap },
];

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>(0);
  const [uploadedResume, setUploadedResume] = useState<StructuredResumeResponse | null>(null);
  const [savedProfile, setSavedProfile] = useState<CandidateProfileResponse | null>(null);
  const [existingProfile, setExistingProfile] = useState<CandidateProfileResponse | null>(null);
  const [hasResume, setHasResume] = useState(false);

  useEffect(() => {
    // Pre-check what's already done
    resumes.list().then((r) => setHasResume(r.length > 0)).catch(() => {});
    candidateProfile.get().then(setExistingProfile).catch(() => {});
  }, []);

  function onResumeUploaded(r: StructuredResumeResponse) {
    setUploadedResume(r);
    setHasResume(true);
  }

  return (
    <AppShell>
      <div className="max-w-xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-gray-900">Get started</h1>
          <p className="text-gray-500 mt-1">Set up your account to generate tailored documents.</p>
        </div>

        {/* Step indicators */}
        <div className="flex items-center mb-8">
          {steps.map((s, i) => {
            const done = i < step;
            const active = i === step;
            return (
              <div key={i} className="flex items-center flex-1 last:flex-none">
                <div className="flex flex-col items-center">
                  <div
                    className={cn(
                      "h-9 w-9 rounded-full flex items-center justify-center border-2 transition-all",
                      done
                        ? "border-indigo-600 bg-indigo-600 text-white"
                        : active
                        ? "border-indigo-600 bg-white text-indigo-600"
                        : "border-gray-200 bg-gray-50 text-gray-400"
                    )}
                  >
                    {done ? (
                      <CheckCircle className="h-5 w-5" />
                    ) : (
                      <s.icon className="h-4 w-4" />
                    )}
                  </div>
                  <span
                    className={cn(
                      "text-xs mt-1.5 font-medium",
                      active ? "text-indigo-600" : "text-gray-400"
                    )}
                  >
                    {s.label}
                  </span>
                </div>
                {i < steps.length - 1 && (
                  <div
                    className={cn(
                      "flex-1 h-0.5 mx-2 mb-5",
                      i < step ? "bg-indigo-600" : "bg-gray-200"
                    )}
                  />
                )}
              </div>
            );
          })}
        </div>

        {/* Step content */}
        {step === 0 && (
          <Card>
            <CardHeader>
              <h2 className="font-semibold text-gray-900">Upload your master resume</h2>
              <p className="text-sm text-gray-500 mt-0.5">
                Upload your full resume (DOCX, PDF, or TXT). We&apos;ll parse it as the base for all tailored versions.
              </p>
            </CardHeader>
            <CardBody>
              {hasResume && !uploadedResume && (
                <div className="mb-4 p-3 bg-green-50 border border-green-100 rounded-lg text-sm text-green-700 flex items-center gap-2">
                  <CheckCircle className="h-4 w-4" />
                  You already have a resume uploaded. You can upload a new one or continue.
                </div>
              )}
              <ResumeUpload onUpload={onResumeUploaded} />
            </CardBody>
            <CardFooter className="flex justify-between">
              <span />
              <Button
                onClick={() => setStep(1)}
                disabled={!hasResume && !uploadedResume}
              >
                Next step →
              </Button>
            </CardFooter>
          </Card>
        )}

        {step === 1 && (
          <Card>
            <CardHeader>
              <h2 className="font-semibold text-gray-900">Candidate profile</h2>
              <p className="text-sm text-gray-500 mt-0.5">
                Tell us about your background. This helps the AI understand your strengths and tailor your documents precisely.
              </p>
            </CardHeader>
            <CardBody>
              <ProfileForm
                initial={existingProfile}
                onSaved={(p) => setSavedProfile(p)}
              />
            </CardBody>
            <CardFooter className="flex justify-between">
              <Button variant="ghost" onClick={() => setStep(0)}>← Back</Button>
              <Button onClick={() => setStep(2)} disabled={!savedProfile && !existingProfile}>
                Next step →
              </Button>
            </CardFooter>
          </Card>
        )}

        {step === 2 && (
          <Card>
            <CardBody className="py-10 text-center space-y-4">
              <div className="inline-flex h-16 w-16 rounded-full bg-green-100 items-center justify-center mx-auto">
                <CheckCircle className="h-8 w-8 text-green-600" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-gray-900">You&apos;re all set!</h2>
                <p className="text-gray-500 mt-2">
                  Your resume and profile are ready. Now add a job description and generate your first tailored application.
                </p>
              </div>
              <div className="flex gap-3 justify-center pt-2">
                <Button variant="secondary" onClick={() => router.push("/dashboard")}>
                  Go to dashboard
                </Button>
                <Button onClick={() => router.push("/generate")}>
                  <Zap className="h-4 w-4" />
                  Generate now
                </Button>
              </div>
            </CardBody>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
