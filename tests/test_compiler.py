import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from harmonia.domain.compiler import PlaylistConstraints, admissible, compile_playlist, harmonic
from harmonia.domain.matching import identity
from harmonia.domain.models import Playlist


def test_duration_parsing_and_unknown_fields():
    assert PlaylistConstraints(duration="90m").duration == 5400
    assert PlaylistConstraints(duration="2h").duration == 7200
    for data in (
        {"duration": "90 minutes"},
        {"duration": -1},
        {"bogus": True},
        {"tempo": {"start": -1, "end": 100}},
        {"energy": {"start": 0, "end": 2}},
        {"transitions": {"semantic_similarity": 0.5}},
    ):
        with pytest.raises(ValidationError):
            PlaylistConstraints.model_validate(data)


def test_hard_constraints_no_duplicates_and_duration(make_track):
    tracks = [
        make_track(id=str(i), artist=f"A{i % 3}", duration=60000, bpm=90 + i) for i in range(9)
    ]
    tracks += [
        make_track(id="live", title="Teardrop (Live)", artist="Live artist", duration=60000),
        make_track(id="explicit", artist="Explicit artist", duration=60000, explicit=True),
        make_track(id="unknown-explicit", artist="Unknown artist", duration=60000, explicit=None),
    ]
    # Distinct titles represent distinct recordings, rather than duplicate editions.
    tracks = [
        t.model_copy(
            update={
                "recording": t.recording.model_copy(
                    update={"title": t.recording.title + " " + t.id}
                )
            }
        )
        for t in tracks
    ]
    c = PlaylistConstraints(
        duration="10m", rules={"max_tracks_per_artist": 2, "artist_gap": 1, "explicit": False}
    )
    result = compile_playlist(c, tracks)
    assert len(result.tracks) == 6
    assert len({identity(t) for t in result.tracks}) == 6
    assert sum(t.recording.duration_ms for t in result.tracks) <= c.duration * 1000
    assert all(admissible(result.tracks[:i], t, c) for i, t in enumerate(result.tracks))
    assert "live" not in [t.id for t in result.tracks]
    assert any(w.startswith("underfilled") for w in result.warnings)


def test_no_repeated_recording_across_catalogues(make_track):
    result = compile_playlist(
        PlaylistConstraints(duration="10m"), [make_track(id="a"), make_track(id="b")]
    )
    assert len(result.tracks) == 1


def test_tempo_ramp_changes_order(make_track):
    tracks = [
        make_track(id=str(i), title=f"Track {i}", artist=f"A{i}", duration=60000, bpm=bpm)
        for i, bpm in enumerate([90, 110, 130])
    ]
    c = PlaylistConstraints(
        duration="3m",
        tempo={"start": 90, "end": 150},
        transitions={"bpm_smoothness": 0, "harmonic_key": 0},
    )
    result = compile_playlist(c, tracks)
    assert [t.features.bpm for t in result.tracks] == [90, 110, 130]


def test_missing_features_are_explicit(make_track):
    c = PlaylistConstraints(duration="10m", tempo={"start": 90, "end": 120})
    result = compile_playlist(c, [make_track()])
    assert any(w.startswith("missing_features") for w in result.warnings)
    c = PlaylistConstraints(
        duration="10m", tempo={"start": 90, "end": 120}, rules={"missing_features": "exclude"}
    )
    assert not compile_playlist(c, [make_track()]).tracks


def test_known_clean_constraint_fails_closed(make_track):
    c = PlaylistConstraints(rules={"explicit": False})
    assert not compile_playlist(c, [make_track(explicit=None)]).tracks


def test_harmonic_relative_major_and_fifths(make_track):
    a = make_track(key="C", scale="major")
    assert harmonic(a, make_track(key="A", scale="minor")) == 1
    assert harmonic(a, make_track(key="G", scale="major")) == 1
    assert harmonic(a, make_track(key="F#", scale="major")) == 0
    assert harmonic(a, make_track()) == 0.5


def test_energy_mood_and_seed_affect_selection(make_track):
    tracks = [
        make_track(
            id="calm", title="Calm", artist="Seed", duration=60000, energy=0.1, mood={"relaxed": 1}
        ),
        make_track(
            id="loud", title="Loud", artist="Other", duration=60000, energy=0.9, mood={"relaxed": 0}
        ),
    ]
    c = PlaylistConstraints(
        duration="60s",
        energy={"start": 0.1, "end": 0.1},
        mood={"start": "relaxed", "end": "relaxed"},
        seed={"artists": ["Seed"]},
    )
    assert compile_playlist(c, tracks).tracks[0].id == "calm"


