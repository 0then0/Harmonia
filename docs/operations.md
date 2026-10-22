# Operations

## Configuration and secrets

The default compose deployment is loopback-only and single-operator. Use distinct randomly generated operator and database secrets. Set `POSTGRES_PASSWORD` to a URL-safe random value because Compose constructs the database URL from it. CLI does not auto-load `.env`. If running API and worker on the host, export `DATABASE_URL`, `REDIS_URL`, `HARMONIA_API_KEY` and optional provider settings before starting them:

```sh
uv run alembic upgrade head
uv run uvicorn harmonia.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
# separate terminal, same environment:
uv run playlist-worker
```

Use `playlist-worker` (or `python -m harmonia.infrastructure.jobs`) to start the worker on every supported Python version. It creates the event loop explicitly with `asyncio.run()`. The upstream ARQ 0.26 CLI depends on implicit event-loop creation and fails on Python 3.14+; do not use it as this project's worker entry point. `playlist-worker --burst` processes queued jobs and exits.

Never mount provider browser credentials writable. They can confer account access. Do not expose Redis/PostgreSQL publicly. The default Docker healthcheck proves HTTP process liveness only; monitor authenticated `/api/ready`, worker failures, queue age, PostgreSQL capacity and MusicBrainz 429/503 responses separately. Logs use JSON for application events and deliberately suppress SDK HTTP logs. Avoid enabling debug/wire logs with real tokens.

Add a TLS reverse proxy for remote access with a 5 MB request-body limit and suitable connection/rate limits. The operator API is not designed for untrusted public bulk uploads or multiple tenants. Store the API key in a password manager, not in the UI source or public URL. The web UI keeps the entered operator key only in JavaScript memory and clears the password field after successful unlock.

## Jobs and exports

API endpoints return 202 with a job UUID; poll `/api/jobs/{id}`. Worker import/build/preview/export handlers return `ok` and a safe error message. Results remain in Redis for 24 hours. A process crash can leave a failed job without a business result. Export claims in PostgreSQL are the durable source of truth:

```sql
SELECT key, state, created_at, result FROM operations ORDER BY created_at DESC;
```

`pending` means a writer reserved the key; it might have crashed before or after a remote write. `uncertain` means a remote operation raised an error, possibly after partially succeeding. `succeeded` contains the confirmed remote playlist snapshot. Never reset an unresolved row or blindly retry with a new key. Inspect the provider library first, identify any partial/private playlist, and choose an explicit recovery action. This release has no automated reconcile/rollback command. The journal prevents duplicate writes using the same key; it does not make remote requests transactional.

Migration `0002` widens namespaced playlist keys without changing existing data. Rolling back code does not require shrinking this column. The migration downgrade refuses keys longer than 500 characters rather than truncating them.

Run one migration job before deploying API/worker. Back up PostgreSQL (canonical snapshots, overrides, previews, idempotency history) and Redis (queued jobs). Do not discard the idempotency journal on upgrade. Establish retention limits for imported snapshots, previews and succeeded operations according to your deployment. Never prune pending/uncertain operations automatically. No schema or retention task runs silently at application startup.

## Scope of service verification

Default tests mock all external music APIs. PostgreSQL, Redis and ARQ integration tests use local disposable services. Before relying on a deployment, check the account's search/read permissions and export a deliberately small private playlist. Spotify's current development-mode restrictions can disallow public playlist imports. Unofficial YouTube/Yandex SDKs may break as services change. Tokens, regions and accounts are part of that live integration check.
