from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from harmonia.domain.models import Playlist
from harmonia.infrastructure.http import HTTP, MemoryCache
from harmonia.infrastructure.musicbrainz import MusicBrainz
from harmonia.providers.base import ProviderError, UnsupportedCapability
from harmonia.providers.spotify import Spotify
from harmonia.providers.yandex import YandexMusic
from harmonia.providers.youtube import YouTubeMusic

SPOTIFY_TRACK = {
    "id": "s1",
    "name": "Teardrop",
    "duration_ms": 330000,
    "artists": [{"id": "a1", "name": "Massive Attack"}],
    "album": {"id": "r1", "name": "Mezzanine"},
    "external_ids": {"isrc": "GBBKS9800001"},
    "explicit": False,
    "type": "track",
}
YT_TRACK = {
    "videoId": "v1",
    "title": "Teardrop",
    "artists": [{"name": "Massive Attack", "id": "a1"}],
    "album": {"name": "Mezzanine"},
    "duration": "5:30",
    "isExplicit": False,
}
YA_TRACK = {
    "id": "123",
    "title": "Teardrop",
    "duration_ms": 330000,
    "artists": [{"id": 1, "name": "Massive Attack"}],
    "albums": [{"id": 99, "title": "Mezzanine"}],
    "available": True,
}


@pytest.fixture
def provider_case(request):
    provider = request.param
    if provider == "spotify":

        async def handler(req):
            path = req.url.path
            if path.endswith("/search"):
                return httpx.Response(200, json={"tracks": {"items": [SPOTIFY_TRACK]}})
            if path.endswith("/me/playlists"):
                return (
                    httpx.Response(
                        200,
                        json={
                            "items": [{"id": "p1", "name": "Test", "snapshot_id": "r"}],
                            "next": None,
                        },
                    )
                    if req.method == "GET"
                    else httpx.Response(201, json={"id": "new", "snapshot_id": "new-r"})
                )
            if path.endswith("/items"):
                return (
                    httpx.Response(200, json={"items": [{"item": SPOTIFY_TRACK}], "next": None})
                    if req.method == "GET"
                    else httpx.Response(201, json={"snapshot_id": "new-r"})
                )
            return httpx.Response(200, json={"id": "p1", "name": "Test", "snapshot_id": "r"})

        client = httpx.AsyncClient(
            base_url="https://api.spotify.com/v1/", transport=httpx.MockTransport(handler)
        )
        return Spotify("fake", client), "p1"
    if provider == "youtube":
        client = NS(
            search=Mock(return_value=[YT_TRACK]),
            get_library_playlists=Mock(return_value=[{"playlistId": "p1", "title": "Test"}]),
            get_playlist=Mock(return_value={"title": "Test", "tracks": [YT_TRACK]}),
            create_playlist=Mock(return_value="new"),
        )
        return YouTubeMusic(client=client), "p1"
    from yandex_music import TrackShort

    owner = NS(uid=7)
    track = NS(id="123", title="Teardrop", to_dict=lambda: YA_TRACK, artists=[1])
    playlist = NS(
        kind=3,
        title="Test",
        owner=owner,
        revision=1,
        tracks=[TrackShort(id="123", timestamp="2026-01-01T00:00:00Z", album_id="99")],
    )
    client = NS(
        init=AsyncMock(),
        account_status=AsyncMock(),
        search=AsyncMock(return_value=NS(tracks=NS(results=[track]))),
        users_playlists_list=AsyncMock(return_value=[playlist]),
        users_playlists=AsyncMock(return_value=playlist),
        tracks=AsyncMock(return_value=[track]),
        users_playlists_create=AsyncMock(return_value=playlist),
        users_playlists_insert_track=AsyncMock(return_value=playlist),
    )
    return YandexMusic("fake", client), "7:3"


@pytest.mark.parametrize("provider_case", ["spotify", "youtube", "yandex"], indirect=True)
async def test_provider_contract(provider_case):
    provider, ref = provider_case
    found = await provider.search("Massive Attack Teardrop")
    assert found and found[0].recording.title == "Teardrop"
    assert found[0].recording.duration_ms == 330000
    assert found[0].provider_id(provider.name)
    assert all(p.provider == provider.name for p in found[0].providers)
    playlists = await provider.list_playlists()
    assert playlists[0].provider == provider.name
    imported = await provider.read_playlist(ref)
    assert len(imported.tracks) == 1
    exported = await provider.create_playlist("New", found)
    assert exported.title == "New" and exported.tracks == tuple(found)
    if not provider.capabilities.isrc_search:
        with pytest.raises(UnsupportedCapability):
            await provider.search("GBBKS9800001", isrc=True)
    if isinstance(provider, Spotify):
        await provider.client.aclose()


