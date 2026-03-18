// frontend/src/types/api.ts
// Typed contracts mirroring backend Pydantic schemas.

// ── Auth ──────────────────────────────────────────────────────────────────

export interface AuthMeResponse {
  user_id: string;
  email: string;
  role: string;
  is_admin: boolean;
  plan_type: string;
}

export interface AuthStatusResponse {
  auth_mode: string;  // "supabase" | "dev_bypass"
  status: string;
}

export interface AuthSessionResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface AuthUserResponse {
  id: string;
  email: string;
  role: string;
  is_admin: boolean;
  plan_type: string;
}

export interface AuthLoginResponse {
  authenticated: boolean;
  user: AuthUserResponse;
  session: AuthSessionResponse;
}

// ── Candidate Profile ─────────────────────────────────────────────────────

export interface CandidateIdentity {
  name: string;
  headline?: string;
  summary?: string;
}

export interface DomainExperience {
  primary: string[];
  secondary: string[];
}

export interface ExperienceHighlight {
  area: string;
  market?: string;
  employer_relationship?: string;
  impact: string[];
  team_context: string[];
  architecture_patterns: string[];
  constraints_and_tradeoffs: string[];
  skills_applied: string[];
  security_auth_patterns: string[];
}

export interface TechnicalSkills {
  languages: string[];
  backend_systems: string[];
  datastores: string[];
  infra_devops: string[];
  api_patterns: string[];
  async_messaging: string[];
  observability: string[];
  security_auth_patterns: string[];
  scalability_reliability_patterns: string[];
}

export interface ClaimBoundaries {
  security_auth: string[];
  domain_limits: string[];
  employment_constraints: string[];
}

export interface LeadershipScope {
  team_size_max: number | null;
  style_keywords: string[];
}

export interface Leadership {
  scope: LeadershipScope;
  practices: string[];
  risk_management: string[];
}

export interface AIToolingPractice {
  hands_on_tools: string[];
  usage_patterns: string[];
  principles: string[];
  concepts_familiarity: string[];
}

export interface ConstraintsAndPreferences {
  work_context: string[];
  communication: string[];
  resume_constraint: string[];
}

export interface CandidateProfileDocument {
  candidate_profile_version: "1.0" | "1.1" | "2.0";
  candidate: CandidateIdentity;
  domains: DomainExperience;
  experience_highlights: ExperienceHighlight[];
  technical_skills: TechnicalSkills;
  leadership: Leadership;
  ai_tooling_practice: AIToolingPractice;
  role_fit_themes: string[];
  constraints_and_preferences: ConstraintsAndPreferences;
  claim_boundaries: ClaimBoundaries;
}

export interface CandidateProfileResponse {
  id: string;
  user_id: string;
  profile_version: string;
  profile: CandidateProfileDocument;
  created_at: string;
  updated_at: string;
}

export interface CandidateProfileUpsertRequest {
  profile: CandidateProfileDocument;
  profile_version: "1";
}

// ── Structured Resume ─────────────────────────────────────────────────────

export interface ContactInfo {
  email?: string;
  phone?: string;
  location?: string;
  linkedin_url?: string;
  github_url?: string;
  website_url?: string;
}

export interface ExperienceEntry {
  company: string;
  role: string;
  start_date: string;
  end_date: string;
  bullets: string[];
  employment_type?: string;
}

export interface EducationEntry {
  institution: string;
  degree?: string;
  field_of_study?: string;
  start_date?: string;
  end_date?: string;
}

export interface SkillsSection {
  flat?: string[];
  categorized?: Record<string, string[]>;
}

export interface StructuredResumeDocument {
  name: string;
  contacts: ContactInfo;
  summary: string;
  experience: ExperienceEntry[];
  technical_skills: SkillsSection;
  education: EducationEntry[];
  raw_text?: string;
}

export interface StructuredResumeResponse {
  id: string;
  user_id: string;
  resume: StructuredResumeDocument;
  source_file_url?: string;
  input_conversion_warning?: string;
  created_at: string;
}

export interface StructuredResumeSummary {
  id: string;
  name: string;
  created_at: string;
  source_file_url?: string;
}

// ── Job Description ───────────────────────────────────────────────────────

export interface JobMetadata {
  company?: string;
  job_title?: string;
  location?: string;
  employment_type?: string;
  seniority_level?: string;
  remote_policy?: string;
}

export interface JobDescriptionScrapeRequest {
  url: string;
}

export interface JobDescriptionManualRequest {
  raw_text: string;
  company?: string;
  job_title?: string;
  source_url?: string;
}

export interface JobDescriptionResponse {
  id: string;
  user_id: string;
  source_url?: string;
  source_type?: "scraped" | "manual";
  raw_text: string;
  metadata?: JobMetadata;
  created_at: string;
}

