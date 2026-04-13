// frontend/src/config/exampleProfileData.ts
//
// Generic example data for the candidate profile form.
// Used by the "Load from example profile" feature to populate one section at a time.
// All examples are broadly applicable to software engineers — no specific company
// names, niche domain references, or underscore_notation.

import type {
  CandidateIdentity,
  DomainExperience,
  ExperienceHighlight,
  TechnicalSkills,
  Leadership,
  AIToolingPractice,
  ConstraintsAndPreferences,
  ClaimBoundaries,
} from "@/types/api";

export const EXAMPLE_CANDIDATE: CandidateIdentity = {
  name: "Jane Smith",
  headline: "Software engineer, backend systems and cloud applications",
  summary:
    "Software engineer with experience building and maintaining backend and full-stack systems. " +
    "Focused on scalability, reliability, and delivering production-quality features across diverse teams.",
};

export const EXAMPLE_DOMAINS: DomainExperience = {
  primary: [
    "Web applications",
    "Backend systems",
    "Cloud infrastructure",
  ],
  secondary: [
    "Enterprise SaaS",
    "Financial technology",
    "AI-enabled applications",
  ],
};

export const EXAMPLE_HIGHLIGHTS: ExperienceHighlight[] = [
  {
    area: "Backend platform development",
    impact: [
      "Improved application performance and reliability",
      "Reduced response times through backend and database optimization",
      "Resolved production issues and improved overall system stability",
    ],
    team_context: [
      "Worked in cross-functional engineering teams",
      "Collaborated with product and QA stakeholders",
    ],
    architecture_patterns: [
      "Service-oriented design",
      "API-based architecture",
      "Layered application structure",
    ],
    constraints_and_tradeoffs: [
      "Worked within an existing architecture",
      "Balanced feature delivery with system stability",
    ],
    skills_applied: [
      "Backend development",
      "API design",
      "Database optimization",
      "Debugging and troubleshooting",
    ],
    security_auth_patterns: [],
  },
];

export const EXAMPLE_TECHNICAL_SKILLS: TechnicalSkills = {
  languages: ["Java", "Python", "JavaScript", "TypeScript"],
  backend_systems: [
    "REST API development",
    "Backend services",
    "Integrations and business logic",
  ],
  datastores: ["PostgreSQL", "MySQL", "NoSQL", "Query optimization"],
  infra_devops: ["AWS", "Docker", "CI/CD pipelines", "Monitoring and logging"],
  frontend: ["React", "HTML", "CSS", "Frontend integration"],
  api_patterns: ["REST", "GraphQL", "Webhooks"],
  async_messaging: ["Message queues", "Kafka", "Asynchronous processing"],
  observability: ["Metrics and logging", "Distributed tracing", "Error monitoring"],
  security_auth_patterns: [
    "Token-based authentication",
    "Role-based access control",
    "Secure API design",
  ],
  scalability_reliability_patterns: [
    "Caching",
    "Horizontal scaling",
    "Asynchronous processing",
    "Performance tuning",
  ],
};

export const EXAMPLE_LEADERSHIP: Leadership = {
  scope: {
    team_size_max: 8,
    style_keywords: ["Ownership", "Collaboration", "Execution", "Continuous improvement"],
  },
  practices: [
    "Participates in code reviews and knowledge sharing",
    "Collaborates with cross-functional teams",
    "Breaks down complex tasks into manageable deliverables",
  ],
  risk_management: [
    "Prioritizes reliability and stability",
    "Validates changes through testing and incremental rollout",
    "Balances delivery speed with code quality",
  ],
};

export const EXAMPLE_AI_TOOLING: AIToolingPractice = {
  hands_on_tools: ["ChatGPT", "GitHub Copilot", "AI-assisted IDE tools"],
  usage_patterns: [
    "Debugging issues",
    "Learning new frameworks and APIs",
    "Improving development workflows",
  ],
  principles: [
    "Engineer remains accountable for code quality",
    "Avoid sharing sensitive data with AI tools",
    "Review and validate AI-generated output",
  ],
  concepts_familiarity: [
    "Large language models",
    "RAG systems",
    "AI-assisted development",
  ],
};

export const EXAMPLE_ROLE_THEMES: string[] = [
  "Backend development",
  "Scalable system design",
  "Cloud-based applications",
  "API development",
  "Production reliability",
  "Cross-functional collaboration",
];

export const EXAMPLE_CONSTRAINTS: ConstraintsAndPreferences = {
  work_context: [
    "Agile engineering teams",
    "Product-focused development",
    "Collaborative software environments",
  ],
  communication: [
    "Clear technical communication",
    "Cross-functional collaboration",
    "Documentation and knowledge sharing",
  ],
  resume_constraint: [
    "Maintain accuracy of roles and dates",
    "Avoid exaggerating responsibilities",
    "Keep claims aligned with actual experience",
  ],
};

export const EXAMPLE_CLAIM_BOUNDARIES: ClaimBoundaries = {
  security_auth: [
    "Do not claim ownership of large-scale security architecture unless supported",
    "Keep security claims aligned with actual implementation experience",
  ],
  domain_limits: [
    "Avoid overstating leadership scope",
    "Keep domain claims aligned with actual experience",
  ],
  employment_constraints: [
    "Maintain accuracy of roles and dates",
    "Avoid implying a different employment status than what applies",
  ],
};
