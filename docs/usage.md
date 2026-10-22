# Working with playlists

Harmonia uses canonical JSON to move music metadata between services and the compiler. A playlist snapshot contains track order, recording and release metadata, provider IDs and any supplied audio features. It contains no audio files.

## Import a library

For offline use, provide one or more canonical JSON files. For the workbench, enter the operator key, then import a configured service playlist or upload JSON. The bundled **Load sample library** action imports fictional tracks for learning the workflow.

Server CLI commands need a running API and worker, plus an exported operator key:

```sh
export HARMONIA_API_KEY='your-operator-key'
export HARMONIA_API_URL='http://127.0.0.1:8000/'
uv run playlist import spotify 'https://open.spotify.com/playlist/PLAYLIST_ID' -o imported.json
uv run playlist import youtube 'https://music.youtube.com/playlist?list=PLAYLIST_ID' -o imported.json
uv run playlist import yandex 'owner:kind' -o imported.json
```

Choose the command for your configured service. See [provider setup](providers.md) for accepted references and credentials. Copying `.env` configures Compose but does not export variables into a host shell.

Imports preserve duplicate track occurrences. Unavailable or non-music entries that cannot be represented are reported in the playlist's `warnings`. A stored snapshot is independent of later edits in the remote service; import again to refresh it.

## Canonical JSON

This minimal library can be compiled without a provider:

```json
{
  "schema_version": 1,
  "id": "local:library",
  "title": "My library",
  "tracks": [
    {
      "id": "local:harbour-lights",
      "recording": {
        "title": "Harbour Lights",
        "artists": [{"name": "Example Ensemble"}],
        "duration_ms": 240000
      },
      "explicit": false,
      "features": {"bpm": 90, "key": "D", "scale": "minor", "energy": 0.3}
    }
  ]
}
```

Durations are milliseconds. Energy and mood values range from 0 to 1; beat positions, when supplied, are seconds. Unknown features remain absent or `null`. Use `features.provenance` to identify the source of supplied measurements. Imported provider metadata does not include calculated BPM, key, energy or mood.

Optional `album` metadata describes the release. `recording.isrc` and `recording.musicbrainz_id` identify recordings; `providers` holds service-specific IDs and availability. The full model is defined in [models.py](../src/harmonia/domain/models.py) and exposed in OpenAPI. Schema version 1 rejects unknown fields. A playlist can hold up to 10,000 track occurrences, while compilation and transfer have smaller limits below.

## Compile a playlist

```sh
uv run playlist build examples/night-drive.yml \
  --library imported.json --output night-drive.json
```

Repeat `--library` to combine candidate sources. In the workbench, check the libraries to use, edit the YAML and choose **Compile playlist**. Compilation selects and orders tracks from these candidates; it does not search a service for additional music.

The [complete example](../examples/night-drive.yml) includes tempo, energy and mood curves. A smaller configuration is:

```yaml
title: Night drive
duration: 90m
random_seed: 42
seed:
  artists: [Massive Attack, Portishead]
tempo:
  start: 85
  end: 125
rules:
  max_tracks_per_artist: 2
  artist_gap: 1
  avoid_live: true
  avoid_remixes: true
  explicit: false
transitions:
  bpm_smoothness: 0.8
  harmonic_key: 0.7
```

### Constraints and preferences

`duration` accepts integer seconds or an integer with `s`, `m` or `h` suffix, from 30 seconds to 12 hours. Total duration never exceeds this target. If the library cannot fill it under the rules, the result is shorter. Results below 95% of the target include an underfill warning.

`seed.artists` favours those artists; it does not exclude everyone else. `random_seed` controls deterministic tie-breaking. The same constraints, seed and canonical candidate data produce the same result, regardless of candidate input order.

`tempo` and `energy` accept `start`, `end` and optional `middle` values. Tempo ranges from 20 to 400 BPM and energy from 0 to 1. Positions follow elapsed time relative to the requested duration. `mood` uses label strings matched to keys in each track's `features.mood` probabilities. Without `middle`, start/end labels apply to the first/second halves; when supplied, the middle label applies to the middle third.

Rules enforce the following:

- `max_tracks_per_artist` limits every credited artist, including collaborations; the default is 2.
- `artist_gap` sets how many preceding tracks must not share an artist; the default is 1, and 0 disables separation.
- `avoid_live` and `avoid_remixes` default to `true`.
- `explicit: false` accepts only tracks explicitly marked clean; unknown explicit status is excluded. Omit the field or use `null` to allow either status. `true` selects only tracks explicitly marked explicit.
- `missing_features: allow` retains tracks with unknown requested features and reports relevant warnings. `exclude` requires BPM for a tempo curve, energy for an energy curve, a nonempty mood map for a mood curve, and key/scale when harmonic weighting is nonzero. It does not require embeddings or genre labels.

Transition weights range from 0 to 1: `bpm_smoothness`, `harmonic_key` and `genre_diversity` influence ordering. `semantic_similarity` must be 0 because embedding-based scoring is not implemented.

Compilation accepts at most 1,000 candidates. `max_tracks` defaults to 100 and is limited to 200. `beam_width` defaults to 12 and ranges from 1 to 32. The optimizer combines candidate filtering, beam search and bounded ordering refinement; it does not guarantee a globally optimal playlist. Check the resulting duration and warnings before exporting.

## Review catalogue matches

For a transfer preview:

```sh
uv run playlist diff night-drive.json --to spotify
# Equivalent preview without publishing:
uv run playlist export night-drive.json --to spotify
```

Both commands accept a compiled JSON snapshot, not a YAML configuration. A preview stores the snapshot and searches the destination catalogue; it does not create a remote playlist. Transfer previews support at most 200 tracks.

Harmonia prioritizes ISRC and MusicBrainz recording identity, then considers artist/title, edition, duration and album evidence. Different live, remix, remaster, acoustic and other version types remain distinct. Different named remixes, live venues or remaster years require review even when an identifier agrees. Close alternatives and insufficient or contradictory evidence also require review. Confidence scores are heuristic scores, not probabilities that a recording is correct.

In **Match & export**, inspect each row's reasons and alternatives. Search the destination catalogue and choose **Confirm mapping** when you have identified the intended recording. Generate a new preview after corrections. Confirmed mappings persist for the source track and destination provider, and may intentionally select a different edition.

The CLI accepts individual canonical Track files for a correction:

```sh
uv run playlist confirm source-track.json chosen-target.json --to spotify
```

To compare one Track against an offline library without the server:

```sh
uv run playlist match source-track.json --library target-library.json
```

## Publish a private playlist

Once all matches are resolved:

```sh
uv run playlist export night-drive.json --to spotify \
  --apply --idempotency-key night-drive-v1
```

Use `youtube` or `yandex` as the destination for those services. Export always creates a new private playlist. It does not replace, reorder or delete an existing remote playlist.

An idempotency key identifies one export payload. Reusing it with the same payload returns the recorded successful result; using it with a different payload fails. If a timeout or crash interrupts publication, a partial playlist may already exist. Inspect the remote library and the operation journal before attempting another export. [Export recovery](operations.md#export-recovery) explains the journal states.
