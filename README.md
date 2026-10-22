<p align="center"><img src="src/harmonia/api/static/icon.svg" width="112" height="112" alt="Harmonia: independent music paths converging on one record"></p>

# Harmonia

[![CI](https://img.shields.io/github/actions/workflow/status/0then0/Harmonia/ci.yml?branch=main&label=checks)](https://github.com/0then0/Harmonia/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.15-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/0then0/Harmonia)](LICENSE)
[![Self hosted](https://img.shields.io/badge/self--hosted-PostgreSQL%20%2B%20Redis-167D8D)](compose.yml)

A provider-independent music library and playlist engine. Import a library, inspect cross-catalogue matches, compile a playlist from explicit constraints, and export only the matches you trust.

Version 0.1 is the first working iteration, not a claim of audited production maturity. It implements canonical metadata, MusicBrainz, Spotify, YouTube Music, Yandex Music, conservative matching, JSON import/export, deterministic compilation, a REST API, CLI, and a small operator UI. DSP, fingerprinting, embeddings, AI, and continuous synchronization are deliberately deferred.

## Try the core without services

Requirements: Python 3.10–3.15 and [uv](https://docs.astral.sh/uv/). No tokens, database, Redis, audio models, or AI accounts are needed for local compilation.

```sh
uv sync --frozen
uv run playlist build examples/night-drive.yml \
  --library examples/library.json --output /tmp/night-drive.json
uv run pytest -m 'not integration'
```

The supported range is CPython 3.10–3.15 (standard GIL builds). CI installs the frozen lockfile and runs the full suite for every minor version. On Python 3.10, uv selects compatible SQLAlchemy 2.0 and Starlette 1.7; Python 3.11+ uses the newer locked versions. Use `uv sync --frozen --python 3.10` (or another supported version) in a dedicated environment. The default Docker image stays on pinned Python 3.12.

The example library is **synthetic**. Its titles and features demonstrate the algorithm; BPM, key and mood are not measurements of commercial tracks. The compiler may return less than 90 minutes because the library and artist limits cannot satisfy the target. Warnings explain that outcome.

## Run the workbench

```sh
cp .env.example .env
# Fill HARMONIA_API_KEY and POSTGRES_PASSWORD with different random values.
# Generate each with: python -c 'import secrets; print(secrets.token_urlsafe(32))'
docker compose up --build -d
```

Open [the workbench](http://127.0.0.1:8000) and enter your operator API key. [OpenAPI / Swagger UI](http://127.0.0.1:8000/docs) supports the `X-API-Key` header. PostgreSQL migrations run as a separate one-shot service before API and worker start. PostgreSQL and Redis have persistent volumes and no published ports. The API binds to loopback on the host. For access from other machines, configure an authenticated TLS reverse proxy; do not forward the plaintext API port directly.

The compact workbench supports **System**, **Light** and **Dark** themes. Theme preference is stored locally; the operator key is kept only in memory. Libraries load in pages of 200 with a visible **Load more playlists** control (`GET /api/playlists?limit=200&offset=200`).

Use **Load clearly labelled demo library**, select it, edit YAML, and compile. For real data, import a provider playlist or upload canonical JSON. Unknown features stay unknown. Spotify audio features/analysis, YouTube audio streams and Yandex audio URLs are never requested.

`/healthz` is a process liveness check. Authenticated `/api/ready` checks PostgreSQL and Redis. Back up PostgreSQL and Redis volumes before upgrades; Redis contains queued jobs and results. Job results expire after 24 hours; PostgreSQL previews and operation history remain until an operator removes them under a chosen retention policy. This release is for one trusted operator, not a multi-user public SaaS.

## Provider configuration

All provider credentials are optional. Core compilation works with no configured provider. API and worker read the same environment at startup. The UI shows configured state, implemented capabilities, and a read-playlists connection check. Tokens cannot be entered into the web UI in this release.

- **Spotify:** set `SPOTIFY_ACCESS_TOKEN` to a user OAuth token with `playlist-read-private`, `playlist-read-collaborative` and `playlist-modify-private` scopes. This release accepts an existing token; it does not implement the authorization redirect, token vault, or refresh flow. Renew expired tokens and restart API and worker. App approval, quota mode and account rules still apply. Current playlist items endpoints use `/playlists/{id}/items`, and playlist creation uses `/me/playlists`. Spotify restricts playlist item reads to owners/collaborators; arbitrary public playlist URLs may return 403. See the [official migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) and [playlist items reference](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items).
- **YouTube Music:** uses [ytmusicapi](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html), an unofficial SDK. Create a browser credentials file with the SDK's setup procedure. Set `YOUTUBE_AUTH_FILE=/run/providers/browser.json` in `.env` and mount your file **read-only into both API and worker**. OAuth setup is not implemented by this adapter and its OAuth capability is false. Public playlist reads/search can work unauthenticated when using the adapter directly; the server only enables it after configuring the auth file.
- **Yandex Music:** uses the unofficial [yandex-music SDK](https://github.com/MarshalX/yandex-music-api). Set `YANDEX_TOKEN` to an existing user token. Use `owner:kind` or `/users/<owner>/playlists/<kind>` URLs; the new opaque UUID playlist URLs are not resolved in this release. Album IDs are preserved because writes require track and album IDs.
- **MusicBrainz:** set a meaningful `MUSICBRAINZ_USER_AGENT`, preferably including your deployment's contact URL. Metadata, including a missing ISRC result, is cached for 24 hours. A Redis-coordinated limiter spaces requests by at least 1.05 seconds across all processes in this installation, including retries. Separate installations behind one public IP need a shared limiter namespace/Redis. Follow [MusicBrainz API usage and licensing rules](https://musicbrainz.org/doc/MusicBrainz_API).

Example `compose.override.yml` for YouTube credentials (the file must be readable by container UID 10001):

```yaml
services:
  api:
    volumes:
      - ./private/browser.json:/run/providers/browser.json:ro
  worker:
    volumes:
      - ./private/browser.json:/run/providers/browser.json:ro
```

Keep `private/`, `.env` and provider auth JSON out of Git and Docker build contexts. Service availability and unofficial SDK compatibility can change. Mocked contracts in CI do not prove that your account has live API access.

## CLI

Offline commands:

```sh
playlist build examples/night-drive.yml --library examples/library.json -o /tmp/night-drive.json
playlist match source-track.json --library target-library.json
```

Server commands require `HARMONIA_API_KEY`; optionally set `HARMONIA_API_URL` (default `http://127.0.0.1:8000/`). These environment variables must be exported in your shell; copying `.env` does not export them for a host CLI.

```sh
playlist import spotify 'https://open.spotify.com/playlist/ID' -o imported.json
playlist import youtube 'https://music.youtube.com/playlist?list=ID' -o imported.json
playlist import yandex 'owner:kind' -o imported.json
playlist diff /tmp/night-drive.json --to spotify
playlist export /tmp/night-drive.json --to spotify             # dry-run only
playlist confirm source-track.json chosen-target.json --to spotify
playlist export /tmp/night-drive.json --to spotify --apply --idempotency-key night-drive-v1
```

`diff` accepts a compiled **JSON snapshot**, not YAML; `build` is explicit so a publication cannot silently rebuild a different list. `export` always creates a **new private playlist**. It never edits a user's existing playlist. Reusing an idempotency key with the same payload returns the saved result; using it with a changed payload fails. Every unresolved match blocks export. The UI displays the proposed source-to-target substitutions, confidence, reasons and alternatives. Manual confirmations persist and are applied to subsequent previews.

The engine also supplies an occurrence-aware ordered diff and a bounded Spotify replacement primitive with revision checking, but existing-playlist synchronization is not exposed through the v0.1 API/CLI. Continuous sync, `playlist sync`, and `playlist analyze` are future iterations.

## Declarative compilation

See [the complete example](examples/night-drive.yml). Duration accepts integer seconds or `s`, `m`, `h` suffixes. `seed.artists` is a preference, not an exclusive artist filter; `random_seed` controls deterministic tie-breaking. Candidate order does not change the output.

Hard constraints: duration never exceeds the target; recordings are not repeated; artist caps count every credited artist; `artist_gap` prevents adjacent/recent artist repetition; live/remix exclusions and known explicit flags are enforced. `explicit: false` excludes tracks with unknown explicit metadata too. `max_tracks` defaults to 100 (limit 200). The compiler accepts at most 1000 candidates and beam width 1–32. A fraction of the beam preserves shorter prefixes to avoid duration dead ends. It filters candidates, uses beam search, then two bounded swap-refinement passes. It is a heuristic, not a proof of global optimality.

Soft objectives: tempo, energy and mood progression over elapsed playlist time; BPM continuity; relative-major / circle-of-fifths key compatibility; seed affinity; adjacent genre diversity; target duration coverage. Features missing from a recording contribute a neutral/low score and produce relevant warnings. `rules.missing_features: exclude` makes requested feature availability mandatory. No API adapter invents BPM, mood, energy, genres or embeddings. `transitions.semantic_similarity` must be zero in this iteration.

Canonical JSON preserves Artist, Recording, Release, Track, Playlist, ProviderTrack and AudioFeatures. Durations are milliseconds, energy/mood probabilities are 0–1, BPM is positive, keys use musical pitch names, and original/remaster/live/remix/acoustic/instrumental/radio edit/extended/sped up/slowed versions stay distinct. ISRC and MusicBrainz recording IDs are optional. AudioFeatures also reserves beat positions, loudness, embedding and fingerprint fields for later **local-only** analysis. Supplying features is allowed; calculating them is outside this release.

## Matching and write safety

The matching cascade considers ISRC and MusicBrainz recording identity, then normalized artist/title, version, duration, album and fuzzy similarity. Version mismatches, conflicting known MusicBrainz recording IDs, large duration differences, or unrelated artists/titles veto an automatic match even with the same ISRC. Matching is intentionally conservative. Different named remixes, live venues or remaster years require manual review even when the generic version type and ISRC agree. Unavailable destination entries are excluded from transfer candidates. Known duration differences above five seconds also require review. Scores are explainable heuristics, **not calibrated probabilities**. Close alternatives or missing evidence require review. Ambiguous or unavailable MusicBrainz evidence is shown in the preview and requires review; a missing MusicBrainz ISRC is a normal absence of metadata. Manual overrides can deliberately choose another edition and are labelled `manual_override`.

Reads use bounded exponential retries. Writes are never blindly retried. A PostgreSQL operation claim is committed before remote creation; success stores the returned playlist. Concurrent requests with one key cannot issue duplicate creates. A timeout, worker crash or partial multi-request export leaves `pending`/`uncertain`: inspect the remote service and `operations` table before using a new key. There is no distributed transaction with streaming services and no claim of exactly-once remote delivery. An export can leave a partially populated private playlist; automatic rollback could delete user work and is deliberately absent.

## Development and verification

```sh
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest -m 'not integration' --cov=harmonia --cov-report=term-missing
# Disposable local integration services, without persistent volumes:
docker compose -p harmonia-test -f compose.test.yml up -d --wait
export HARMONIA_TEST_DATABASE_URL=postgresql+psycopg://harmonia_test:disposable_test_password@127.0.0.1:55432/harmonia_test
export HARMONIA_TEST_REDIS_URL=redis://127.0.0.1:56379/0
DATABASE_URL="$HARMONIA_TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest -m integration
DATABASE_URL="$HARMONIA_TEST_DATABASE_URL" uv run alembic check
docker compose -p harmonia-test -f compose.test.yml down
```

Tests cover matching regressions, adapter contracts with mocked APIs, compiler constraints and determinism, review gates, authentication, retries, write journaling, PostgreSQL atomic claims, Redis rate coordination and a real ARQ worker. External-provider smoke tests are manual and must use disposable private playlists with an explicitly authorized account.

Read [architecture.md](architecture.md), [CONTRIBUTING.md](CONTRIBUTING.md), [the provider guide](docs/provider-development.md), and [operations](docs/operations.md), and [the acceptance review](docs/review.md). Licensed under [Apache-2.0](LICENSE). The project SVG icon is included under the same license; provider names and marks belong to their owners. The bundled Spotify attribution logo comes from the [official brand asset pack](https://developer.spotify.com/documentation/design) and is used under Spotify's branding terms, not the project's Apache license. Track names link back to their source service; Spotify metadata carries its attribution logo.
