# frontend/Dockerfile
# Multi-stage build for the Next.js frontend.
# Build context: frontend/ directory
#
# Build:  docker build -f frontend/Dockerfile -t ai-resume-tailor-frontend ./frontend
# Run:    docker run -p 3000:3000 --env-file frontend/.env ai-resume-tailor-frontend

# ── Stage 1: Install dependencies ────────────────────────────────────────────
FROM node:20-slim AS deps
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --prefer-offline

# ── Stage 2: Build ────────────────────────────────────────────────────────────
FROM node:20-slim AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_API_URL must be set at build time for client-side usage.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# Turnstile site key for the guest /try flow (#155).  NEXT_PUBLIC_* values
# are inlined at build time; empty means the widget shows "not configured"
# in production builds.  Use Cloudflare's test key locally.
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY=
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=$NEXT_PUBLIC_TURNSTILE_SITE_KEY

RUN npm run build

# ── Stage 3: Production runtime ───────────────────────────────────────────────
FROM node:20-slim AS runner
WORKDIR /app

ENV NODE_ENV=production

# Copy only what is needed to run
# public/ is optional — only copy if it exists (created during build if empty)
RUN mkdir -p ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]
