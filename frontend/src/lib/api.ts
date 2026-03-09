// frontend/src/lib/api.ts
//
// Centralized API client for the AI Resume Tailor backend.
//
// Auth: Dev-mode uses X-User-Id header (UUID stored in localStorage).
// Base URL: NEXT_PUBLIC_API_URL env var (default: http://localhost:8000/api/v1)

import { getStoredUserId } from "./auth";
import type {
  AuthMeResponse,
  BillingStatus,
  CandidateProfileResponse,
  CandidateProfileUpsertRequest,
  CheckoutSessionRequest,
  CheckoutSessionResponse,
  EvaluationResponse,
  GenerationRequest,
  GenerationResponse,
  GenerationRunDetail,
  GenerationRunSummary,
  JobDescriptionManualRequest,
  JobDescriptionResponse,
  JobDescriptionScrapeRequest,
  JobDescriptionSummary,
  StructuredResumeResponse,
  StructuredResumeSummary,
  SystemStats,
  TailoredDocumentDetail,
} from "@/types/api";

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

// ── Core fetch wrapper ────────────────────────────────────────────────────

class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const userId = getStoredUserId();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };

  if (userId) {
    headers["X-User-Id"] = userId;
  }
  if (!(init.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...init, headers });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────

export const auth = {
  me: () => request<AuthMeResponse>("/auth/me"),
};

// ── Candidate Profile ─────────────────────────────────────────────────────

export const candidateProfile = {
  get: () => request<CandidateProfileResponse>("/candidate-profile"),
  upsert: (body: CandidateProfileUpsertRequest) =>
    request<CandidateProfileResponse>("/candidate-profile", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
};

// ── Resumes ───────────────────────────────────────────────────────────────

export const resumes = {
  list: () => request<StructuredResumeSummary[]>("/resumes"),
  get: (id: string) => request<StructuredResumeResponse>(`/resumes/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<StructuredResumeResponse>("/resumes/upload", {
      method: "POST",
      body: form,
    });
  },
};

// ── Job Descriptions ──────────────────────────────────────────────────────

export const jobDescriptions = {
  list: () => request<JobDescriptionSummary[]>("/job-descriptions"),
  get: (id: string) => request<JobDescriptionResponse>(`/job-descriptions/${id}`),
  scrape: (body: JobDescriptionScrapeRequest) =>
    request<JobDescriptionResponse>("/job-descriptions/scrape", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  manual: (body: JobDescriptionManualRequest) =>
    request<JobDescriptionResponse>("/job-descriptions/manual", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  delete: (id: string) =>
    request<void>(`/job-descriptions/${id}`, { method: "DELETE" }),
};

// ── Generations ───────────────────────────────────────────────────────────

export const generations = {
  create: (body: GenerationRequest) =>
    request<GenerationResponse>("/generations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  list: (limit = 50, offset = 0) =>
    request<GenerationRunSummary[]>(`/generations?limit=${limit}&offset=${offset}`),
  get: (id: string) => request<GenerationRunDetail>(`/generations/${id}`),
};

// ── Documents ─────────────────────────────────────────────────────────────

export const documents = {
  get: (id: string) => request<TailoredDocumentDetail>(`/documents/${id}`),
  download: async (id: string, part: "resume" | "cover_letter"): Promise<Blob> => {
    const userId = getStoredUserId();
    const headers: Record<string, string> = {};
    if (userId) headers["X-User-Id"] = userId;
    const res = await fetch(`${BASE_URL}/documents/${id}/download?part=${part}`, { headers });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const body = await res.json(); detail = body.detail ?? detail; } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return res.blob();
  },
};

// ── Billing ───────────────────────────────────────────────────────────────

export const billing = {
  status: () => request<BillingStatus>("/billing/status"),
  createCheckout: (body: CheckoutSessionRequest) =>
    request<CheckoutSessionResponse>("/billing/create-checkout-session", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ── Admin ─────────────────────────────────────────────────────────────────

export const admin = {
  stats: () => request<SystemStats>("/admin/system-stats"),
  evaluateRun: (generation_run_id: string) =>
    request<EvaluationResponse>("/admin/evaluate-run", {
      method: "POST",
      body: JSON.stringify({ generation_run_id }),
    }),
};

export { ApiError };
