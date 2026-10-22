# Acceptance review · 2026-10-22

**PASS WITH RISKS for the v0.1 scope.** Confirmed defects found during the review have been fixed and protected by regression tests. This is not a certification of live music-provider access: account permissions, unofficial APIs and remote publication still require an authorized account smoke test.

## Scope and baseline

The baseline is commit `5a9518844e91c6d63ce8f60f3d2094a61c014e17`, which contained only LICENSE and .gitignore. The reviewed work includes all source, new/untracked files, dependency manifest/lockfile, schema migrations, tests, CI, containers, documentation and the subsequent CPython 3.10–3.15 compatibility changes created in this session. Repository history and session history establish that scope; this review was not limited to the last commit. No additional repository AGENTS.md was present. The user's supplied instructions apply.

The requested first iteration excludes DSP, embeddings, AI and continuous sync. Their absence is deliberate scope, not a substitute implementation. Existing-playlist mutations are not exposed by the v0.1 UI/CLI. The latest request also authorizes the visual redesign, fixes, tests, commit and push; it supersedes the earlier read-only review instruction.

## Confirmed findings, now closed

### 1. [P1] Yandex playlist import used the wrong SDK object

Location: `src/harmonia/providers/yandex.py:121`, `read_playlist`.

`Playlist.fetch_tracks_async()` returns `TrackShort` objects, which do not carry the `artists` attribute used by the adapter. A real playlist therefore raised AttributeError; the original mock incorrectly returned full tracks and concealed the defect. The adapter now batches metadata hydration through `ClientAsync.tracks`, restores original order and duplicate occurrences, preserves album context, and reports omitted records. The regression uses real SDK Playlist/Track/TrackShort deserialization rather than a different mock contract: `test_yandex_real_sdk_short_tracks_are_hydrated_and_order_preserved`.

### 2. [P1] Different editions were automatically matched as one recording

Location: `src/harmonia/domain/text.py:41`, `version_details`; `domain/matching.py:59`, `score`; `domain/matching.py:37`, `recording_keys`.

For example, `Teardrop (Four Tet Remix)` and `Teardrop (Club Remix)` with the same artist/duration/album received 0.97 confidence and no review requirement. Removing the whole descriptor retained only the generic `remix` tag. The same problem affected live venues and remaster years, and collapsed such editions in compiler duplicate detection. Edition details are now retained; disagreements require review even with a shared ISRC, and duplicate keys retain edition identity. Generic marker wording/order is normalized so `Remastered 2018` and `2018 Remaster` remain compatible. Tests cover different remixers, venues, years and equivalent descriptor order.

### 3. [P2] Unavailable destination tracks could make a preview ready

Location: `src/harmonia/application/transfer.py:33`, `preview_transfer`.

A high-confidence target with `ProviderTrack.available=false` could win matching despite an available alternative. Export then failed identifier validation after the preview had claimed readiness. Transfer candidate selection now requires an available ID for the destination. Tests verify both fallback to an available target and an unresolved preview when none exists.

### 4. [P2] Local validation could consume an idempotency key as uncertain

Location: `src/harmonia/application/transfer.py:103`, `export_tracks`; `transfer.py:91`, `export_preview`.

Missing destination IDs were discovered by an adapter only after the PostgreSQL operation claim. An entirely local failure was consequently recorded as an uncertain remote write. ID validation now occurs before reserving a key. Export also checks match count, missing matches and individual review flags rather than relying only on the aggregate ready flag. Regressions assert that invalid exports never claim an operation.

### 5. [P2] Invalid non-ASCII API keys returned a server error

Location: `src/harmonia/api/app.py:79`, `authenticated`.

`secrets.compare_digest(str, str)` rejects non-ASCII strings. A request header containing byte 0xff triggered TypeError instead of 401. Comparing encoded byte strings preserves constant-time comparison and returns unauthorized. `test_non_ascii_invalid_key_is_unauthorized_not_server_error` reproduces the boundary.

### 6. [P2] Older library snapshots disappeared after 200 entries

Location: `src/harmonia/infrastructure/store.py:81`, `list_playlists`; `api/app.py`, `/api/playlists`; `api/static/app.js:108`, `refresh`.

The query silently returned only 200 rows with no way to reach older snapshots from the UI. The endpoint now exposes bounded limit/offset parameters with a stable secondary sort; the UI includes Load more playlists. Refresh invalidates an export preview if its source is no longer selected. API tests cover explicit paging and rejected excessive limits.

### 7. [P2] Overdue rate-limit reservations could wake as a burst

Location: `src/harmonia/infrastructure/http.py:66`, `RedisCache.wait`.

The previous algorithm reserved future slots and slept once. After a process/event-loop pause beyond several reserved times, overdue callers could resume together and violate MusicBrainz spacing. Eligibility is now rechecked atomically against Redis time after every wake. A local integration test pauses the loop beyond the old reservations and verifies spacing of the resumed callers. No real provider receives those test calls.

### 8. [P2] Valid namespaced playlist IDs exceeded the storage column

Location: `src/harmonia/infrastructure/store.py:21`, `SavedPlaylist.id`; migration `0002_playlist_key.py`.

