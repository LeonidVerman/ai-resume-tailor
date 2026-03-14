"""
backend/app/db/models/user.py

User table — stores app-level user state.
Auth identity is delegated to Supabase Auth; this table holds
application data such as plan type, usage counters, and role.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, new_uuid
from backend.app.constants import PLAN_FREE, ROLE_USER


class User(Base, CreatedAtMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    # Supabase Auth identity link — set on first real login; null for dev-bypass users
    supabase_user_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    plan_type: Mapped[str] = mapped_column(String(32), nullable=False, default=PLAN_FREE)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    free_generations_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=ROLE_USER)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    candidate_profiles: Mapped[list["CandidateProfile"]] = relationship(  # noqa: F821
        "CandidateProfile", back_populates="user", cascade="all, delete-orphan"
    )
    structured_resumes: Mapped[list["StructuredResume"]] = relationship(  # noqa: F821
        "StructuredResume", back_populates="user", cascade="all, delete-orphan"
    )
    job_descriptions: Mapped[list["JobDescription"]] = relationship(  # noqa: F821
        "JobDescription", back_populates="user", cascade="all, delete-orphan"
    )
    generation_runs: Mapped[list["GenerationRun"]] = relationship(  # noqa: F821
        "GenerationRun", back_populates="user", cascade="all, delete-orphan"
    )
    tailored_documents: Mapped[list["TailoredDocument"]] = relationship(  # noqa: F821
        "TailoredDocument", back_populates="user", cascade="all, delete-orphan"
    )
    billing: Mapped["Billing | None"] = relationship(  # noqa: F821
        "Billing", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r} plan={self.plan_type!r}>"
