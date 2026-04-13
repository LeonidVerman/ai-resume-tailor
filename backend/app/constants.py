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

# Default initial credits granted to new users (overridden by admin_config.initial_credits)
SIGNUP_CREDITS_DEFAULT = 3

# Generation modes
GENERATION_MODE_CONSERVATIVE = "conservative"
GENERATION_MODE_NORMAL = "normal"
GENERATION_MODE_AGGRESSIVE = "aggressive"
GENERATION_MODES = (GENERATION_MODE_CONSERVATIVE, GENERATION_MODE_NORMAL, GENERATION_MODE_AGGRESSIVE)
