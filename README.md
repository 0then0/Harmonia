<p align="center"><img src="src/harmonia/api/static/icon.svg" width="112" height="112" alt="Harmonia project icon"></p>

# Harmonia

[![CI](https://img.shields.io/github/actions/workflow/status/0then0/Harmonia/ci.yml?branch=main&label=checks)](https://github.com/0then0/Harmonia/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.15-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/0then0/Harmonia)](LICENSE)
[![Self hosted](https://img.shields.io/badge/self--hosted-PostgreSQL%20%2B%20Redis-167D8D)](compose.yml)

Harmonia is a self-hosted music library and playlist engine. Import playlists from Spotify, YouTube Music or Yandex Music, match recordings across catalogues, and build playlists from rules for duration, tempo, mood and artist variety.

The Python core uses a common music model independent of any service. A CLI, REST API and web workbench provide access to the same matching and compilation logic. AI accounts are not required, and Harmonia never downloads audio from streaming services.

## What you can do

- Import service playlists or canonical JSON files, preserving track order and duplicate occurrences.
- Compare recordings using ISRC, MusicBrainz identity, artist/title, version, duration and release metadata.
- Inspect confidence, reasons and alternatives, and save manual corrections.
- Compile repeatable playlists from YAML constraints and an existing library.
- Preview transfers and create a new private playlist in a destination service.
- Run the compiler offline, or host the workbench with PostgreSQL and Redis.

Version 0.1 supports one trusted operator and one configured account per service. Existing-playlist synchronization, browser-based account linking, automatic token refresh, local audio analysis and AI interfaces are not available. BPM, key and mood can be supplied in canonical JSON; provider imports do not calculate them. See [working with playlists](docs/usage.md) for the supported workflow and limits.

## Quick start: compile offline

Install [uv](https://docs.astral.sh/uv/), then run these commands from the repository root:

```sh
uv sync --frozen
uv run playlist build examples/night-drive.yml \
  --library examples/library.json --output night-drive.json
```

The result is a canonical JSON playlist. This command needs no database, Redis or provider credentials. The example library contains fictional tracks and illustrative features. It may produce less than the requested 90 minutes because the artist and duration rules limit the available selection; warnings in the result identify substantial shortfalls.

Supported interpreters are CPython 3.10–3.15 with the standard GIL. CI tests each minor version on Linux. Use the committed lockfile for reproducible installations; the [Dockerfile](Dockerfile) pins the container runtime separately.

## Quick start: web workbench

Install Docker with Compose, then prepare your configuration:

```sh
cp .env.example .env
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Generate two different values: put one in `HARMONIA_API_KEY` and the other in `POSTGRES_PASSWORD` in `.env`. Start the services:

```sh
docker compose up --build -d --wait
```

Open [the workbench](http://127.0.0.1:8000) and enter your operator API key. Select **Load sample library**, select it as a candidate library, and choose **Compile playlist**. The interface supports system, light and dark themes.

To use your own music metadata, upload canonical JSON or follow [provider setup](docs/providers.md) to configure a service and import a playlist. For a transfer, open **Match & export**, choose a source and destination, generate a preview, and resolve every uncertain match before creating the private playlist.

The default deployment binds the API to `127.0.0.1:8000`; PostgreSQL and Redis are not exposed on the host. Their data persists in Docker volumes. Read [operations](docs/operations.md) before remote deployment, upgrades or recovery from an interrupted export.

## Documentation

- [Working with playlists](docs/usage.md): canonical JSON, YAML constraints, CLI commands and match review.
- [Provider setup](docs/providers.md): credentials, supported references and service limitations.
- [REST API](docs/api.md): authentication, background jobs and request examples. Interactive [OpenAPI documentation](http://127.0.0.1:8000/docs) is available on a running server.
- [Operations](docs/operations.md): configuration, health checks, backups and export recovery.
- [Architecture](architecture.md): models, package boundaries and algorithm behaviour.
- [Testing](docs/testing.md): local checks, disposable integration services and CI coverage.
- [Contributing](CONTRIBUTING.md) and [adding a provider](docs/provider-development.md): development setup and extension contracts.

## License and attribution

Harmonia and its project icon are licensed under [Apache-2.0](LICENSE). Provider names and marks belong to their owners. The bundled Spotify logo comes from the [official brand asset pack](https://developer.spotify.com/documentation/design) and is subject to Spotify's branding terms rather than the project's Apache license. The workbench links track metadata to its source service and displays Spotify attribution for Spotify content.