@pytest.mark.parametrize(
    "adapter,value",
    [
        (Spotify, "https://evil.example/playlist/xx"),
        (Spotify, "../../users/me"),
        (YouTubeMusic, "https://evil.example/?list=PL123"),
        (YandexMusic, "https://music.yandex.ru/playlists/not-a-kind"),
    ],
)
def test_invalid_references(adapter, value):
    with pytest.raises(ValueError):
        adapter.reference(value)


def test_reference_urls_and_duration():
    assert Spotify.reference("https://open.spotify.com/playlist/abc?si=xyz") == "abc"
    assert Spotify.reference("spotify:playlist:abc") == "abc"
    assert YouTubeMusic.reference("https://music.youtube.com/playlist?list=PL_test") == "PL_test"
    assert YandexMusic.reference("https://music.yandex.ru/users/listener/playlists/7") == (
        "listener",
        "7",
    )
    assert YouTubeMusic.track({**YT_TRACK, "duration": "1:02:03"}).recording.duration_ms == 3723000
    assert "live" in YandexMusic.track({**YA_TRACK, "version": "Live"}).recording.versions


async def test_spotify_pagination_null_local_episode_and_current_item_field():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        if req.url.path.endswith("/items"):
            if req.url.params["offset"] == "0":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {"item": SPOTIFY_TRACK},
                            {"item": None},
                            {"item": {**SPOTIFY_TRACK, "type": "episode"}},
                            {"item": SPOTIFY_TRACK, "is_local": True},
                        ],
                        "next": "do-not-follow-foreign-url",
                    },
                )
            return httpx.Response(
                200, json={"items": [{"track": {**SPOTIFY_TRACK, "id": "s2"}}], "next": None}
            )
        return httpx.Response(200, json={"name": "Paged", "snapshot_id": "r"})

    async with httpx.AsyncClient(
        base_url="https://api.spotify.com/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        result = await Spotify("fake", client).read_playlist("p1")
    assert len(result.tracks) == 2
    assert result.warnings == ("skipped_unavailable_or_non_music:3",)
    assert any("offset=4" in s for s in seen)
    assert all(s.startswith("https://api.spotify.com/") for s in seen)


async def test_spotify_detects_import_revision_change():
    revisions = iter(["r1", "r2"])

    def handler(req):
        if req.url.path.endswith("/items"):
            return httpx.Response(200, json={"items": [], "next": None})
        return httpx.Response(200, json={"name": "Changed", "snapshot_id": next(revisions)})

    async with httpx.AsyncClient(
        base_url="https://api.spotify.com/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ProviderError, match="changed"):
            await Spotify("fake", client).read_playlist("p1")


async def test_transport_retries_reads_but_never_writes(monkeypatch):
    monkeypatch.setattr("harmonia.infrastructure.http.asyncio.sleep", AsyncMock())
    count = 0

    def handler(req):
        nonlocal count
        count += 1
        return (
            httpx.Response(503, json={"secret": "must not be exposed"})
            if count < 3
            else httpx.Response(200, json={"ok": True})
        )

    async with httpx.AsyncClient(
        base_url="https://example.org/", transport=httpx.MockTransport(handler)
    ) as client:
        assert await HTTP(client).request("GET", "test") == {"ok": True}
        assert count == 3
        count = 0
        with pytest.raises(ProviderError) as error:
            await HTTP(client).request("POST", "test")
        assert count == 1
        assert "secret" not in str(error.value)


async def test_musicbrainz_lookup_cache_and_entity_validation():
    cache = MemoryCache()
    cache.wait = AsyncMock()
    requests = []

    def handler(req):
        requests.append(req)
        if "/isrc/" in req.url.path:
            return httpx.Response(
                200,
                json={
                    "recordings": [
                        {
                            "id": "4f9da3e3-a0f1-4e86-a28c-345f3df36f37",
                            "title": "Teardrop",
                            "length": 330000,
                            "artist-credit": [
                                {"artist": {"name": "Massive Attack", "id": "artist"}}
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"id": "4f9da3e3-a0f1-4e86-a28c-345f3df36f37"})

    async with httpx.AsyncClient(
        base_url="https://musicbrainz.org/ws/2/", transport=httpx.MockTransport(handler)
    ) as client:
        mb = MusicBrainz(cache, "Harmonia tests (https://example.org)", client)
        first = await mb.by_isrc("GBBKS9800001")
        second = await mb.by_isrc("GBBKS9800001")
        assert first == second and len(requests) == 1
        assert first[0].recording.musicbrainz_id
        for entity in ["recording", "artist", "release", "release-group"]:
            await mb.lookup(entity, "4f9da3e3-a0f1-4e86-a28c-345f3df36f37")
        assert len(requests) == 5 and cache.wait.await_count == 5
        with pytest.raises(ValueError):
            await mb.lookup("evil", "4f9da3e3-a0f1-4e86-a28c-345f3df36f37")
        with pytest.raises(ValueError):
            await mb.by_isrc("../../private")


async def test_export_validates_all_ids_before_writing(make_track):
    writes = []

    def handler(req):
        writes.append(req)
        return httpx.Response(201, json={"id": "created"})

    async with httpx.AsyncClient(
        base_url="https://api.spotify.com/v1/", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ProviderError):
            await Spotify("fake", client).create_playlist("Test", [make_track()])
    assert not writes


async def test_spotify_replace_conflict_and_budget(make_track, monkeypatch):
    provider = Spotify("fake")
    monkeypatch.setattr(
        provider,
        "read_playlist",
        AsyncMock(return_value=Playlist(id="p1", title="Test", revision="r2")),
    )
    try:
        with pytest.raises(ProviderError, match="conflict"):
            await provider.replace_tracks(
                Playlist(id="p1", title="Test", revision="r1"), [make_track(provider="spotify")]
            )
        with pytest.raises(ProviderError, match="100"):
            await provider.replace_tracks(
                Playlist(id="p1", title="Test", revision="r1"),
                [make_track(provider="spotify")] * 101,
            )
    finally:
        await provider.close()


async def test_musicbrainz_missing_isrc_is_negative_cached_not_transfer_failure():
    cache = MemoryCache()
    cache.wait = AsyncMock()
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(404, json={"error": "Not Found"})

    async with httpx.AsyncClient(
        base_url="https://musicbrainz.org/ws/2/", transport=httpx.MockTransport(handler)
    ) as client:
        mb = MusicBrainz(cache, "Harmonia tests (https://example.org)", client)
        assert await mb.by_isrc("GBBKS9800001") == []
        assert await mb.by_isrc("GBBKS9800001") == []
        assert len(calls) == 1
        with pytest.raises(ProviderError):
            await mb.lookup("artist", "4f9da3e3-a0f1-4e86-a28c-345f3df36f37")


async def test_yandex_real_sdk_short_tracks_are_hydrated_and_order_preserved():
    from yandex_music import ClientAsync
    from yandex_music import Playlist as SDKPlaylist
    from yandex_music import Track as SDKTrack

    client = ClientAsync("test-token")
    raw = {
        "kind": 3,
        "title": "Test",
        "owner": {"uid": 7, "login": "listener"},
        "revision": 1,
        "tracks": [
            {"id": str(i), "album_id": "99", "timestamp": "2026-01-01T00:00:00Z"}
            for i in [123, 456, 123, 789]
        ],
    }
    playlist = SDKPlaylist.de_json(raw, client)
    first = SDKTrack.de_json(YA_TRACK, client)
    second = SDKTrack.de_json({**YA_TRACK, "id": "456", "title": "Other"}, client)
    client.users_playlists = AsyncMock(return_value=playlist)
    client.tracks = AsyncMock(return_value=[second, first])  # upstream omits unavailable 789
    adapter = YandexMusic("test-token", client)
    adapter.initialized = True
    imported = await adapter.read_playlist("7:3")
    assert [t.recording.title for t in imported.tracks] == ["Teardrop", "Other", "Teardrop"]
    assert imported.warnings == ("skipped_unavailable:1",)
    client.tracks.assert_awaited_once_with(["123:99", "456:99", "123:99", "789:99"])