def test_candidate_and_length_budgets(make_track):
    with pytest.raises(ValueError, match="1000"):
        compile_playlist(PlaylistConstraints(), [make_track()] * 1001)
    assert not compile_playlist(PlaylistConstraints(duration=30), [make_track()]).tracks


def test_deterministic_across_input_order_and_process_hash_seed(tmp_path):
    library = Playlist.model_validate_json(Path("examples/library.json").read_text())
    c = PlaylistConstraints(duration="20m", random_seed=42)
    assert compile_playlist(c, list(library.tracks)) == compile_playlist(
        c, list(reversed(library.tracks))
    )
    outputs = []
    for seed in ("1", "99"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        outputs.append(
            subprocess.check_output(
                [
                    sys.executable,
                    "-m",
                    "harmonia.cli",
                    "build",
                    "examples/night-drive.yml",
                    "--library",
                    "examples/library.json",
                ],
                env=env,
            )
        )
    assert outputs[0] == outputs[1]
    assert json.loads(outputs[0])["tracks"]


def test_partially_enriched_duplicate_cannot_repeat(make_track):
    a = make_track(id="a", isrc="GBBKS9800001")
    b = make_track(id="b", isrc="GBBKS9800001", mbid="some-mbid")
    result = compile_playlist(PlaylistConstraints(duration="20m"), [a, b])
    assert len(result.tracks) == 1


def test_conflicting_duplicate_id_is_rejected(make_track):
    with pytest.raises(ValueError, match="conflicting candidate"):
        compile_playlist(
            PlaylistConstraints(), [make_track(id="same"), make_track(id="same", duration=300000)]
        )


def test_three_point_energy_and_mood_arcs(make_track):
    from harmonia.domain.compiler import Curve, MoodCurve, quality

    arc = Curve(start=0.1, middle=0.8, end=0.2)
    assert arc.at(0.25) == pytest.approx(0.45)
    assert arc.at(0.75) == pytest.approx(0.5)
    mood = MoodCurve(start="relaxed", middle="dark", end="energetic")
    assert [mood.at(p) for p in (0, 0.5, 0.9)] == ["relaxed", "dark", "energetic"]
    a = make_track(id="a", artist="A", genres=("trip-hop",))
    b = make_track(id="b", artist="B", genres=("jazz",))
    c = PlaylistConstraints(transitions={"genre_diversity": 1})
    assert quality((a, b), c) > quality((a, a), c)


def test_invalid_audio_features_are_rejected():
    from harmonia.domain.models import AudioFeatures

    for data in (
        {"key": "not-a-key"},
        {"bpm": float("nan")},
        {"energy": 2},
        {"mood": {"dark": -0.1}},
        {"beat_positions": [1, 0]},
        {"beat_positions": [-1]},
    ):
        with pytest.raises(ValidationError):
            AudioFeatures.model_validate(data)


def test_local_refinement_recovers_tempo_order_with_narrow_beam(make_track):
    # Unequal durations bias a width-one beam towards the long second track.
    # Refinement must recover a smooth order without changing the selected set.
    tracks = [
        make_track(id=str(i), title=f"Track {i}", artist=f"A{i}", bpm=bpm, duration=seconds * 1000)
        for i, (bpm, seconds) in enumerate([(90, 60), (100, 180), (130, 60), (150, 60)])
    ]
    c = PlaylistConstraints(
        duration=360,
        beam_width=1,
        tempo={"start": 90, "end": 150},
        transitions={"bpm_smoothness": 0, "harmonic_key": 0},
    )
    result = compile_playlist(c, tracks)
    assert [t.features.bpm for t in result.tracks] == [90, 100, 130, 150]
    assert sum(t.recording.duration_ms for t in result.tracks) == 360000


def test_admissible_counts_all_artists_and_can_allow_adjacent_repeats(make_track):
    a = make_track(id="a", title="One", artist="A")
    b = make_track(id="b", title="Two", artist="A")
    c = PlaylistConstraints(rules={"max_tracks_per_artist": 1})
    assert not admissible((a,), b, c)
    c = PlaylistConstraints(rules={"max_tracks_per_artist": 2, "artist_gap": 0})
    assert admissible((a,), b, c)


def test_duration_search_keeps_shorter_prefixes_to_avoid_dead_ends():
    library = Playlist.model_validate_json(Path("examples/library.json").read_text())
    result = compile_playlist(
        PlaylistConstraints(duration="20m", random_seed=42), list(library.tracks)
    )
    assert 1140000 <= sum(t.recording.duration_ms for t in result.tracks) <= 1200000
    assert len(result.tracks) == 3
