from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from harmonia.domain.text import detect_versions

Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
NonBlank = Annotated[str, Field(pattern=r"\S")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Version(str, Enum):
    def __str__(self) -> str:
        return self.value

    ORIGINAL = "original"
    LIVE = "live"
    REMIX = "remix"
    REMASTER = "remaster"
    ACOUSTIC = "acoustic"
    INSTRUMENTAL = "instrumental"
    RADIO_EDIT = "radio_edit"
    EXTENDED = "extended"
    SPED_UP = "sped_up"
    SLOWED = "slowed"
    DEMO = "demo"


class Artist(Model):
    name: NonBlank = Field(min_length=1, max_length=500)
    musicbrainz_id: str | None = None
    provider_ids: dict[str, str] = Field(default_factory=dict)


class Release(Model):
    title: NonBlank = Field(min_length=1, max_length=1000)
    musicbrainz_id: str | None = None
    release_group_id: str | None = None
    date: str | None = None
    provider_ids: dict[str, str] = Field(default_factory=dict)


class AudioFeatures(Model):
    """Supplied audio metadata; beat positions are seconds and probabilities are 0–1."""

    bpm: float | None = Field(default=None, gt=0, le=400)
    key: str | None = Field(default=None, pattern=r"^[A-G](#|b)?$")
    scale: str | None = Field(default=None, pattern="^(major|minor)$")
    energy: Probability | None = None
    loudness: float | None = None
    beat_positions: tuple[Annotated[float, Field(ge=0)], ...] = ()
    mood: dict[str, Probability] = Field(default_factory=dict)
    embedding: tuple[float, ...] | None = None
    fingerprint: str | None = None
    provenance: str | None = None

    @field_validator("beat_positions")
    @classmethod
    def ordered_beats(cls, positions):
        if any(a >= b for a, b in zip(positions, positions[1:], strict=False)):
            raise ValueError("beat positions must be strictly increasing")
        return positions


class Recording(Model):
    title: NonBlank = Field(min_length=1, max_length=1000)
    artists: tuple[Artist, ...] = Field(min_length=1)
    duration_ms: int | None = Field(default=None, gt=0, le=86_400_000)
    isrc: str | None = None
    musicbrainz_id: str | None = None
    versions: frozenset[Version] = frozenset({Version.ORIGINAL})

    @field_validator("isrc")
    @classmethod
    def clean_isrc(cls, value: str | None) -> str | None:
        if not value:
            return None
        import re

        value = value.replace("-", "").upper()
        if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", value):
            raise ValueError("invalid ISRC")
        return value

    @model_validator(mode="after")
    def infer_versions(self) -> "Recording":
        versions = set(self.versions) | {Version(v) for v in detect_versions(self.title)}
        if len(versions) > 1:
            versions.discard(Version.ORIGINAL)
        object.__setattr__(self, "versions", frozenset(versions or {Version.ORIGINAL}))
        return self

    @field_serializer("versions")
    def serialize_versions(self, versions):
        return sorted(versions)


class ProviderTrack(Model):
    provider: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    id: str = Field(min_length=1, max_length=500)
    url: str | None = None
    available: bool = True


class Track(Model):
    id: str = Field(min_length=1, max_length=500)
    recording: Recording
    album: Release | None = None
    providers: tuple[ProviderTrack, ...] = ()
    explicit: bool | None = None
    genres: tuple[str, ...] = ()
    features: AudioFeatures = Field(default_factory=AudioFeatures)

    @model_validator(mode="after")
    def release_version(self) -> "Track":
        if self.album and (tags := detect_versions(self.album.title)):
            versions = (self.recording.versions | {Version(v) for v in tags}) - {Version.ORIGINAL}
            object.__setattr__(
                self,
                "recording",
                self.recording.model_copy(update={"versions": frozenset(versions)}),
            )
        return self

    def provider_id(self, provider: str) -> str | None:
        return next((p.id for p in self.providers if p.provider == provider and p.available), None)


class Playlist(Model):
    schema_version: int = Field(default=1, ge=1, le=1)
    id: str = Field(min_length=1, max_length=500)
    title: NonBlank = Field(min_length=1, max_length=300)
    tracks: tuple[Track, ...] = Field(default=(), max_length=10_000)
    provider: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    revision: str | None = None
    warnings: tuple[str, ...] = ()
