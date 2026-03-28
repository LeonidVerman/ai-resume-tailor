// frontend/src/lib/api.ts
//
// Centralized API client for the AI Resume Tailor backend.
//
// Auth: sends Authorization: Bearer <token> when a token is stored.
//       Falls back to X-User-Id header when only a dev-bypass UUID is stored.
// Base URL: NEXT_PUBLIC_API_URL env var (default: http://localhost:8000/api/v1)

import { getStoredToken, getStoredUserId } from "./auth";
import type {
  AdminActionResponse,
  AuthLoginResponse,
  AuthMeResponse,
  AuthStatusResponse,
  LegalAcceptRequest,
  LegalCurrentResponse,
  LegalStatusResponse,
  BenchmarkRunDetail,
  BenchmarkRunSummary,
  BillingStatus,
  CandidateProfileResponse,
  CandidateProfileUpsertRequest,
  CheckoutSessionRequest,
  CheckoutSessionResponse,
  CustomerPortalResponse,
  EvaluationResponse,
  GenerationConfigRequest,
  GenerationConfigResponse,
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
  const token = getStoredToken();
  const userId = getStoredUserId(); // dev-bypass fallback
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  } else if (userId) {
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
  register: (body: { email: string; password: string }) =>
    request<AuthLoginResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  login: (body: { email: string; password: string }) =>
    request<AuthLoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<AuthMeResponse>("/auth/me"),
  status: () => request<AuthStatusResponse>("/auth/status"),
  forgotPassword: (body: { email: string }) =>
    request<void>("/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  resetPassword: (body: { access_token: string; new_password: string }) =>
    request<void>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ── Candidate Profile ─────────────────────────────────────────────────────

export const candidateProfile = {
  get: () => request<CandidateProfileResponse>("/candidate-profile"),
  upsert: (body: CandidateProfileUpsertRequest) =>
    request<CandidateProfileResponse>("/candidate-profile", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  completeOnboarding: () =>
    request<CandidateProfileResponse>("/candidate-profile/complete-onboarding", {
      method: "POST",
    }),
};

// ── Resumes ───────────────────────────────────────────────────────────────

export const resumes = {
  list: () => request<StructuredResumeSummary[]>("/resumes"),
  get: (id: number) => request<StructuredResumeResponse>(`/resumes/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<StructuredResumeResponse>("/resumes/upload", {
      method: "POST",
      body: form,
    });
  },
  delete: (id: number) => request<void>(`/resumes/${id}`, { method: "DELETE" }),
};

// ── Job Descriptions ──────────────────────────────────────────────────────

export const jobDescriptions = {
  list: () => request<JobDescriptionSummary[]>("/job-descriptions"),
  get: (id: number) => request<JobDescriptionResponse>(`/job-descriptions/${id}`),
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
  delete: (id: number) =>
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
  get: (id: number) => request<GenerationRunDetail>(`/generations/${id}`),
  delete: (id: number) => request<void>(`/generations/${id}`, { method: "DELETE" }),
};

// ── Documents ─────────────────────────────────────────────────────────────

export const documents = {
  get: (id: number) => request<TailoredDocumentDetail>(`/documents/${id}`),
  download: async (id: number, part: "resume" | "cover_letter"): Promise<Blob> => {
    const token = getStoredToken();
    const userId = getStoredUserId();
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    else if (userId) headers["X-User-Id"] = userId;
    const res = await fetch(`${BASE_URL}/documents/${id}/download?part=${part}`, { headers });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const body = await res.json(); detail = body.detail ?? detail; } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return res.blob();
  },
  // Download a rendered DOCX or PDF artifact.
  downloadFormatted: async (
    id: number,
    part: "resume" | "cover_letter",
    format: "docx" | "pdf",
  ): Promise<{ blob: Blob; filename: string }> => {
    const token = getStoredToken();
    const userId = getStoredUserId();
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    else if (userId) headers["X-User-Id"] = userId;
    const res = await fetch(
      `${BASE_URL}/documents/${id}/download?part=${part}&format=${format}`,
      { headers },
    );
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const body = await res.json(); detail = body.detail ?? detail; } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    const disposition = res.headers.get("content-disposition") ?? "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : `${part}.${format}`;
    return { blob: await res.blob(), filename };
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
  customerPortal: (return_url?: string) =>
    request<CustomerPortalResponse>("/billing/customer-portal", {
      method: "POST",
      body: JSON.stringify(return_url ? { return_url } : {}),
    }),
};

// ── Admin ─────────────────────────────────────────────────────────────────

/** Fetch a binary endpoint and return the blob + filename from Content-Disposition. */
async function downloadBlob(
  path: string,
  fallbackFilename: string
): Promise<{ blob: Blob; filename: string }> {
  const token = getStoredToken();
  const userId = getStoredUserId();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  else if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(`${BASE_URL}${path}`, { headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : fallbackFilename;
  return { blob: await res.blob(), filename };
}

export const admin = {
  stats: () => request<SystemStats>("/admin/system-stats"),
  evaluateRun: (generation_run_id: number) =>
    request<EvaluationResponse>("/admin/evaluate-run", {
      method: "POST",
      body: JSON.stringify({ generation_run_id }),
    }),
  getGenerationConfig: () =>
    request<GenerationConfigResponse>("/admin/generation-config"),
  saveGenerationConfig: (body: GenerationConfigRequest) =>
    request<AdminActionResponse>("/admin/generation-config", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  downloadLogs: (fromDate: string, toDate?: string) => {
    const params = new URLSearchParams({ from_date: fromDate });
    if (toDate) params.set("to_date", toDate);
    return downloadBlob(`/admin/logs/download?${params}`, "logs.zip");
  },
  downloadRunData: (fromDate: string, toDate?: string) => {
    const params = new URLSearchParams({ from_date: fromDate });
    if (toDate) params.set("to_date", toDate);
    return downloadBlob(`/admin/run-data/download?${params}`, "run-data.zip");
  },
  downloadRunDataById: (runId: string) =>
    downloadBlob(`/admin/run-data/download/${runId}`, `run-data-${runId}.json`),

  // Benchmark
  startBenchmark: (client_id: string, assess_model: string) =>
    request<BenchmarkRunSummary>("/admin/benchmark-runs", {
      method: "POST",
      body: JSON.stringify({ client_id, assess_model }),
    }),
  listBenchmarkRuns: (limit = 20) =>
    request<BenchmarkRunSummary[]>(`/admin/benchmark-runs?limit=${limit}`),
  getBenchmarkRun: (id: number) =>
    request<BenchmarkRunDetail>(`/admin/benchmark-runs/${id}`),
  downloadBenchmarkZip: (id: number) =>
    downloadBlob(`/admin/benchmark-runs/${id}/download`, `benchmark-${id}.zip`),
};

// ── Legal ──────────────────────────────────────────────────────────────────

export const legal = {
  current: () => request<LegalCurrentResponse>("/legal/current"),
  status: () => request<LegalStatusResponse>("/legal/status"),
  accept: (body: LegalAcceptRequest) =>
    request<void>("/legal/accept", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  documentContent: async (docType: string): Promise<string> => {
    const res = await fetch(`${BASE_URL}/legal/document/${docType}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.text();
  },
};

export { ApiError };
