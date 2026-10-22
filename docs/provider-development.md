# Adding a provider

Core algorithms consume canonical data and do not know Spotify, YouTube Music or Yandex endpoints. Add a module under `src/harmonia/providers/`; do not import the new SDK from `domain/` or add provider branches to the matcher/compiler.

1. Subclass `Provider` from `providers/base.py`. Implement `search`, `list_playlists`, `read_playlist`, `create_playlist` and optional `close` cleanup. All public I/O methods are async. Use an async HTTP library where possible; if a required SDK blocks, serialize its session and run it with `asyncio.to_thread` as the YouTube adapter does.
2. Declare every `Capabilities` flag honestly. Here capabilities describe **implemented adapter operations**, not everything the upstream service can theoretically do. `oauth` means the adapter accepts OAuth bearer access tokens; it does not promise a redirect/refresh UI. Likes and ISRC search remain false until implemented and tested. Use `require()` to reject unsupported requests rather than substituting a text search for an ISRC query.
3. Convert the upstream response to Artist/Recording/Release/Track. Preserve missing fields as `None`, service track ID/link, explicit/availability flags, all credited artists, duration in milliseconds, version descriptors and release context. Do not infer ISRC or audio features from unrelated fields. Keep service album IDs when writes require them. Skip unavailable/null/non-music items with visible warnings rather than returning a shortened playlist silently.
4. Implement complete pagination with bounded safety limits. Never silently import just the first page. Do not follow untrusted pagination URLs with authorization headers outside the fixed provider origin. Validate URL/URI references into an identifier before constructing a request.
5. Use explicit HTTP timeouts. Retry bounded safe reads with exponential backoff and Retry-After when available. Never retry non-idempotent creates/appends on timeout. Sanitize SDK exceptions; tokens and raw response bodies must not enter API responses or logs. Create playlists privately. Validate all target IDs before the first write. Document partial-success behaviour.
6. Wire credentials and construction in `infrastructure/config.py` and `runtime.py`. Add the provider name to API validation and the UI/CLI selection lists. This is the composition/presentation layer, **not core**. No plugin registry, dynamic loading or new architectural package is needed for three adapters.
7. Extend the shared provider contract tests in `tests/test_providers.py` with a fake/MockTransport and representative upstream responses. Test pagination, unavailable entries, missing optional metadata, all write preconditions, revision conflicts if supported, auth failures, timeout/retry and malformed data. Test SDK payloads against the installed SDK's signatures. Live mutation tests require a deliberately disposable private playlist and explicit authorization.
8. Document credentials, scopes, unofficial API status, restrictions, capability limits and supported reference syntax. Add new dependencies only when approved. Update the lockfile intentionally.

A minimal adapter has this shape:

```python
from harmonia.providers.base import Capabilities, Provider


class ExampleProvider(Provider):
    name = "example"
    capabilities = Capabilities(isrc_search=False, oauth=False)

    async def search(self, query, *, isrc=False):
        if isrc:
            self.require("isrc_search")
        # GET metadata using a fixed origin, then return canonical Track objects.
        ...

    async def list_playlists(self): ...
    async def read_playlist(self, reference): ...
    async def create_playlist(self, title, tracks): ...
```

The ellipses above illustrate the boundary only; a real adapter must implement every operation it declares. Apple Music, TIDAL, Deezer, Qobuz and Amazon Music can use this same contract without modifying domain code. Do not promise upstream access until its authorization and catalogue APIs have been verified.
