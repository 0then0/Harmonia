# Provider setup

Service adapters translate catalogue metadata into Harmonia's canonical model. Spotify uses its Web API; YouTube Music and Yandex Music use unofficial SDKs. MusicBrainz supplies independent recording and release metadata.

The offline compiler needs no provider credentials. For service operations, configure credentials on the server and restart both API and worker:

```sh
docker compose up -d --force-recreate api worker
```

The **Connections** tab shows which adapters are configured and their supported operations. **Read playlists** checks account access. A configured adapter is not necessarily a valid or authorized account connection.

## Spotify

Set `SPOTIFY_ACCESS_TOKEN` in `.env` to an existing user OAuth access token. Playlist reading and private playlist creation use `playlist-read-private`, `playlist-read-collaborative` and `playlist-modify-private` scopes as applicable to the account and app.

Accepted playlist references are a playlist ID, `spotify:playlist:ID`, or an `https://open.spotify.com/playlist/ID` URL.

The adapter supports search, ISRC search, playlist listing/import and private playlist creation. Its bounded replacement method is available to Python callers, but existing-playlist updates are not exposed by the API or CLI. Replacement can also set track order. Likes are not implemented; the API and CLI expose creation only.

Harmonia does not run an OAuth redirect flow or refresh tokens. Replace expired access tokens in server configuration and restart API and worker. App approval, quota mode, scopes and ownership restrictions determine which playlists can be read; a public URL alone does not establish access.

See the official [Web API documentation](https://developer.spotify.com/documentation/web-api) and [February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide) for account and endpoint requirements.

## YouTube Music

Create a browser authentication file using the [ytmusicapi setup instructions](https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html). Store it outside source control, for example at `private/browser.json`.

In `.env`, set:

```dotenv
YOUTUBE_AUTH_FILE=/run/providers/browser.json
```

Mount the file read-only into both API and worker with a `compose.override.yml`:

```yaml
services:
  api:
    volumes:
      - ./private/browser.json:/run/providers/browser.json:ro
  worker:
    volumes:
      - ./private/browser.json:/run/providers/browser.json:ro
```

The file must be readable by container UID 10001. It grants account access; do not share it in issues, logs or fixtures.

Accepted references are a playlist ID or a `youtube.com/playlist?list=ID` or `music.youtube.com/playlist?list=ID` URL. Search, playlist listing/import and private playlist creation are supported. ISRC search, likes, updates, reorder and OAuth authentication are not implemented by this adapter. The server enables YouTube Music only when `YOUTUBE_AUTH_FILE` is configured.

The SDK is unofficial. Account/session changes or changes to YouTube Music can require a new authentication file or an SDK update.

## Yandex Music

Set `YANDEX_TOKEN` in `.env` to an existing account token compatible with the [yandex-music SDK](https://github.com/MarshalX/yandex-music-api). Token acquisition and refresh are managed outside Harmonia.

Use `owner:kind` (profile login and numeric playlist kind) or a `https://music.yandex.ru/users/owner/playlists/kind` URL as the playlist reference. Opaque UUID playlist URLs are not supported.

The adapter supports search, playlist listing/import and private playlist creation. It retains the track and album IDs required for publication. ISRC search, likes, updates, reorder and OAuth authentication are not implemented by this adapter. Service availability depends on account permissions and region; the SDK is unofficial.

## MusicBrainz

Set `MUSICBRAINZ_USER_AGENT` to identify your deployment and include a contact URL, for example:

```dotenv
MUSICBRAINZ_USER_AGENT=Harmonia/0.1.0 (https://example.org/music/contact)
```

Supported operations are ISRC lookup and lookup by recording, artist, release or release-group UUID. MusicBrainz records provide metadata rather than playable destination IDs. A missing ISRC result is a normal metadata absence; ambiguous identity requires review.

Results, including missing ISRC results, are cached for 24 hours. Requests are spaced by at least 1.05 seconds across API and worker using Redis. Deployments sharing a public egress IP should share the limiter namespace and Redis. Review [MusicBrainz API usage and licensing](https://musicbrainz.org/doc/MusicBrainz_API) before deployment.

## Data and access boundaries

Adapters retrieve metadata and publish playlists. They do not request streaming audio, Spotify audio analysis, YouTube streams or Yandex download URLs. Feature values must come from supplied canonical metadata.

Credentials belong in environment variables or read-only files, never in canonical JSON. The API uses one configured account per service. Verify read/search access first, then test publication with a small disposable private playlist. Automated provider contracts use mocked responses and do not verify a particular account's permissions.

To add another service, follow [the provider development guide](provider-development.md). The matcher and compiler need no service-specific changes.
