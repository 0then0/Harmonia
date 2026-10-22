import pytest

from harmonia.domain.models import Artist, AudioFeatures, ProviderTrack, Recording, Release, Track


@pytest.fixture
def make_track():
    def make(
        id="track:1",
        title="Teardrop",
        artist="Massive Attack",
        duration=330000,
        isrc=None,
        mbid=None,
        versions=("original",),
        album="Mezzanine",
        bpm=None,
        energy=None,
        explicit=False,
        provider=None,
        provider_id=None,
        mood=None,
        key=None,
        scale=None,
        genres=(),
    ):
        return Track(
            id=id,
            recording=Recording(
                title=title,
                artists=(Artist(name=artist),),
                duration_ms=duration,
                isrc=isrc,
                musicbrainz_id=mbid,
                versions=frozenset(versions),
            ),
            album=Release(title=album) if album else None,
            features=AudioFeatures(bpm=bpm, energy=energy, mood=mood or {}, key=key, scale=scale),
            explicit=explicit,
            providers=(ProviderTrack(provider=provider, id=provider_id or id),) if provider else (),
            genres=genres,
        )

    return make
