# Contributing to Harmonia

Bug fixes, clearer documentation, matching regressions and provider improvements are welcome. For public API changes, new dependencies or destructive migrations, open an issue to agree on scope before implementation.

## Development setup

Clone the repository and install the locked development environment:

```sh
git clone https://github.com/0then0/Harmonia.git
cd Harmonia
uv sync --frozen
```

Use CPython 3.10–3.15. Keep source syntax and standard-library imports compatible with Python 3.10. For another interpreter, use a separate environment rather than replacing your current `.venv`:

```sh
UV_PROJECT_ENVIRONMENT=../harmonia-venv-310 uv sync --frozen --python 3.10
UV_PROJECT_ENVIRONMENT=../harmonia-venv-310 uv run pytest -m 'not integration'
```

[Testing](docs/testing.md) covers integration services, migrations and the CI matrix. [Architecture](architecture.md) explains package boundaries; read [adding a provider](docs/provider-development.md) before implementing an adapter.

## Making a change

Keep the change focused on one problem. Preserve public formats and service authentication behaviour unless the change explicitly updates that contract. Domain algorithms must remain independent of databases, HTTP frameworks and provider SDKs.

Add a regression test for a bug fix. Useful tests check observable behaviour: edition/artist mismatches, ambiguous results, compiler constraints, deterministic output, complete pagination, revision conflicts and concurrent exports. Default tests must not contact real music services.

Use fictional or sanitized fixtures. Do not commit access tokens, browser authentication exports, audio files, personal library dumps or raw provider responses containing private data. Report problems with reproducible metadata rather than account credentials.

## Checks before a pull request

```sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m 'not integration'
uv lock --check
```

Run the [integration suite](docs/testing.md#integration-tests) for persistence, job, transport or rate-limit changes. For container changes, run the documented Compose startup and health checks. Compatibility changes require frozen installation and the full suite on every supported Python minor version.

Dependency changes should be intentional and include `uv.lock`. Dependabot groups updates on a monthly schedule; review compatibility and CI results before merging.

## Database migrations

Generate migrations against a disposable database after applying existing migrations:

```sh
uv run alembic revision --autogenerate -m description
uv run alembic upgrade head
uv run alembic check
```

Inspect generated operations, consider existing data and document downgrade limits. Keep applied migrations intact. Destructive schema changes require an explicit migration and recovery plan.

## Pull requests and commits

Explain the problem, the resulting behaviour and how it was verified. Include relevant limitations, especially when external account access was not tested. Update user documentation when commands, configuration or behaviour change.

Use English Conventional Commit messages with `feat`, `fix`, `refactor`, `chore`, `docs` or `test`. Follow the [pull request template](.github/pull_request_template.md). Open a [bug report](https://github.com/0then0/Harmonia/issues/new/choose) with a minimal reproduction, or use the provider request template to propose service support.