export interface JobDescriptionSummary {
  id: string;
  company?: string;
  job_title?: string;
  source_url?: string;
  created_at: string;
}

// ── Generation ────────────────────────────────────────────────────────────

export type GenerationStatus = "pending" | "running" | "succeeded" | "failed";

export interface GenerationRequest {
  job_description_id: string;
  structured_resume_id: string;
}

export interface GenerationResponse {
  run_id: string;
  status: GenerationStatus;
  tailored_document_id?: string;
  message?: string;
}

export interface GenerationRunSummary {
  id: string;
  status: GenerationStatus;
  run_type: string;
  model_name: string;
  started_at: string;
  completed_at?: string;
  cost_estimate?: number;
  tailored_document_id?: string;
  company_name?: string;
  role_title?: string;
}

export interface GenerationRunDetail extends GenerationRunSummary {
  user_id: string;
  job_description_id?: string;
  prompt_version: string;
  token_input?: number;
  token_output?: number;
  error_message?: string;
}

// ── Tailored Document ─────────────────────────────────────────────────────

export interface ArtifactURLs {
  resume_docx_url?: string;
  resume_pdf_url?: string;
  cover_letter_docx_url?: string;
  cover_letter_pdf_url?: string;
}

export interface TailoredDocumentDetail {
  id: string;
  user_id: string;
  generation_run_id: string;
  company_name: string;
  role_title: string;
  resume_json?: Record<string, unknown>;
  cover_letter_json?: Record<string, unknown>;
  artifacts: ArtifactURLs;
  created_at: string;
}

// ── Billing ───────────────────────────────────────────────────────────────

export type PlanType = "free" | "starter" | "pro";
export type SubscriptionStatus = "active" | "canceled" | "past_due" | "trialing" | null;

export interface BillingStatus {
  user_id: string;
  plan_type: PlanType;
  subscription_status?: SubscriptionStatus;
  free_generations_used: number;
  free_generations_limit: number;
  current_period_end?: string;
  stripe_customer_id?: string;
}

export interface CheckoutSessionRequest {
  plan_type: "starter" | "pro";
  success_url: string;
  cancel_url: string;
}

export interface CheckoutSessionResponse {
  checkout_url: string;
  session_id: string;
}

// ── Evaluation ────────────────────────────────────────────────────────────

export interface EvaluationScores {
  truthfulness_score?: number;
  role_fit_score?: number;
  clarity_score?: number;
  seniority_score?: number;
  integrated_score?: number;
}

export interface EvaluationResponse {
  id: string;
  generation_run_id: string;
  scores: EvaluationScores;
  created_at: string;
}

// ── Admin ─────────────────────────────────────────────────────────────────

export interface GenerationConfigResponse {
  simple_model: string;
  available_models: string[];
}

export interface GenerationConfigRequest {
  simple_model: string;
}

export interface AdminActionResponse {
  ok: boolean;
  message: string;
}

export interface SystemStats {
  total_users: number;
  total_generation_runs: number;
  total_succeeded_runs: number;
  total_failed_runs: number;
  total_tailored_documents: number;
  total_evaluation_runs: number;
}

// ── Benchmark ─────────────────────────────────────────────────────────────

export type BenchmarkStatus = "queued" | "running" | "completed" | "failed";

export interface BenchmarkStartRequest {
  client_id: string;
}

export interface BenchmarkRunSummary {
  id: string;
  client_id: string;
  status: BenchmarkStatus;
  positions_count: number | null;
  completed_positions: number;
  integrated_score: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface BenchmarkPositionSummary {
  id: string;
  position_url: string;
  company: string | null;
  role_title: string | null;
  truthfulness_score: number | null;
  role_fit_score: number | null;
  seniority_positioning_score: number | null;
  clarity_impact_score: number | null;
  mechanism_quality_score: number | null;
  constraint_compliance_score: number | null;
  cover_letter_effectiveness_score: number | null;
  overall_readiness_score: number | null;
  integrated_score: number | null;
}

export interface BenchmarkRunDetail extends BenchmarkRunSummary {
  truthfulness_score: number | null;
  role_fit_score: number | null;
  seniority_positioning_score: number | null;
  clarity_impact_score: number | null;
  mechanism_quality_score: number | null;
  constraint_compliance_score: number | null;
  cover_letter_effectiveness_score: number | null;
  overall_readiness_score: number | null;
  simple_model: string | null;
  error_message: string | null;
  report_dir: string | null;
  weights_json: Record<string, number> | null;
  positions: BenchmarkPositionSummary[];
}

// ── Generic ───────────────────────────────────────────────────────────────

export interface ApiError {
  detail: string;
}
