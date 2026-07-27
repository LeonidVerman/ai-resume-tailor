# Import all models so SQLAlchemy's mapper can resolve relationships regardless
# of which module is imported first.
from backend.app.db.models import (  # noqa: F401
    billing,
    candidate_profile,
    evaluation_run,
    generation_run,
    guest,
    job_description,
    monthly_usage,
    structured_resume,
    tailored_document,
    user,
)
