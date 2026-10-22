import pytest
from pydantic import ValidationError

from harmonia.domain.matching import identity, match_track
from harmonia.domain.models import Artist
from harmonia.domain.text import base_title, detect_versions, normalize

ISRC = "GBBKS9800001"


@pytest.mark.parametrize(
    "title,version",
    [
        ("Teardrop (Live)", "live"),
        ("Teardrop - Live at the O2", "live"),
        ("Teardrop (Acoustic)", "acoustic"),
        ("Teardrop (Unplugged)", "acoustic"),
        ("Teardrop (2018 Remaster)", "remaster"),
        ("Teardrop - Remastered 2018", "remaster"),
        ("Teardrop (Remix)", "remix"),
        ("Teardrop [Radio Edit]", "radio_edit"),
        ("Teardrop (Extended Mix)", "extended"),
        ("Teardrop (Instrumental)", "instrumental"),
        ("Teardrop (Sped Up)", "sped_up"),
        ("Teardrop - Slowed + Reverb", "slowed"),
        ("Teardrop (Nightcore)", "sped_up"),
        ("Teardrop (Demo)", "demo"),
        ("Teardrop (Концертная версия)", "live"),
        ("Teardrop (Ремикс)", "remix"),
    ],
)
def test_versions_rejected_even_with_same_isrc(make_track, title, version):
    source = make_track(isrc=ISRC)
    target = make_track(id="other", title=title, isrc=ISRC)
    assert version in target.recording.versions
    assert match_track(source, [target]).match is None
    assert identity(source) != identity(target)


@pytest.mark.parametrize("title", ["Live Forever", "Deliver", "Acoustic Dreams", "Remix the World"])
def test_words_in_original_titles_are_not_version_markers(title):
    assert detect_versions(title) == set()


def test_exact_isrc_and_deterministic_ranking(make_track):
    source = make_track(isrc=ISRC)
    candidates = [make_track(id="b", isrc=ISRC), make_track(id="a", isrc=ISRC)]
    result = match_track(source, candidates)
    assert result.match.track.id == "a"
    assert result.match.confidence == 0.99
    assert result.needs_review
    assert result.reasons == ("ambiguous_candidates",)
    assert result == match_track(source, candidates[::-1])


def test_musicbrainz_cascade(make_track):
    source = make_track(mbid="4f9da3e3-a0f1-4e86-a28c-345f3df36f37")
    target = make_track(id="other", mbid=source.recording.musicbrainz_id)
    result = match_track(source, [target])
    assert result.match.confidence == 0.98
    assert "exact_musicbrainz_recording" in result.match.reasons
    assert not result.needs_review


def test_conflicting_mbid_rejected(make_track):
    assert (
        match_track(
            make_track(mbid="a", isrc=ISRC), [make_track(id="x", mbid="b", isrc=ISRC)]
        ).match
        is None
    )


def test_normalized_artist_title_album_duration(make_track):
    result = match_track(
        make_track(), [make_track(id="other", title="TEARDROP!", artist="Massive  Attack")]
    )
    assert not result.needs_review
    assert "normalized_artist_title" in result.match.reasons
    assert "same_album" in result.match.reasons


@pytest.mark.parametrize(
    "changes",
    [dict(title="Completely Different"), dict(artist="Unrelated Artist"), dict(duration=600000)],
)
def test_bad_metadata_cannot_hide_behind_isrc(make_track, changes):
    assert (
        match_track(make_track(isrc=ISRC), [make_track(id="x", isrc=ISRC, **changes)]).match is None
    )


def test_different_isrc_needs_review(make_track):
    result = match_track(make_track(isrc=ISRC), [make_track(id="x", isrc="GBBKS9800002")])
    assert result.needs_review
    assert "different_isrc" in result.match.reasons


