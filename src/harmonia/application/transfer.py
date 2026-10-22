from difflib import SequenceMatcher

from harmonia.domain.matching import Match, MatchResult, match_track
from harmonia.domain.models import Model, Playlist, Track
from harmonia.providers.base import Provider, ProviderError, track_ids


class DiffLine(Model):
    action: str
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()


def playlist_diff(before: Playlist, after: Playlist) -> tuple[DiffLine, ...]:
    a, b = [t.id for t in before.tracks], [t.id for t in after.tracks]
    return tuple(
        DiffLine(
            action={"equal": "keep", "insert": "add", "delete": "remove", "replace": "replace"}[op],
            before=tuple(a[i:j]),
            after=tuple(b[k:end]),
        )
        for op, i, j, k, end in SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    )


class TransferPreview(Model):
    source: Playlist
    provider: str
    matches: tuple[MatchResult, ...]
    ready: bool


async def preview_transfer(
    source: Playlist, provider: Provider, store=None, musicbrainz=None
) -> TransferPreview:
    matches = []
    for track in source.tracks:
        confirmed = await store.get_mapping(track, provider.name) if store else None
        if confirmed and confirmed.provider_id(provider.name):
            matches.append(
                MatchResult(
                    match=Match(track=confirmed, confidence=1, reasons=("manual_override",)),
                    needs_review=False,
                )
            )
            continue
        if track.provider_id(provider.name):
            matches.append(
                MatchResult(
                    match=Match(track=track, confidence=1, reasons=("same_provider_id",)),
                    needs_review=False,
                )
            )
            continue
        candidates = []
        metadata_issue = None
        recording = track.recording
        if recording.isrc and provider.capabilities.isrc_search:
            candidates.extend(await provider.search(recording.isrc, isrc=True))
        # MusicBrainz adds an independent identity to candidates when supplied by
        # the caller. No arbitrary first search result becomes an authoritative ID.
        if musicbrainz and recording.isrc:
            try:
                mb_tracks = await musicbrainz.by_isrc(recording.isrc)
                mb_result = match_track(track, mb_tracks)
                if mb_result.match and not mb_result.needs_review:
                    mbid = mb_result.match.track.recording.musicbrainz_id
                    track = track.model_copy(
                        update={"recording": recording.model_copy(update={"musicbrainz_id": mbid})}
                    )
                elif mb_tracks:
                    metadata_issue = "musicbrainz_identity_requires_review"
            except ProviderError:
                metadata_issue = "musicbrainz_unavailable_requires_review"
        query = " ".join(a.name for a in recording.artists) + " " + recording.title
        candidates.extend(await provider.search(query))
        result = match_track(track, [c for c in candidates if c.provider_id(provider.name)])
        if metadata_issue:
            result = result.model_copy(
                update={"needs_review": True, "reasons": (*result.reasons, metadata_issue)}
            )
        matches.append(result)
    return TransferPreview(
        source=source,
        provider=provider.name,
        matches=tuple(matches),
        ready=all(m.match and not m.needs_review for m in matches),
    )


async def export_preview(preview: TransferPreview, provider: Provider, store, key: str) -> Playlist:
    if (
        preview.provider != provider.name
        or not preview.ready
        or len(preview.matches) != len(preview.source.tracks)
        or any(m.match is None or m.needs_review for m in preview.matches)
    ):
        raise ProviderError("all matches must be reviewed before export")
    tracks = [m.match.track for m in preview.matches if m.match]
    return await export_tracks(preview.source.title, tracks, provider, store, key)


async def export_tracks(
    title: str, tracks: list[Track], provider: Provider, store, key: str
) -> Playlist:
    # A persisted claim precedes external I/O. Pending/uncertain claims are never
    # replayed automatically. This intentionally favours no duplicates over liveness.
    track_ids(provider.name, tracks)
    payload = {
        "title": title,
        "tracks": [t.model_dump(mode="json") for t in tracks],
        "provider": provider.name,
    }
    previous = await store.claim_operation(key, payload)
    if previous:
        return previous
    try:
        result = await provider.create_playlist(title, tracks)
    except Exception:
        await store.finish_operation(key, "uncertain")
        raise
    await store.finish_operation(key, "succeeded", result)
    return result
