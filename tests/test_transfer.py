from unittest.mock import AsyncMock

import pytest

from harmonia.application.transfer import (
    export_preview,
    export_tracks,
    playlist_diff,
    preview_transfer,
)
from harmonia.domain.models import Playlist
from harmonia.infrastructure.store import Conflict
from harmonia.providers.base import Capabilities, ProviderError


class FakeProvider:
    name = "spotify"
    capabilities = Capabilities(isrc_search=True)
    search = AsyncMock()
    create_playlist = AsyncMock()


async def test_preview_isrc_then_metadata_search_and_no_writes(make_track):
    source = make_track(isrc="GBBKS9800001")
    target = make_track(id="target", isrc="GBBKS9800001", provider="spotify")
    provider = FakeProvider()
    provider.search = AsyncMock(side_effect=[[target], [target]])
    provider.create_playlist = AsyncMock()
    result = await preview_transfer(Playlist(id="p", title="Test", tracks=(source,)), provider)
    assert result.ready
    assert provider.search.await_args_list[0].kwargs == {"isrc": True}
    provider.create_playlist.assert_not_awaited()


async def test_override_short_circuits_search_and_allows_explicit_version_change(make_track):
    source = make_track()
    target = make_track(id="target", title="Teardrop (Remaster)", provider="spotify")
    provider = FakeProvider()
    provider.search = AsyncMock()
    store = AsyncMock()
    store.get_mapping.return_value = target
    result = await preview_transfer(
        Playlist(id="p", title="Test", tracks=(source,)), provider, store
    )
    assert result.ready
    assert result.matches[0].match.reasons == ("manual_override",)
    provider.search.assert_not_awaited()


async def test_same_provider_track_preserves_occurrences(make_track):
    track = make_track(provider="spotify")
    provider = FakeProvider()
    provider.search = AsyncMock()
    preview = await preview_transfer(
        Playlist(id="p", title="Test", tracks=(track, track)), provider
    )
    assert len(preview.matches) == 2 and preview.ready
    provider.search.assert_not_awaited()


async def test_low_confidence_blocks_export(make_track):
    provider = FakeProvider()
    provider.search = AsyncMock(return_value=[])
    preview = await preview_transfer(
        Playlist(id="p", title="Test", tracks=(make_track(),)), provider
    )
    with pytest.raises(ProviderError):
        await export_preview(preview, provider, AsyncMock(), "key")


async def test_write_journal_success_replay_and_uncertainty(make_track):
    provider = FakeProvider()
    target = make_track(provider="spotify")
    playlist = Playlist(id="new", title="Export", tracks=(target,), provider="spotify")
    provider.create_playlist = AsyncMock(return_value=playlist)
    store = AsyncMock()
    store.claim_operation.return_value = None
    assert await export_tracks("Export", [target], provider, store, "key") == playlist
    store.finish_operation.assert_awaited_once_with("key", "succeeded", playlist)
    store.claim_operation.return_value = playlist
    assert await export_tracks("Export", [target], provider, store, "key") == playlist
    assert provider.create_playlist.await_count == 1
    store.claim_operation.return_value = None
    provider.create_playlist.side_effect = ProviderError("timeout")
    with pytest.raises(ProviderError):
        await export_tracks("Export", [target], provider, store, "new-key")
    store.finish_operation.assert_awaited_with("new-key", "uncertain")
    store.claim_operation.side_effect = Conflict("pending")
    with pytest.raises(Conflict):
        await export_tracks("Export", [target], provider, store, "new-key")
    assert provider.create_playlist.await_count == 2


def test_diff_preserves_duplicates_and_order(make_track):
    a, b = make_track(id="a"), make_track(id="b")
    before = Playlist(id="p", title="Test", tracks=(a, a, b))
    after = Playlist(id="p", title="Test", tracks=(b, a))
    lines = playlist_diff(before, after)
    assert any(line.action == "remove" for line in lines)
    assert any(line.action == "add" for line in lines)
    assert playlist_diff(after, after)[0].action == "keep"


async def test_musicbrainz_enrichment_prevents_conflicting_recording_match(make_track):
    source = make_track(isrc="GBBKS9800001")
    mb = AsyncMock()
    mb.by_isrc.return_value = [make_track(id="mb", isrc="GBBKS9800001", mbid="correct-recording")]
    target = make_track(
        id="target", isrc="GBBKS9800001", mbid="different-recording", provider="spotify"
    )
    provider = FakeProvider()
    provider.search = AsyncMock(return_value=[target])
    preview = await preview_transfer(
        Playlist(id="p", title="Test", tracks=(source,)), provider, musicbrainz=mb
    )
    assert not preview.ready and preview.matches[0].match is None


@pytest.mark.parametrize("state", ["ambiguous", "unavailable", "missing"])
async def test_musicbrainz_uncertainty_remains_visible_without_blocking_preview(make_track, state):
    source = make_track(isrc="GBBKS9800001")
    mb = AsyncMock()
    mb.by_isrc.return_value = []
    if state == "ambiguous":
        mb.by_isrc.return_value = [
            make_track(id=f"mb:{i}", isrc="GBBKS9800001", mbid=str(i)) for i in range(2)
        ]
    elif state == "unavailable":
        mb.by_isrc.side_effect = ProviderError("temporary error")
    target = make_track(id="target", isrc="GBBKS9800001", provider="spotify")
    provider = FakeProvider()
    provider.search = AsyncMock(return_value=[target])
    preview = await preview_transfer(
        Playlist(id="p", title="Test", tracks=(source,)), provider, musicbrainz=mb
    )
    assert preview.matches[0].match is not None
    if state == "missing":
        assert preview.ready
    else:
        assert not preview.ready
        assert any("musicbrainz" in reason for reason in preview.matches[0].reasons)


async def test_unavailable_candidates_cannot_make_preview_ready(make_track):
    from harmonia.domain.models import ProviderTrack

    source = make_track(isrc="GBBKS9800001")
    unavailable = make_track(
        id="unavailable", isrc=source.recording.isrc, provider="spotify"
    ).model_copy(
        update={"providers": (ProviderTrack(provider="spotify", id="u", available=False),)}
    )
    available = make_track(id="available", provider="spotify")
    provider = FakeProvider()
    provider.search = AsyncMock(return_value=[unavailable, available])
    preview = await preview_transfer(
        Playlist(id="source", title="Test", tracks=(source,)), provider
    )
    assert preview.ready and preview.matches[0].match.track.id == "available"
    provider.search = AsyncMock(return_value=[unavailable])
    assert not (await preview_transfer(preview.source, provider)).ready


async def test_invalid_export_does_not_reserve_idempotency_key(make_track):
    store = AsyncMock()
    provider = FakeProvider()
    with pytest.raises(ProviderError):
        await export_tracks("Invalid", [make_track()], provider, store, "key")
    store.claim_operation.assert_not_awaited()


async def test_ready_flag_cannot_hide_missing_or_unreviewed_matches(make_track):
    from harmonia.application.transfer import TransferPreview
    from harmonia.domain.matching import Match, MatchResult

    source = Playlist(id="p", title="Test", tracks=(make_track(),))
    provider = FakeProvider()
    store = AsyncMock()
    for matches in [
        (),
        (MatchResult(),),
        (
            MatchResult(
                match=Match(track=make_track(provider="spotify"), confidence=0.8, reasons=()),
                needs_review=True,
            ),
        ),
    ]:
        preview = TransferPreview(source=source, provider="spotify", matches=matches, ready=True)
        with pytest.raises(ProviderError):
            await export_preview(preview, provider, store, "key")
    store.claim_operation.assert_not_awaited()