def test_collaborator_missing_needs_review(make_track):
    source = make_track()
    target = make_track(id="x")
    target = target.model_copy(
        update={
            "recording": target.recording.model_copy(
                update={"artists": (*target.recording.artists, Artist(name="Guest Artist"))}
            )
        }
    )
    assert match_track(source, [target]).needs_review


def test_missing_duration_and_album_does_not_auto_confirm(make_track):
    result = match_track(
        make_track(duration=None, album=None), [make_track(id="other", duration=None, album=None)]
    )
    assert result.needs_review
    assert result.match is not None


def test_fuzzy_typo_returns_explanation(make_track):
    result = match_track(make_track(), [make_track(id="x", title="Teardrops")])
    assert "fuzzy_artist_title" in result.match.reasons


def test_combined_version_tags(make_track):
    t = make_track(title="Teardrop (Live, Remastered)")
    assert t.recording.versions == {"live", "remaster"}
    assert base_title(t.recording.title) == "teardrop"
    assert match_track(t, [make_track(id="b", title="Teardrop (Live)")]).match is None


def test_cyrillic_unicode_and_isrc_validation(make_track):
    assert normalize("Мумий Тролль — Всё") == normalize("мумий тролль все")
    assert normalize("Beyoncé") == normalize("Beyonce")
    assert make_track(isrc="GB-BKS-98-00001").recording.isrc == ISRC
    with pytest.raises(ValidationError):
        make_track(isrc="not-an-isrc")


def test_empty_and_duplicate_candidates(make_track):
    assert match_track(make_track(), []).match is None
    other = make_track(id="other")
    result = match_track(make_track(), [other, other])
    assert not result.alternatives
    assert not result.needs_review


def test_release_version_is_evidence_even_when_track_title_has_no_marker(make_track):
    source = make_track(isrc=ISRC)
    remaster = make_track(id="other", isrc=ISRC, album="Mezzanine (2018 Remaster)")
    assert remaster.recording.versions == {"remaster"}
    assert match_track(source, [remaster]).match is None


def test_reused_isrc_with_moderate_duration_difference_requires_review(make_track):
    result = match_track(
        make_track(isrc=ISRC), [make_track(id="edited", duration=340000, isrc=ISRC)]
    )
    assert result.match is not None and result.needs_review
    assert "duration_requires_review" in result.match.reasons


def test_version_strings_and_canonical_serialization_remain_stable(make_track):
    from harmonia.domain.models import Track, Version

    track = make_track(title="Teardrop (Live, Remastered)")
    assert Version.LIVE == "live"
    assert str(Version.LIVE) == "live"
    assert f"{Version.LIVE}" == "live"
    payload = track.model_dump(mode="json")
    assert payload["recording"]["versions"] == ["live", "remaster"]
    assert Track.model_validate_json(track.model_dump_json()) == track


@pytest.mark.parametrize(
    "before,after",
    [
        ("Teardrop (Four Tet Remix)", "Teardrop (Club Remix)"),
        ("Teardrop (Live at Brixton)", "Teardrop (Live in Paris)"),
        ("Teardrop (2011 Remaster)", "Teardrop (2018 Remaster)"),
    ],
)
def test_different_editions_of_same_version_type_need_review(make_track, before, after):
    from harmonia.domain.matching import recording_keys

    source = make_track(title=before, isrc=ISRC)
    target = make_track(id="other", title=after, isrc=ISRC)
    result = match_track(source, [target])
    assert result.needs_review
    assert "version_details_require_review" in result.match.reasons
    assert not recording_keys(source) & recording_keys(target)


def test_remaster_word_order_does_not_change_edition(make_track):
    source = make_track(title="Teardrop (Remastered 2018)")
    target = make_track(id="other", title="Teardrop (2018 Remaster)")
    assert not match_track(source, [target]).needs_review


@pytest.mark.parametrize("field", ["title", "artist"])
def test_whitespace_metadata_cannot_create_confident_false_matches(make_track, field):
    with pytest.raises(ValidationError):
        make_track(**{field: "   "})