The public model permits a 500-character playlist ID, but prefixing `spotify:` produced a 508-character storage key for a VARCHAR(500) column. The column is widened to 600; provider names are bounded consistently with ProviderTrack. Migration 0001 is preserved. An integration test roundtrips the full-length ID. Downgrade refuses oversized keys instead of truncating them.

### 9. [P2] Blank musical credits could supply false matching evidence

Location: `src/harmonia/domain/models.py:16`, NonBlank metadata fields.

A whitespace-only artist/title passed min_length and normalized to an empty value. Two absent credits could therefore contribute exact matching evidence. Artist names and recording/release/playlist titles now require a non-whitespace character. Opaque IDs are not trimmed or rewritten. Regression tests reject whitespace-only artist/title inputs.

### 10. [P2] Safe causes were lost from job failures; failed startup skipped cleanup

Location: `src/harmonia/infrastructure/jobs.py:20`, `failure`; `jobs.py:32`, `startup`; `jobs.py:39`, `shutdown`.

HTTP 403, an idempotency conflict and an unexpected failure all became generic messages, while logs retained only the exception type. Sanitized ProviderError/Conflict causes now reach the operator; unknown SDK/DB messages remain suppressed to avoid secret leakage. Runtime is registered before startup, allowing shutdown to clean partially initialized resources. Tests cover safe HTTP status preservation, unexpected-secret suppression, and failed-startup cleanup.

### 11. [P2] Spotify metadata had no source links/attribution

Location: `src/harmonia/api/static/app.js:58`, track links; `app.js:70`, attribution; `static/spotify-logo.svg`.

The UI displayed imported metadata without linking recordings to their originating service or identifying Spotify content with its logo. Track names now link to fixed service origins using encoded IDs, and Spotify content carries the original official attribution asset. The brand asset is identified separately from the project's Apache-licensed icon in README. Source: [Spotify design guidelines](https://developer.spotify.com/documentation/design). This fixes the concrete attribution issue; it is not a broader provider-policy/legal approval.

## UI acceptance

The workbench now uses compact monochrome surface tiers, pill controls and readable metadata, with no invented artwork, streaming audio or external font dependency. System, Light and Dark are selectable. Only the theme preference is saved in localStorage; the operator key remains in memory. Browser checks confirmed system-color changes, light selection persistence after reload, dark rendering, keyboard navigation, compilation, invalid YAML recovery, blocked export with an unconfigured provider and no page-width overflow at 1440 px and 390 px. Source metadata uses textContent; links use fixed known origins rather than user-supplied URL schemes.

## Verification actually performed

- Frozen-lock installation for CPython 3.10–3.15 was verified in separate environments during the compatibility step; the existing project .venv was preserved. The lockfile has not been upgraded during this review.
- Full suite, with disposable PostgreSQL/Redis: **112 passed on each of CPython 3.10, 3.11, 3.12, 3.13, 3.14 and 3.15**. Representative command: `HARMONIA_TEST_DATABASE_URL=postgresql+psycopg://harmonia_test:disposable_test_password@127.0.0.1:57432/harmonia_test HARMONIA_TEST_REDIS_URL=redis://127.0.0.1:57379/0 /private/tmp/harmonia-compat-3.10/bin/python -m pytest -q --disable-warnings`. Interpreter path and Redis database varied per version. Upstream deprecation warnings remain; no tests were disabled to obtain a passing result.
- `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `git diff --check`, `UV_CACHE_DIR=/private/tmp/harmonia-uv-cache uv lock --check`: passed.
- `DATABASE_URL=<disposable database> .venv/bin/alembic upgrade head` and `alembic check`: passed, including migration 0002 and the full-length key test.
- `BUILDX_CONFIG=/private/tmp/harmonia-buildx docker build -t harmonia:review .`: passed. The container uses the frozen lock and a non-root runtime.
- The built image also passed `playlist-worker --burst` with a read-only filesystem and `alembic check` against disposable PostgreSQL/Redis on the task's Docker network.
- Playwright browser interaction and screenshot inspection were performed against a local API and worker with disposable data. They do not prove external publication or constitute an accessibility certification.

## Remaining verification limits and risks

No live Spotify/YouTube Music/Yandex account was used and no external playlist was published. The Yandex SDK object-contract regression is verified locally, but region/account restrictions and unofficial endpoint behaviour remain account-level smoke-test requirements. PostgreSQL/Redis and job concurrency were exercised locally; the six Python versions were tested on macOS ARM64, with Linux checks configured in CI and the default Python 3.12 Linux Docker image built locally. Windows and free-threaded interpreter builds were not verified.

Remote writes still have a documented distributed failure window: a timeout or crash may leave a partial private playlist and a pending/uncertain journal entry. Such writes are not automatically retried or claimed to be exactly-once. This limitation is not hidden by a successful unit suite. Full token lifecycle, ongoing sync, local DSP and AI remain later iterations.

The blocking acceptance fixes were findings 1 and 2. They and the confirmed limited-impact findings above are closed. Remaining work is live-account release verification and the already declared later-iteration scope, not an outstanding confirmed P1 defect.
