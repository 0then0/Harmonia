# Contributing

Keep changes small and explain the user-visible behaviour. Discuss public contracts, new dependencies and destructive migrations before implementing them. Use Python 3.10–3.15, the checked-in uv lockfile and existing package boundaries. Never add real tokens, browser auth exports, audio, personal library dumps or provider responses containing private data to fixtures.

Set up with `uv sync --frozen`. Run `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest -m 'not integration'`. For persistence, job and rate-limit changes also run the disposable PostgreSQL/Redis integration workflow in README. CI runs both suites and a container smoke test. No tests may contact a music provider by default. Keep source syntax and standard-library imports compatible with Python 3.10; Ruff targets `py310`. When changing compatibility, validate both installation from the frozen lockfile and the full suite, including the standalone worker bootstrap, on every supported minor version. Do not replace an existing development environment merely to test another interpreter; set `UV_PROJECT_ENVIRONMENT` to a separate path.

Protect behaviours rather than line coverage: especially wrong edition/artist matches, ambiguity gates, all constraints, deterministic output, pagination, conflicting revisions, concurrent idempotency and uncertain write outcomes. Include a regression test for an observed defect. Domain algorithms must remain independent of API frameworks, databases and provider SDKs.

Use `uv run alembic revision --autogenerate -m description` against a disposable migrated database, inspect generated operations and run `alembic upgrade head` / `alembic check`. Never squash, drop or rewrite an applied migration without an explicit migration plan.

Conventional Commits use English and one of `feat`, `fix`, `refactor`, `chore`, `docs`, `test`. A PR should explain the problem, resulting behaviour, verification actually performed and material limitations. Do not claim live service compatibility from mocked tests alone. Read [the provider guide](docs/provider-development.md) before adding an adapter.
