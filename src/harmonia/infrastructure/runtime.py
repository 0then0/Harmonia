from arq import create_pool
from arq.connections import RedisSettings

from harmonia.infrastructure.config import Settings
from harmonia.infrastructure.http import RedisCache
from harmonia.infrastructure.musicbrainz import MusicBrainz
from harmonia.infrastructure.store import Store
from harmonia.providers.base import ProviderError
from harmonia.providers.spotify import Spotify
from harmonia.providers.yandex import YandexMusic
from harmonia.providers.youtube import YouTubeMusic


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.database_url)
        self.providers = {}
        self.redis = None
        self.musicbrainz = None

    async def start(self):
        self.redis = await create_pool(RedisSettings.from_dsn(self.settings.redis_url))
        self.musicbrainz = MusicBrainz(RedisCache(self.redis), self.settings.musicbrainz_user_agent)
        s = self.settings
        if s.spotify_token:
            self.providers["spotify"] = Spotify(s.spotify_token)
        if s.youtube_auth_file:
            self.providers["youtube"] = YouTubeMusic(s.youtube_auth_file)
        if s.yandex_token:
            self.providers["yandex"] = YandexMusic(s.yandex_token)

    def provider(self, name):
        if name not in self.providers:
            raise ProviderError(f"provider {name} is not configured")
        return self.providers[name]

    async def close(self):
        for provider in self.providers.values():
            await provider.close()
        if self.musicbrainz:
            await self.musicbrainz.close()
        if self.redis:
            await self.redis.aclose()
        await self.store.close()
