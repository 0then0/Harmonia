import hashlib
import re
from uuid import UUID

import httpx

from harmonia.domain.models import Artist, Recording, Release, Track
from harmonia.infrastructure.http import HTTP
from harmonia.providers.base import ProviderHTTPError


class MusicBrainz:
    """Independent metadata source. Never treats search rank as confirmed identity."""

    def __init__(self, cache, user_agent: str, client: httpx.AsyncClient | None = None):
        if not user_agent.strip():
            raise ValueError("MusicBrainz requires an identifying User-Agent with contact URL")
        self.cache = cache
        self.owned = client is None
        self.client = client or httpx.AsyncClient(
            base_url="https://musicbrainz.org/ws/2/",
            timeout=20,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self.http = HTTP(self.client, lambda: cache.wait("musicbrainz"))

    async def _get(
        self, path: str, params: dict | None = None, *, missing_ok: bool = False
    ) -> dict:
        params = {**(params or {}), "fmt": "json"}
        key = "mb:" + hashlib.sha256((path + str(sorted(params.items()))).encode()).hexdigest()
        cached = await self.cache.get(key)
        if cached is not None:
            return cached
        try:
            value = await self.http.request("GET", path, params=params)
        except ProviderHTTPError as exc:
            if exc.status_code != 404 or not missing_ok:
                raise
            value = {}
        await self.cache.set(key, value)
        return value

    async def lookup(self, entity: str, mbid: str) -> dict:
        includes = {
            "recording": "artists+releases+isrcs",
            "artist": "aliases",
            "release": "artists+recordings+release-groups",
            "release-group": "artists+releases",
        }
        if entity not in includes:
            raise ValueError("unsupported MusicBrainz entity")
        identifier = str(UUID(mbid))
        return await self._get(f"{entity}/{identifier}", {"inc": includes[entity]})

    async def by_isrc(self, isrc: str) -> list[Track]:
        code = isrc.replace("-", "").upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", code):
            raise ValueError("invalid ISRC")
        result = await self._get(f"isrc/{code}", {"inc": "artists+releases"}, missing_ok=True)
        return [self.recording(item, code) for item in result.get("recordings", [])]

    @staticmethod
    def recording(data: dict, isrc: str | None = None) -> Track:
        credits = [
            c["artist"]
            for c in data.get("artist-credit", [])
            if isinstance(c, dict) and "artist" in c
        ]
        releases = data.get("releases") or []
        release = releases[0] if releases else None
        return Track(
            id=f"musicbrainz:{data['id']}",
            recording=Recording(
                title=data["title"],
                artists=tuple(Artist(name=a["name"], musicbrainz_id=a.get("id")) for a in credits),
                duration_ms=data.get("length"),
                isrc=isrc,
                musicbrainz_id=data["id"],
            ),
            album=Release(
                title=release["title"],
                musicbrainz_id=release["id"],
                release_group_id=release.get("release-group", {}).get("id"),
                date=release.get("date"),
            )
            if release
            else None,
        )

    async def close(self):
        if self.owned:
            await self.client.aclose()
