// frontend/src/lib/api.ts
//
// Centralized API client for the CVRocket backend.
//
// Auth: sends Authorization: Bearer <token> when a token is stored.
//       Falls back to X-User-Id header when only a dev-bypass UUID is stored.
// Base URL: NEXT_PUBLIC_API_URL env var (default: http://localhost:8000/api/v1)

import {
  clearStoredToken,
  getStoredRefreshToken,
  getStoredToken,
  getStoredUserId,
  setStoredRefreshToken,
  setStoredToken,
} from "./auth";
import type {
  AdminActionResponse,
  AuthLoginResponse,
  AuthMeResponse,
  AuthStatusResponse,
  LegalAcceptRequest,
  LegalCurrentResponse,
  LegalStatusResponse,
  AutofillDraftResponse,
  AutofillGenerateRequest,
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
  SignupCreditPolicyRequest,
  SignupCreditPolicyResponse,
  GenerationRequest,
  GenerationResponse,
  GuestClaimRequest,
  GuestClaimResponse,
  GuestDataDeleteResponse,
  GuestMetricsResponse,
  GuestSessionRequest,
  GuestSessionResponse,
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
  UpgradePlanRequest,
  UpgradePlanResponse,
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

let _refreshPromise: Promise<string | null> | null = null;

async function _tryRefreshToken(): Promise<string | null> {
  // Deduplicate concurrent refresh attempts
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    const refreshToken = getStoredRefreshToken();
    if (!refreshToken) return null;
    try {
      const res = await fetch(`${BASE_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
      if (!res.ok) {
        clearStoredToken();
        return null;
      }
      const data = await res.json();
      const newAccessToken: string = data.session?.access_token;
      const newRefreshToken: string = data.session?.refresh_token;
      if (!newAccessToken) {
        clearStoredToken();
        return null;
      }
      setStoredToken(newAccessToken);
      if (newRefreshToken) setStoredRefreshToken(newRefreshToken);
      return newAccessToken;
    } catch {
      clearStoredToken();
      return null;
    } finally {
      _refreshPromise = null;
    }
  })();
  return _refreshPromise;
}

async function _rawFetch(
  path: string,
  init: RequestInit,
  token: string | null,
  userId: string | null
): Promise<Response> {
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
  return fetch(`${BASE_URL}${path}`, { ...init, headers });
}

async function request<T>(
  path: string,
  init: RequestInit = {}
): Promise<T> {
  const userId = getStoredUserId(); // dev-bypass fallback
  let token = getStoredToken();

  let res = await _rawFetch(path, init, token, userId);

  // On 401 with a stored refresh token, attempt one silent refresh + retry
  if (res.status === 401 && !userId) {
    const newToken = await _tryRefreshToken();
    if (newToken) {
      res = await _rawFetch(path, init, newToken, null);
    } else {
      // Refresh failed — session is unrecoverable. Redirect to login so the
      // user can re-authenticate rather than seeing a raw "Invalid or expired
      // token" error and having to reload manually.
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = "/login?reason=session_expired";
      }
    }
  }

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
  refresh: (body: { refresh_token: string }) =>
    request<AuthLoginResponse>("/auth/refresh", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  me: () => request<AuthMeResponse>("/auth/me"),
  status: () => request<AuthStatusResponse>("/auth/status"),
  forgotPassword: (body: { email: string; redirect_to?: string }) =>
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
  autofill: {
    listResumes: () =>
      request<StructuredResumeSummary[]>("/candidate-profile/autofill/resumes"),
    getDraft: (resumeId: number) =>
      request<AutofillDraftResponse>(
        `/candidate-profile/autofill/draft?resume_id=${resumeId}`
      ),
    generate: (body: AutofillGenerateRequest) =>
      request<AutofillDraftResponse>("/candidate-profile/autofill/generate", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    selectSource: (resumeId: number) =>
      request<{ source_resume_id: number | null }>(
        "/candidate-profile/autofill/select-source",
        {
          method: "POST",
          body: JSON.stringify({ resume_id: resumeId }),
        }
      ),
  },
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
    const res = await _authFetch(`${BASE_URL}/documents/${id}/download?part=${part}`);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const body = await res.json(); detail = body.detail ?? detail; } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    return res.blob();
  },
  // Download a rendered DOCX, PDF, or plain-text artifact.
  downloadFormatted: async (
    id: number,
    part: "resume" | "cover_letter",
    format: "docx" | "pdf" | "txt",
  ): Promise<{ blob: Blob; filename: string }> => {
    const res = await _authFetch(
      `${BASE_URL}/documents/${id}/download?part=${part}&format=${format}`,
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
  upgradePlan: (body: UpgradePlanRequest) =>
    request<UpgradePlanResponse>("/billing/upgrade-plan", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  customerPortal: (return_url?: string) =>
    request<CustomerPortalResponse>("/billing/customer-portal", {
      method: "POST",
      body: JSON.stringify(return_url ? { return_url } : {}),
    }),
};

// ── Authenticated fetch helper (used by binary download functions) ────────

/**
 * Raw fetch with Authorization header + the same 401-silent-refresh logic
 * as request(). Used by binary download helpers that can't go through the
 * JSON-only request() wrapper.
 */
async function _authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const userId = getStoredUserId();
  let token = getStoredToken();

  const doFetch = (t: string | null) => {
    const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
    if (t) headers["Authorization"] = `Bearer ${t}`;
    else if (userId) headers["X-User-Id"] = userId;
    return fetch(url, { ...init, headers });
  };

  let res = await doFetch(token);

  if (res.status === 401 && !userId) {
    const newToken = await _tryRefreshToken();
    if (newToken) {
      res = await doFetch(newToken);
    } else {
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = "/login?reason=session_expired";
      }
    }
  }

  return res;
}

// ── Admin ─────────────────────────────────────────────────────────────────

/** Fetch a binary endpoint and return the blob + filename from Content-Disposition. */
async function downloadBlob(
  path: string,
  fallbackFilename: string
): Promise<{ blob: Blob; filename: string }> {
  const res = await _authFetch(`${BASE_URL}${path}`);
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
  getGuestMetrics: () =>
    request<GuestMetricsResponse>("/admin/guest-metrics"),
  getSignupCreditPolicy: () =>
    request<SignupCreditPolicyResponse>("/admin/signup-credit-policy"),
  saveSignupCreditPolicy: (body: SignupCreditPolicyRequest) =>
    request<AdminActionResponse>("/admin/signup-credit-policy", {
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
    downloadBlob(`/admin/run-data/download/${runId}`, `run-data-${runId}.zip`),

  // Benchmark
  startBenchmark: (client_id: string, assess_model: string, generation_mode?: string) =>
    request<BenchmarkRunSummary>("/admin/benchmark-runs", {
      method: "POST",
      body: JSON.stringify({ client_id, assess_model, generation_mode }),
    }),
  listBenchmarkRuns: (limit = 20) =>
    request<BenchmarkRunSummary[]>(`/admin/benchmark-runs?limit=${limit}`),
  getBenchmarkRun: (id: number) =>
    request<BenchmarkRunDetail>(`/admin/benchmark-runs/${id}`),
  downloadBenchmarkZip: (id: number) =>
    downloadBlob(`/admin/benchmark-runs/${id}/download`, `benchmark-${id}.zip`),

  grantCredits: (user_id: string, amount: number) =>
    request<{ user_id: string; credits_adjusted: number }>("/admin/billing/grant-credits", {
      method: "POST",
      body: JSON.stringify({ user_id, amount }),
    }),

  classifyFile: async (
    file: File
  ): Promise<{
    classificationBlob: Blob; classificationFilename: string;
    inputBlob: Blob; inputFilename: string;
  }> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await _authFetch(`${BASE_URL}/admin/classification/classify-file`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
      } catch { /* ignore */ }
      throw new ApiError(res.status, detail);
    }
    const json = await res.json();
    const basename = file.name.replace(/\.[^.]+$/, "");
    return {
      classificationBlob: new Blob([JSON.stringify(json.classification, null, 2)], { type: "application/json" }),
      classificationFilename: `${basename}.json`,
      inputBlob: new Blob([JSON.stringify(json.llm_input, null, 2)], { type: "application/json" }),
      inputFilename: `${basename}_input.json`,
    };
  },
};

// ── Guest (public /try flow, issue #155) ──────────────────────────────────

export const guest = {
  /**
   * Create an anonymous guest session (public endpoint, no auth header).
   * Uses credentials: "include" so the backend can read/set the httpOnly
   * guest device cookie across origins. The caller stores the returned
   * tokens in the standard art_access_token / art_refresh_token slots so
   * every subsequent call flows through the normal Authorization header.
   */
  createSession: async (body: GuestSessionRequest): Promise<GuestSessionResponse> => {
    const res = await fetch(`${BASE_URL}/guest/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const data = await res.json();
        detail = data.detail ?? detail;
      } catch {
        // ignore parse errors
      }
      throw new ApiError(res.status, detail);
    }
    return res.json() as Promise<GuestSessionResponse>;
  },

  /**
   * Claim/merge a guest session into the authenticated account (Phase 4).
   * Sent with the NEW registered user's Authorization header; the guest
   * access token in the body is the ownership proof.
   */
  claim: (body: GuestClaimRequest) =>
    request<GuestClaimResponse>("/guest/claim", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /**
   * "Delete my files now" (Phase 5): immediately purge the authenticated
   * guest's uploaded resume and generated artifacts. Guest accounts only.
   */
  deleteData: () =>
    request<GuestDataDeleteResponse>("/guest/data", { method: "DELETE" }),
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
