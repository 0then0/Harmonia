from abc import ABC, abstractmethod

from harmonia.domain.models import Model, Playlist, Track


class Capabilities(Model):
    search: bool = True
    read_playlists: bool = True
    create_playlists: bool = True
    update_playlists: bool = False
    reorder_tracks: bool = False
    read_liked_tracks: bool = False
    write_liked_tracks: bool = False
    isrc_search: bool = False
    oauth: bool = False


class ProviderError(Exception):
    """A safe, user-facing provider failure. Never include credentials or raw responses."""


class UnsupportedCapability(ProviderError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"provider returned HTTP {status_code}")


class Provider(ABC):
    name: str
    capabilities: Capabilities

    def require(self, capability: str) -> None:
        if not getattr(self.capabilities, capability, False):
            raise UnsupportedCapability(f"{self.name} does not support {capability}")

    @abstractmethod
    async def search(self, query: str, *, isrc: bool = False) -> list[Track]: ...

    @abstractmethod
    async def list_playlists(self) -> list[Playlist]: ...

    @abstractmethod
    async def read_playlist(self, reference: str) -> Playlist: ...

    @abstractmethod
    async def create_playlist(self, title: str, tracks: list[Track]) -> Playlist:
        """Create a private playlist. Never blindly retry this operation."""

    async def replace_tracks(self, playlist: Playlist, tracks: list[Track]) -> Playlist:
        raise UnsupportedCapability(f"{self.name} does not support update_playlists")

    async def close(self) -> None:
        return None


def track_ids(provider: str, tracks: list[Track]) -> list[str]:
    ids = [t.provider_id(provider) for t in tracks]
    if any(x is None for x in ids):
        raise ProviderError(f"every track must have an available {provider} identifier")
    return ids
