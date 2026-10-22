# Operations

The default deployment runs a single-operator workbench, API, worker, PostgreSQL and Redis. It uses a private Docker network and publishes only the API at `127.0.0.1:8000`.

## Configuration

Copy [`.env.example`](../.env.example) to `.env`. Compose reads this file; commands run directly on the host need exported environment variables.

Required Compose settings:

- `HARMONIA_API_KEY`: a random operator key of at least 24 characters.
- `POSTGRES_PASSWORD`: a different random, URL-safe database password. Compose embeds it in the database URL.

Generate each value separately with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`.

Host-process settings:

- `DATABASE_URL`: SQLAlchemy PostgreSQL URL using `postgresql+psycopg://`.
- `REDIS_URL`: Redis URL shared by API and worker.
- `HARMONIA_API_URL`: API base URL for the host CLI, defaulting to `http://127.0.0.1:8000/`.

Compose supplies internal `DATABASE_URL` and `REDIS_URL` values for its containers. The values in `.env.example` for these two variables describe host connections, not additional published container ports. Optional `SPOTIFY_ACCESS_TOKEN`, `YOUTUBE_AUTH_FILE`, `YANDEX_TOKEN` and `MUSICBRAINZ_USER_AGENT` are documented in [provider setup](providers.md). API and worker must use the same configuration. `HARMONIA_ENV_FILE` can select an alternative container environment file; use Compose's `--env-file` option as well when changing interpolation values.

Keep credentials out of source control, build contexts, logs and canonical JSON. Mount provider files read-only and make them readable by container UID 10001. The web UI accepts only the operator key and keeps it in tab memory; it does not manage provider tokens.

## Start, stop and inspect

Run from the repository root after configuring `.env`:

```sh
docker compose up --build -d --wait
docker compose ps
docker compose logs --tail 100 api worker
```

A one-shot migration service runs before API and worker start. The worker runs as `playlist-worker`; API and worker share a non-root image with a read-only root filesystem and writable temporary storage.

To stop the deployment while preserving database and queue volumes:

```sh
docker compose down
```

Do not add `-v` unless you intend to remove the deployment's persisted data.

For a host installation, export the configuration, then run:

```sh
uv run alembic upgrade head
uv run uvicorn harmonia.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
# In a separate terminal with the same environment:
uv run playlist-worker
```

`playlist-worker --burst` processes queued jobs and exits. Use the project's worker entry point on all supported Python versions; it manages event-loop creation and shutdown.

## Health and monitoring

`GET /healthz` is public and checks API process liveness. `GET /api/ready` requires `X-API-Key` and checks PostgreSQL and Redis connectivity:

```sh
curl --fail http://127.0.0.1:8000/healthz
curl --fail -H "X-API-Key: $HARMONIA_API_KEY" http://127.0.0.1:8000/api/ready
```

Export `HARMONIA_API_KEY` in the shell before using the second command; the CLI and curl do not load `.env` automatically.

`playlist-worker --check` returns 0 when ARQ's heartbeat exists in the configured Redis, and 1 when it is absent. The worker refreshes the expiring heartbeat every 10 seconds. Compose uses this check during `up --wait`. A heartbeat confirms worker activity, not successful completion of individual jobs.

Monitor job failures, queue age, PostgreSQL capacity, provider authentication errors and MusicBrainz rate-limit responses. Application events use JSON logging; raw SDK HTTP logs are suppressed to reduce credential exposure. Avoid wire/debug logging with real credentials.

## Remote access

Put the API behind a TLS reverse proxy with authentication, connection/rate limits and a 5 MB request-body limit. Do not expose the plaintext API, Redis or PostgreSQL ports directly. The application provides a shared operator credential, not tenant isolation or public registration. OpenAPI and static assets are public within the deployment's access boundary.

## Export recovery

Imports, builds, previews and exports return background job IDs. Poll `GET /api/jobs/{id}`; results expire after 24 hours. PostgreSQL previews and operation records persist independently of Redis results.

Export records can be inspected through a PostgreSQL session:

```sql
SELECT key, state, created_at, result FROM operations ORDER BY created_at DESC;
```

The states mean:

- `pending`: a writer reserved the key. A crash may have happened before or after the external write.
- `uncertain`: the external operation raised an error and may have partially succeeded.
- `succeeded`: the remote playlist snapshot was recorded successfully.

For a pending or uncertain export, inspect the destination account for a created or partially populated private playlist. Reconcile that result with the operation record before retrying. Do not reset an unresolved row or use a new key without checking the remote result: either action can create duplicates.

The journal prevents automatic duplicate creation for one key; it does not provide a distributed transaction. Automatic rollback and reconciliation are not available. A worker failure may also leave a failed job without a business result, so the operation journal is the durable source for write recovery.

## Backups and upgrades

Back up PostgreSQL snapshots, mappings, previews and the export journal, plus Redis queue data. PostgreSQL and Redis use named Docker volumes. Establish a retention policy for old snapshots, previews and successful operations; never prune pending or uncertain writes automatically. Test restore procedures before relying on backups.

Run migrations once before starting upgraded API and worker processes. Review migration notes before downgrading. Migration `0002` widens namespaced playlist keys; older code does not require the column to shrink. Its downgrade refuses keys longer than 500 characters instead of truncating identities.

## Troubleshooting

If a provider is **Not configured**, check the server environment and restart both API and worker. A configured state only indicates that credentials were supplied. HTTP 401/403 or SDK authentication errors can require renewed credentials, different scopes or account permissions; see [provider setup](providers.md).

If a job remains queued, inspect worker logs, heartbeat status and whether API and worker share Redis. If an export times out, use the recovery procedure above before retrying. If compilation returns a short or empty playlist, inspect warnings, explicit status, missing durations/features and artist rules in [the usage guide](usage.md).

Before using real service exports, verify search/read access and publish a small disposable private playlist. Automated tests mock external music APIs; they cannot verify your account, region, token or service quota.
