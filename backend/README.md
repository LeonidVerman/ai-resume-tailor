# backend

> **Status:** Scaffolded — not yet implemented.
>
> This directory will contain the FastAPI backend for the SaaS platform.
> See `doc/IMPLEMENTATION_SPEC.md` and `doc/IMPLEMENTATION_TASK_PLAN.md` for the full spec.

The existing CLI generator lives at the **repository root** (`src/tailor/`).
It will be integrated into this backend as a service in a later phase.

## Planned structure

```
backend/
  pyproject.toml      Python project + dependencies
  alembic.ini         Alembic migration config
  .env.example        Backend-specific env vars
  app/
    main.py           FastAPI app factory
    config.py         Settings (env-driven)
    api/              Route handlers
    services/         Business logic
    db/               SQLAlchemy models + repositories
    schemas/          Pydantic request/response models
    clients/          External API clients
    domain/           Enums, types, errors, policies
    utils/            Shared utilities
    workers/          Background task queue
    prompts/          Versioned LLM prompt files
  tests/
    conftest.py
    unit/
    integration/
    api/
  alembic/
    env.py
    versions/
```

## Implementation phases

- Phase 2: Backend core skeleton (main.py, config, health endpoint)
- Phase 3: Database layer (models, migrations)
- Phase 4: Pydantic schemas
- Phase 5: External clients
- Phase 6: Service layer
- Phase 7: Prompt assets
- Phase 8: API endpoints
- Phase 10: Tests
