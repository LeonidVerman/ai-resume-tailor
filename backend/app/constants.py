"""
backend/app/constants.py

Shared application constants.
"""

API_PREFIX = "/api/v1"
SERVICE_NAME = "resume-tailor-backend"

# Plan types
PLAN_FREE = "free"
PLAN_STARTER = "starter"
PLAN_PRO = "pro"

# Monthly generation limits per plan (change here to adjust quotas for all new/unoverridden users)
PLAN_MONTHLY_LIMITS: dict[str, int] = {
    PLAN_FREE: 3,
    PLAN_STARTER: 40,
    PLAN_PRO: 200,
}

# One-time credit pack
CREDIT_PACK_SIZE = 10
CREDIT_PACK_PRICE_DISPLAY = "$4.99"

# User roles
ROLE_USER = "user"
ROLE_ADMIN = "admin"

# Signup credit policy
SIGNUP_CREDIT_MODE_NORMAL = "normal"
SIGNUP_CREDIT_MODE_BETA = "beta"
SIGNUP_CREDIT_MODES = (SIGNUP_CREDIT_MODE_NORMAL, SIGNUP_CREDIT_MODE_BETA)
SIGNUP_CREDIT_AMOUNTS: dict[str, int] = {
    SIGNUP_CREDIT_MODE_NORMAL: 3,
    SIGNUP_CREDIT_MODE_BETA: 10,
}
# Modes that suppress the regular monthly plan quota (user lives entirely on extra_credits)
SIGNUP_CREDIT_MODES_OVERRIDE_MONTHLY: frozenset[str] = frozenset({SIGNUP_CREDIT_MODE_BETA})

# Generation modes
GENERATION_MODE_CONSERVATIVE = "conservative"
GENERATION_MODE_NORMAL = "normal"
GENERATION_MODE_AGGRESSIVE = "aggressive"
GENERATION_MODES = (GENERATION_MODE_CONSERVATIVE, GENERATION_MODE_NORMAL, GENERATION_MODE_AGGRESSIVE)
