# Adding a provider

A provider translates a service catalogue into canonical music metadata and implements supported playlist operations. Add an adapter under `src/harmonia/providers/`, then register it in runtime and presentation code. The domain matcher and compiler remain unchanged.

## Contract and capabilities

Subclass [`Provider`](../src/harmonia/providers/base.py) and implement these async methods:

- `search(query, *, isrc=False)`: return canonical Track candidates.
- `list_playlists()`: return playlist summaries accessible to the account.
- `read_playlist(reference)`: return the complete ordered snapshot with warnings for omitted entries.
- `create_playlist(title, tracks)`: create a private playlist from destination IDs.
- `close()`: release owned clients or sessions when needed.

Declare every `Capabilities` flag to describe implemented adapter operations. Defaults enable search, playlist reads and creation; all other flags default to false. Call `require()` before an unsupported optional operation. ISRC search must be an actual ISRC query rather than a text-search approximation.

The `oauth` flag means that the adapter accepts OAuth access tokens; it does not imply that Harmonia manages authorization redirects, token storage or refresh. Likes have no shared public methods yet and should remain false unless the interface and its callers are extended together.

Declare optional operations explicitly, for example:

```python
from harmonia.providers.base import Capabilities

capabilities = Capabilities(isrc_search=False, oauth=False)
```

Use the existing adapters as complete examples: [Spotify](../src/harmonia/providers/spotify.py) for async HTTP, [YouTube Music](../src/harmonia/providers/youtube.py) for a blocking SDK, and [Yandex Music](../src/harmonia/providers/yandex.py) for an async SDK.

## Metadata conversion

Convert responses to Artist, Recording, Release and Track at the adapter boundary. Preserve all credited artists, duration in milliseconds, version descriptors, release context, service IDs/URLs, explicit status and availability. Preserve album IDs when publication requires them. Keep missing values as `None`; do not invent ISRC, BPM or other features.

Imports must retain order and duplicate occurrences. Resolve abbreviated SDK records into full metadata where needed. Report omitted unavailable/null/non-music entries in `Playlist.warnings`. Implement complete pagination with bounded safety limits; exceeding a limit should fail visibly rather than return an incomplete playlist as complete.

Adapters retrieve metadata only. They must not download streaming audio or invoke service audio-analysis endpoints.

## Transport and write safety

Prefer async HTTP or an async SDK. For blocking SDKs, serialize access to shared sessions and use `asyncio.to_thread`. Use explicit timeouts, bounded read retries and Retry-After where available. Existing helpers are in [`infrastructure/http.py`](../src/harmonia/infrastructure/http.py) and [`providers/sdk.py`](../src/harmonia/providers/sdk.py).

Validate service references into identifiers before constructing requests. Use fixed provider origins; never forward authorization headers to an untrusted pagination URL. Convert SDK failures into sanitized `ProviderError` messages without tokens, raw response bodies or authorization headers.

Validate all destination IDs before the first write and create playlists privately. A timed-out create or append may have succeeded: do not automatically retry it. Document multi-call partial-success behaviour. Existing-playlist mutations need explicit revision/conflict handling; the current API and CLI expose only creation.

## Registration

1. Add credential settings in [`config.py`](../src/harmonia/infrastructure/config.py), keeping secrets out of repr/logging.
2. Construct and close the adapter in [`runtime.py`](../src/harmonia/infrastructure/runtime.py).
3. Register its name, reference validation and authentication description in [`api/app.py`](../src/harmonia/api/app.py).
4. Add it to the CLI choices and workbench service selectors. Update track backlinks and attribution if the service requires them.
5. Update [provider setup](providers.md), `.env.example` and package dependencies/lockfile when required.

These are integration boundaries; no provider-specific condition is needed in `domain/`.

## Tests and documentation

Extend [`tests/test_providers.py`](../tests/test_providers.py) with shared contract cases and service-specific fixtures. Cover pagination, duplicates, missing metadata, unavailable entries, authentication failures, reference validation, retries, write preconditions and partial failures. Validate SDK payload shapes against the supported SDK rather than mocking a different object type.

Document accepted reference syntax, credentials/scopes, official or unofficial status, account/region restrictions and every capability limit. Run [local and integration checks](testing.md). Live mutation tests require an authorized account and disposable private playlists; mocked tests alone do not establish upstream access.
