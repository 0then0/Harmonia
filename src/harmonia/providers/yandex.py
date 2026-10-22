import re
from functools import partial
from urllib.parse import unquote, urlparse

from harmonia.domain.models import Artist, Playlist, ProviderTrack, Recording, Release, Track
from harmonia.providers.base import Capabilities, Provider, ProviderError, track_ids
from harmonia.providers.sdk import read_with_retry, sdk_write


class YandexMusic(Provider):
    name = "yandex"
    capabilities = Capabilities()

    def __init__(self, token: str, client=None):
        if client is None:
            from yandex_music import ClientAsync
            from yandex_music.utils.request_async import Request

            client = ClientAsync(token, request=Request(timeout=20))
        self.client = client
        self.initialized = False

    async def _read(self, method, *args, **kwargs):
        from yandex_music.exceptions import NetworkError, UnauthorizedError, YandexMusicError

        try:
            if not self.initialized:
                await self.client.init()
                self.initialized = True
            return await read_with_retry(
                lambda: getattr(self.client, method)(*args, **kwargs), (NetworkError,)
            )
        except UnauthorizedError:
            raise ProviderError("Yandex Music authentication failed") from None
        except YandexMusicError:
            raise ProviderError("Yandex Music rejected the request") from None

    @staticmethod
    def reference(value: str) -> tuple[str, str]:
        if value.startswith("https://"):
            url = urlparse(value)
            if url.hostname not in {
                "music.yandex.ru",
                "music.yandex.com",
                "music.yandex.kz",
                "music.yandex.by",
            }:
                raise ValueError("expected a Yandex Music playlist URL")
            match = re.fullmatch(r"/users/([^/]+)/playlists/(\d+)/?", url.path)
            if not match:
                raise ValueError("use a /users/<owner>/playlists/<kind> URL or owner:kind")
            value = f"{unquote(match[1])}:{match[2]}"
        match = re.fullmatch(r"([\w.@-]{1,150}):(\d{1,20})", value)
        if not match:
            raise ValueError("Yandex playlist reference must be owner:kind")
        return match[1], match[2]

    @staticmethod
    def track(data: dict) -> Track:
        albums = data.get("albums") or []
        album = albums[0] if albums else None
        tid = str(data["id"])
        album_id = str(album["id"]) if album else ""
        title = data["title"] + (f" ({data['version']})" if data.get("version") else "")
        pid = f"{tid}:{album_id}" if album_id else tid
        return Track(
            id=f"yandex:{pid}",
            recording=Recording(
                title=title,
                artists=tuple(
                    Artist(
                        name=a["name"], provider_ids={"yandex": str(a["id"])} if a.get("id") else {}
                    )
                    for a in data["artists"]
                ),
                duration_ms=data.get("duration_ms"),
            ),
            album=Release(
                title=album["title"],
                date=str(album["year"]) if album.get("year") else None,
                provider_ids={"yandex": album_id},
            )
            if album
            else None,
            providers=(
                ProviderTrack(
                    provider="yandex",
                    id=pid,
                    url=f"https://music.yandex.ru/album/{album_id}/track/{tid}" if album else None,
                    available=data.get("available", True),
                ),
            ),
            explicit=data.get("explicit")
            if "explicit" in data
            else (data.get("content_warning") == "explicit" if "content_warning" in data else None),
            genres=(album["genre"],) if album and album.get("genre") else (),
        )

    async def search(self, query: str, *, isrc: bool = False) -> list[Track]:
        if isrc:
            self.require("isrc_search")
        result = await self._read("search", query, type_="track", nocorrect=True)
        return (
            [self.track(t.to_dict()) for t in result.tracks.results]
            if result and result.tracks
            else []
        )

    async def list_playlists(self) -> list[Playlist]:
        data = await self._read("users_playlists_list")
        return [
            Playlist(
                id=f"{p.owner.uid}:{p.kind}",
                title=p.title,
                provider=self.name,
                revision=str(p.revision),
            )
            for p in data
        ]

    async def read_playlist(self, reference: str) -> Playlist:
        owner, kind = self.reference(reference)
        playlist = await self._read("users_playlists", kind, user_id=owner)
        # Playlist.tracks contains TrackShort, not Track. Hydrate metadata in
        # batches and reconstruct original order/duplicates, including omissions.
        short_tracks = playlist.tracks or []
        metadata = {}
        for offset in range(0, len(short_tracks), 100):
            batch = short_tracks[offset : offset + 100]
            for item in await self._read("tracks", [t.track_id for t in batch]):
                if item and item.title and item.artists:
                    metadata[str(item.id)] = item.to_dict()
        tracks = []
        skipped = 0
        for short in short_tracks:
            data = metadata.get(str(short.id))
            if not data:
                skipped += 1
                continue
            albums = data.get("albums") or []
            data = {
                **data,
                "albums": sorted(albums, key=lambda a: str(a["id"]) != str(short.album_id)),
            }
            tracks.append(self.track(data))
        return Playlist(
            id=f"{owner}:{kind}",
            title=playlist.title,
            provider=self.name,
            revision=str(playlist.revision),
            tracks=tuple(tracks),
            warnings=(f"skipped_unavailable:{skipped}",) if skipped else (),
        )

    async def create_playlist(self, title: str, tracks: list[Track]) -> Playlist:
        ids = track_ids(self.name, tracks)
        if any(not re.fullmatch(r"\d+:\d+", pid) for pid in ids):
            raise ProviderError("Yandex export requires track and album IDs for every track")
        # Initialize authentication using a safe read before the first mutation.
        if not self.initialized:
            await self._read("account_status")
        playlist = await sdk_write(
            lambda: self.client.users_playlists_create(title, visibility="private")
        )
        for index, pid in enumerate(ids):
            tid, aid = pid.split(":")
            playlist = await sdk_write(
                partial(
                    self.client.users_playlists_insert_track,
                    playlist.kind,
                    tid,
                    aid,
                    at=index,
                    revision=playlist.revision,
                )
            )
        return Playlist(
            id=f"{playlist.owner.uid}:{playlist.kind}",
            title=title,
            tracks=tuple(tracks),
            provider=self.name,
            revision=str(playlist.revision),
        )
