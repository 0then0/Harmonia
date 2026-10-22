# Testing

Harmonia uses pytest for domain, API, provider-contract and integration tests. Ruff checks source formatting and compatibility with Python 3.10. Run commands from the repository root with the committed `uv.lock`.

## Local checks

```sh
uv sync --frozen
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run pytest -m 'not integration' --cov=harmonia --cov-report=term-missing
uv run playlist build examples/night-drive.yml \
  --library examples/library.json --output night-drive.json
```

These tests use mocked external APIs. They cover canonical validation, matching and edition conflicts, compiler constraints and determinism, transfer review gates, authentication and CLI behaviour. The sample compilation uses fictional tracks and requires no network service.

## Integration tests

The [test Compose file](../compose.test.yml) starts disposable PostgreSQL and Redis services. It uses no persistent database volume and exposes only loopback ports 55432 and 56379. Make sure these ports are free before starting:

```sh
docker compose -p harmonia-test -f compose.test.yml up -d --wait
export HARMONIA_TEST_DATABASE_URL=postgresql+psycopg://harmonia_test:disposable_test_password@127.0.0.1:55432/harmonia_test
export HARMONIA_TEST_REDIS_URL=redis://127.0.0.1:56379/0
DATABASE_URL="$HARMONIA_TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest -m integration
DATABASE_URL="$HARMONIA_TEST_DATABASE_URL" uv run alembic check
docker compose -p harmonia-test -f compose.test.yml down
```

Use only disposable services for these variables. Integration tests write snapshots, mappings, operation records, queue jobs and Redis cache keys. They exercise PostgreSQL atomic claims, Redis rate coordination, a real ARQ worker process, shutdown and heartbeat checks. Tests are skipped when either integration URL is absent.

With both URLs set, `uv run pytest` runs the complete suite. Use a separate Redis database and PostgreSQL database for concurrent test runs.

## Container startup

Prepare `.env` as described in [README](../README.md#quick-start-web-workbench), then run:

```sh
docker compose up --build -d --wait
docker compose exec -T worker playlist-worker --check
docker compose exec -T api playlist --help
curl --fail http://127.0.0.1:8000/healthz
```

Also verify authenticated `/api/ready` as described in [operations](operations.md#health-and-monitoring). Run this workflow on a disposable deployment when testing migrations or recovery. `docker compose down` preserves volumes; removal of volumes is appropriate only for disposable data.

## CI and supported environments

[GitHub Actions](https://github.com/0then0/Harmonia/actions/workflows/ci.yml) installs the frozen lockfile and runs lint, migrations, the complete suite and sample CLI compilation on CPython 3.10, 3.11, 3.12, 3.13, 3.14 and 3.15 under Linux. A separate job builds and starts the Compose deployment, verifies API readiness and exercises the CLI.

For another interpreter locally, set `UV_PROJECT_ENVIRONMENT` to a separate environment path and use `uv sync --frozen --python VERSION`. Standard GIL builds are supported. Windows and free-threaded builds are not part of the CI matrix.

Provider-contract tests validate adapter behaviour using representative payloads and SDK objects. They do not establish current service access for a real account. Live tests must be run separately with authorized credentials and small disposable private playlists; never enable them as default test traffic.
