import re
from urllib.parse import urlparse

import httpx

from harmonia.domain.models import Artist, Playlist, ProviderTrack, Recording, Release, Track
from harmonia.infrastructure.http import HTTP
from harmonia.providers.base import Capabilities, Provider, ProviderError, track_ids


class Spotify(Provider):
    name = "spotify"
    capabilities = Capabilities(
        isrc_search=True, oauth=True, update_playlists=True, reorder_tracks=True
    )

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.owned = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://api.spotify.com/v1/",
            timeout=20,
            headers={"Authorization": f"Bearer {token}"},
        )
        self.http = HTTP(self.client)

    @staticmethod
    def reference(value: str) -> str:
        if value.startswith("spotify:playlist:"):
            value = value.split(":")[-1]
        elif value.startswith("https://"):
            url = urlparse(value)
            if url.hostname != "open.spotify.com" or "/playlist/" not in url.path:
                raise ValueError("expected a Spotify playlist URL")
            value = url.path.rsplit("/", 1)[-1]
        if not re.fullmatch(r"[a-zA-Z0-9]{1,100}", value):
            raise ValueError("invalid Spotify playlist ID")
        return value

    @staticmethod
    def track(data: dict) -> Track:
        album = data.get("album")
        return Track(
            id=f"spotify:{data['id']}",
            recording=Recording(
                title=data["name"],
                artists=tuple(
                    Artist(name=a["name"], provider_ids={"spotify": a["id"]} if a.get("id") else {})
                    for a in data["artists"]
                ),
                duration_ms=data.get("duration_ms"),
                isrc=data.get("external_ids", {}).get("isrc"),
            ),
            album=Release(
                title=album["name"],
                date=album.get("release_date"),
                provider_ids={"spotify": album["id"]} if album.get("id") else {},
            )
            if album
            else None,
            providers=(
                ProviderTrack(
                    provider="spotify",
                    id=data["id"],
                    url=f"https://open.spotify.com/track/{data['id']}",
                    available=data.get("is_playable", True),
                ),
            ),
            explicit=data.get("explicit"),
        )

    async def search(self, query: str, *, isrc: bool = False) -> list[Track]:
        data = await self.http.request(
            "GET",
            "search",
            params={"q": f"isrc:{query}" if isrc else query, "type": "track", "limit": 10},
        )
        return [self.track(t) for t in data.get("tracks", {}).get("items", []) if t and t.get("id")]

    async def _pages(self, path: str) -> list[dict]:
        items = []
        offset = 0
        for _ in range(201):
            data = await self.http.request("GET", path, params={"limit": 50, "offset": offset})
            batch = data.get("items", [])
            items.extend(batch)
            if not data.get("next"):
                return items
            if not batch or len(items) > 10_000:
                break
            offset += len(batch)
        raise ProviderError("Spotify pagination exceeded safe limit; playlist was not imported")

    async def list_playlists(self) -> list[Playlist]:
        return [
            Playlist(id=p["id"], title=p["name"], provider=self.name, revision=p.get("snapshot_id"))
            for p in await self._pages("me/playlists")
        ]

    async def read_playlist(self, reference: str) -> Playlist:
        pid = self.reference(reference)
        before = await self.http.request("GET", f"playlists/{pid}")
        items = await self._pages(f"playlists/{pid}/items")
        after = await self.http.request("GET", f"playlists/{pid}")
        if before.get("snapshot_id") != after.get("snapshot_id"):
            raise ProviderError("playlist changed during import; retry import")
        tracks, skipped = [], 0
        for item in items:
            t = item.get("item", item.get("track"))
            if (
                not t
                or not t.get("id")
                or t.get("type", "track") != "track"
                or item.get("is_local")
            ):
                skipped += 1
                continue
            tracks.append(self.track(t))
        return Playlist(
            id=pid,
            title=before["name"],
            provider=self.name,
            revision=before.get("snapshot_id"),
            tracks=tuple(tracks),
            warnings=(f"skipped_unavailable_or_non_music:{skipped}",) if skipped else (),
        )

    async def create_playlist(self, title: str, tracks: list[Track]) -> Playlist:
        ids = track_ids(self.name, tracks)
        result = await self.http.request(
            "POST", "me/playlists", json={"name": title, "public": False}
        )
        pid = result["id"]
        revision = result.get("snapshot_id")
        for i in range(0, len(ids), 100):
            response = await self.http.request(
                "POST",
                f"playlists/{pid}/items",
                json={"uris": [f"spotify:track:{x}" for x in ids[i : i + 100]]},
            )
            revision = response.get("snapshot_id")
        return Playlist(
            id=pid, title=title, tracks=tuple(tracks), provider=self.name, revision=revision
        )

    async def replace_tracks(self, playlist: Playlist, tracks: list[Track]) -> Playlist:
        ids = track_ids(self.name, tracks)
        if len(ids) > 100:
            raise ProviderError(
                "safe replacement supports at most 100 tracks; export to a new playlist"
            )
        current = await self.read_playlist(playlist.id)
        if not playlist.revision or current.revision != playlist.revision:
            raise ProviderError("playlist revision conflict; generate a new diff")
        result = await self.http.request(
            "PUT",
            f"playlists/{self.reference(playlist.id)}/items",
            json={"uris": [f"spotify:track:{x}" for x in ids], "snapshot_id": playlist.revision},
        )
        return Playlist(
            id=playlist.id,
            title=playlist.title,
            tracks=tuple(tracks),
            provider=self.name,
            revision=result.get("snapshot_id"),
        )

    async def close(self):
        if self.owned:
            await self.client.aclose()
