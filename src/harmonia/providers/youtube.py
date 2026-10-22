import asyncio
import functools
import re
from urllib.parse import parse_qs, urlparse

from harmonia.domain.models import Artist, Playlist, ProviderTrack, Recording, Release, Track
from harmonia.providers.base import Capabilities, Provider, ProviderError, track_ids
from harmonia.providers.sdk import read_with_retry, sdk_write


class YouTubeMusic(Provider):
    name = "youtube"
    capabilities = Capabilities()

    def __init__(self, auth_file: str | None = None, client=None):
        self.session = None
        if client is None:
            import requests
            from ytmusicapi import YTMusic

            self.session = requests.Session()
            self.session.request = functools.partial(self.session.request, timeout=20)
            client = YTMusic(auth_file, requests_session=self.session)
        self.client = client
        # The synchronous SDK owns a requests.Session, so serialize access to it.
        self.lock = asyncio.Lock()

    async def _read(self, method, *args, **kwargs):
        import requests
        from ytmusicapi.exceptions import YTMusicError

        async with self.lock:
            try:
                return await read_with_retry(
                    lambda: asyncio.to_thread(getattr(self.client, method), *args, **kwargs),
                    (requests.RequestException,),
                )
            except YTMusicError:
                raise ProviderError(
                    "YouTube Music rejected the request; check browser credentials"
                ) from None

    @staticmethod
    def reference(value: str) -> str:
        if value.startswith("https://"):
            url = urlparse(value)
            if url.hostname not in {"music.youtube.com", "www.youtube.com", "youtube.com"}:
                raise ValueError("expected a YouTube Music playlist URL")
            value = parse_qs(url.query).get("list", [""])[0]
        if not re.fullmatch(r"[\w-]{1,200}", value):
            raise ValueError("invalid YouTube playlist ID")
        return value

    @staticmethod
    def track(data: dict) -> Track:
        duration = data.get("duration_seconds")
        if duration is None and data.get("duration"):
            parts = data["duration"].split(":")
            if all(p.isdigit() for p in parts):
                duration = sum(int(p) * 60**i for i, p in enumerate(reversed(parts)))
        album = data.get("album")
        vid = data["videoId"]
        return Track(
            id=f"youtube:{vid}",
            recording=Recording(
                title=data["title"],
                artists=tuple(
                    Artist(name=a["name"], provider_ids={"youtube": a["id"]} if a.get("id") else {})
                    for a in data["artists"]
                ),
                duration_ms=int(duration * 1000) if duration else None,
            ),
            album=Release(
                title=album["name"],
                provider_ids={"youtube": album["id"]} if album.get("id") else {},
            )
            if album
            else None,
            providers=(
                ProviderTrack(
                    provider="youtube",
                    id=vid,
                    url=f"https://music.youtube.com/watch?v={vid}",
                    available=data.get("isAvailable", True),
                ),
            ),
            explicit=data.get("isExplicit"),
        )

    async def search(self, query: str, *, isrc: bool = False) -> list[Track]:
        if isrc:
            self.require("isrc_search")
        data = await self._read("search", query, filter="songs", limit=20)
        return [self.track(t) for t in data if t.get("videoId") and t.get("artists")]

    async def list_playlists(self) -> list[Playlist]:
        data = await self._read("get_library_playlists", limit=None)
        return [Playlist(id=p["playlistId"], title=p["title"], provider=self.name) for p in data]

    async def read_playlist(self, reference: str) -> Playlist:
        pid = self.reference(reference)
        data = await self._read("get_playlist", pid, limit=None)
        tracks = [
            self.track(t)
            for t in data["tracks"]
            if t.get("videoId") and t.get("artists") and t.get("title")
        ]
        skipped = len(data["tracks"]) - len(tracks)
        return Playlist(
            id=pid,
            title=data["title"],
            tracks=tuple(tracks),
            provider=self.name,
            warnings=(f"skipped_unavailable:{skipped}",) if skipped else (),
        )

    async def create_playlist(self, title: str, tracks: list[Track]) -> Playlist:
        ids = track_ids(self.name, tracks)
        async with self.lock:
            pid = await sdk_write(
                lambda: asyncio.to_thread(
                    self.client.create_playlist,
                    title,
                    "Created with Harmonia",
                    privacy_status="PRIVATE",
                    video_ids=ids,
                )
            )
        if not isinstance(pid, str):
            raise ProviderError("YouTube Music did not confirm playlist creation")
        return Playlist(id=pid, title=title, tracks=tuple(tracks), provider=self.name)

    async def close(self):
        if self.session:
            self.session.close()
