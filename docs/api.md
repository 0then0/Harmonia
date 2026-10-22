# REST API

The API serves canonical music metadata and queues long-running operations. Start the deployment using [README](../README.md#quick-start-web-workbench). Interactive documentation is available at `http://127.0.0.1:8000/docs`, with the OpenAPI schema at `/openapi.json`.

## Authentication

Every `/api` endpoint requires `X-API-Key` containing the server's `HARMONIA_API_KEY`. The workbench, static assets, OpenAPI and `/healthz` are public within the deployment's network boundary. Configure TLS and a reverse proxy for remote access; see [operations](operations.md#remote-access).

Export the operator key in your shell before running examples:

```sh
export HARMONIA_API_KEY='your-operator-key'
curl --fail -H "X-API-Key: $HARMONIA_API_KEY" \
  http://127.0.0.1:8000/api/providers
```

Provider IDs in requests are `spotify`, `youtube` or `yandex`. Credentials are configured on the server and are never submitted in API payloads.

## Canonical snapshots

`POST /api/playlists` validates and stores a canonical Playlist and returns its namespaced `playlist_id` with HTTP 201:

```sh
curl --fail -H "X-API-Key: $HARMONIA_API_KEY" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/library.json \
  http://127.0.0.1:8000/api/playlists
```

Use that returned ID with `GET /api/playlist?id=...`. `GET /api/playlists?limit=200&offset=0` lists summaries; limit ranges from 1 to 200 and offset starts at 0. IDs and titles can contain characters that need URL encoding.

`GET /api/providers` lists configuration state and capability flags. `GET /api/providers/{provider}/playlists` reads account playlists; `GET /api/providers/{provider}/search?q=...` searches its catalogue. These are live metadata requests and require the corresponding server credentials.

## Background jobs

The following POST endpoints return HTTP 202 and `{"job_id": "UUID"}`:

- `/api/jobs/import`: `{"provider": "spotify", "reference": "PLAYLIST_ID"}`.
- `/api/jobs/build`: `{"playlist_ids": ["STORED_ID"], "constraints": {"duration": "90m"}}`.
- `/api/jobs/preview`: `{"playlist_id": "STORED_ID", "provider": "spotify"}`.
- `/api/jobs/export`: `{"preview_id": "UUID", "idempotency_key": "night-drive-v1"}`.

Poll `GET /api/jobs/{job_id}`. A completed job has `status: "complete"` and a result containing `ok`. Successful imports/builds return `playlist_id`, previews return `preview_id`, and exports return the saved remote `playlist_id`. A completed job with `ok: false` contains a sanitized error; completion alone does not mean the operation succeeded. `not_found` means no current job/result was found, including after result expiry. Results are retained for 24 hours.

A build accepts up to 20 stored library IDs and 1,000 total candidates. A preview accepts up to 200 source tracks. Full schemas and field bounds are in OpenAPI.

## Match review and export

Read a generated preview with `GET /api/previews/{preview_id}`. Each result contains its chosen match, confidence, reasons, alternatives and review status. Export requires a stored preview with `ready: true` and every match resolved; clients cannot supply a ready flag as export authorization.

To save a correction, POST `/api/matches/confirm` with `source` and `target` canonical Track objects and the destination `provider`. The target must contain an available ID for that provider. Generate a new preview to use the correction.

Export creates a new private playlist. The idempotency key identifies the resolved export payload; changing that payload while reusing a key causes a conflict. Interrupted remote writes may leave partial playlists. Follow [export recovery](operations.md#export-recovery) before retrying a pending or uncertain operation.

## Constraints and metadata

`POST /api/constraints/validate` accepts `{"yaml": "duration: 90m\n"}` and returns validated PlaylistConstraints. YAML is limited to 65,536 characters. See [the usage guide](usage.md#compile-a-playlist) for field meanings.

MusicBrainz metadata is available through `GET /api/metadata/isrc/{isrc}` and `GET /api/metadata/{entity}/{mbid}`, where entity is `recording`, `artist`, `release` or `release-group`. These requests share the deployment's cache and rate limiter.

## Errors and health

Invalid credentials return 401, missing resources 404, conflicting operations or unresolved export previews 409, and invalid input 422. Sanitized provider failures return 502. Background failures are represented in the job result, so inspect `ok` as well as HTTP status.

`GET /healthz` checks API process liveness. Authenticated `GET /api/ready` checks PostgreSQL and Redis. Worker heartbeat monitoring is separate; see [health and monitoring](operations.md#health-and-monitoring).
