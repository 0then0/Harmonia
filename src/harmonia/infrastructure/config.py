import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql+psycopg://harmonia:harmonia@localhost:5432/harmonia"
        )
    )
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    api_key: str = field(default_factory=lambda: os.getenv("HARMONIA_API_KEY", ""), repr=False)
    spotify_token: str = field(
        default_factory=lambda: os.getenv("SPOTIFY_ACCESS_TOKEN", ""), repr=False
    )
    youtube_auth_file: str = field(default_factory=lambda: os.getenv("YOUTUBE_AUTH_FILE", ""))
    yandex_token: str = field(default_factory=lambda: os.getenv("YANDEX_TOKEN", ""), repr=False)
    musicbrainz_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "MUSICBRAINZ_USER_AGENT", "Harmonia/0.1.0 (https://github.com/0then0/Harmonia)"
        )
    )

    def require_api_key(self):
        if len(self.api_key) < 24:
            raise ValueError("set HARMONIA_API_KEY to a random value of at least 24 characters")
