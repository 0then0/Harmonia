import hashlib
import math
import re
from collections import Counter
from typing import Literal

from pydantic import Field, field_validator

from harmonia.domain.matching import recording_keys
from harmonia.domain.models import Model, Playlist, Probability, Track, Version
from harmonia.domain.text import normalize


class Curve(Model):
    start: float
    end: float
    middle: float | None = None

    def at(self, position: float) -> float:
        if self.middle is None:
            return self.start + (self.end - self.start) * position
        a, b, p = (
            (self.start, self.middle, position * 2)
            if position < 0.5
            else (self.middle, self.end, (position - 0.5) * 2)
        )
        return a + (b - a) * p


class MoodCurve(Model):
    start: str
    middle: str | None = None
    end: str

    def at(self, position: float) -> str:
        if self.middle and 1 / 3 <= position < 2 / 3:
            return self.middle
        return self.start if position < 0.5 else self.end


class Seed(Model):
    artists: tuple[str, ...] = ()


class Rules(Model):
    max_tracks_per_artist: int = Field(default=2, ge=1, le=100)
    artist_gap: int = Field(default=1, ge=0, le=20)
    avoid_live: bool = True
    avoid_remixes: bool = True
    explicit: bool | None = None
    missing_features: Literal["allow", "exclude"] = "allow"


class Transitions(Model):
    bpm_smoothness: Probability = 0.8
    harmonic_key: Probability = 0.7
    # Embedding-based scoring is unsupported; reject non-zero weights.
    semantic_similarity: float = Field(default=0, ge=0, le=0)
    genre_diversity: Probability = 0.3


class PlaylistConstraints(Model):
    title: str = Field(default="Compiled playlist", min_length=1, max_length=300)
    duration: int = Field(
        default=5400,
        ge=30,
        le=43_200,
        description="Target seconds; strings such as 90m are accepted",
    )
    seed: Seed = Field(default_factory=Seed)
    random_seed: int = 0
    tempo: Curve | None = None
    energy: Curve | None = None
    mood: MoodCurve | None = None
    rules: Rules = Field(default_factory=Rules)
    transitions: Transitions = Field(default_factory=Transitions)
    beam_width: int = Field(default=12, ge=1, le=32)
    max_tracks: int = Field(default=100, ge=1, le=200)

    @field_validator("duration", mode="before")
    @classmethod
    def parse_duration(cls, value):
        if isinstance(value, str):
            match = re.fullmatch(r"(\d+)([smh])", value.strip())
            if not match:
                raise ValueError("duration must be seconds or an integer with s/m/h suffix")
            return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
        return value

    @field_validator("tempo")
    @classmethod
    def validate_tempo(cls, value):
        if value and any(
            not 20 <= v <= 400 for v in (value.start, value.end, value.middle) if v is not None
        ):
            raise ValueError("tempo curve must be between 20 and 400 BPM")
        return value

    @field_validator("energy")
    @classmethod
    def validate_energy(cls, value):
        if value and any(
            not 0 <= v <= 1 for v in (value.start, value.end, value.middle) if v is not None
        ):
            raise ValueError("energy curve must be between 0 and 1")
        return value


KEYS = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
    "E#": 5,
    "Fb": 4,
    "Cb": 11,
    "B#": 0,
}


def harmonic(a: Track, b: Track) -> float:
    x, y = a.features, b.features
    if x.key not in KEYS or y.key not in KEYS or not x.scale or not y.scale:
        return 0.5
    # Convert minor to its relative major; neighbours on circle of fifths.
    ka = (KEYS[x.key] + (3 if x.scale == "minor" else 0)) % 12
    kb = (KEYS[y.key] + (3 if y.scale == "minor" else 0)) % 12
    return 1.0 if (ka - kb) % 12 in {0, 5, 7} else 0.0


def artists(track: Track) -> set[str]:
    return {normalize(a.name) for a in track.recording.artists}


def admissible(path: tuple[Track, ...], track: Track, c: PlaylistConstraints) -> bool:
    if any(recording_keys(track) & recording_keys(t) for t in path):
        return False
    names = artists(track)
    counts = Counter(name for t in path for name in artists(t))
    if any(counts[name] >= c.rules.max_tracks_per_artist for name in names):
        return False
    return (
        not any(names & artists(t) for t in path[-c.rules.artist_gap :])
        if c.rules.artist_gap
        else True
    )


def quality(path: tuple[Track, ...], c: PlaylistConstraints) -> float:
    total = sum(t.recording.duration_ms or 0 for t in path)
    if not total:
        return -math.inf
    elapsed = 0
    value = 0.0
    seeds = {normalize(a) for a in c.seed.artists}
    for i, t in enumerate(path):
        # Curve is anchored to the requested duration, not a variable track count.
        p = min(1, elapsed / (c.duration * 1000))
        f = t.features
        if c.tempo:
            value += max(0, 1 - abs(f.bpm - c.tempo.at(p)) / 50) if f.bpm else 0.3
        if c.energy:
            value += 1 - abs(f.energy - c.energy.at(p)) if f.energy is not None else 0.3
        if c.mood:
            value += f.mood.get(c.mood.at(p), 0.3)
        if seeds and seeds & artists(t):
            value += 0.35
        if i:
            prev = path[i - 1]
            if prev.features.bpm and f.bpm:
                value += c.transitions.bpm_smoothness * max(
                    0, 1 - abs(f.bpm - prev.features.bpm) / 40
                )
            value += c.transitions.harmonic_key * harmonic(prev, t)
            if prev.genres and t.genres:
                value += c.transitions.genre_diversity * (
                    1
                    - len(set(prev.genres) & set(t.genres)) / len(set(prev.genres) | set(t.genres))
                )
        elapsed += t.recording.duration_ms or 0
    coverage = min(1, total / (c.duration * 1000))
    return 6 * coverage + value / len(path)


def compile_playlist(c: PlaylistConstraints, candidates: list[Track]) -> Playlist:
    if len(candidates) > 1000:
        raise ValueError("at most 1000 candidates are supported; narrow the library first")
    target = c.duration * 1000
    # Reject contradictory snapshots rather than make input order a hidden tiebreaker.
    unique: dict[str, Track] = {}
    for track in candidates:
        if track.id in unique and unique[track.id] != track:
            raise ValueError(f"conflicting candidate snapshots for track ID {track.id}")
        unique[track.id] = track
    pool = []
    for t in sorted(unique.values(), key=lambda t: t.id):
        r, f = t.recording, t.features
        if not r.duration_ms or r.duration_ms > target:
            continue
        if c.rules.avoid_live and Version.LIVE in r.versions:
            continue
        if c.rules.avoid_remixes and Version.REMIX in r.versions:
            continue
        # Unknown explicit metadata cannot satisfy a clean-only constraint.
        if c.rules.explicit is not None and t.explicit is not c.rules.explicit:
            continue
        if c.rules.missing_features == "exclude" and (
            (c.tempo and f.bpm is None)
            or (c.energy and f.energy is None)
            or (c.mood and not f.mood)
            or (c.transitions.harmonic_key and (not f.key or not f.scale))
        ):
            continue
        pool.append(t)

    def rank(path):
        tie = hashlib.sha256(
            (str(c.random_seed) + "|" + "|".join(t.id for t in path)).encode()
        ).hexdigest()
        return (-quality(path, c), tie)

    names = {t.id: artists(t) for t in pool}
    identities = {t.id: recording_keys(t) for t in pool}
    beam: list[tuple[Track, ...]] = [()]
    best: tuple[Track, ...] = ()
    for _ in range(min(c.max_tracks, len(pool))):
        expanded = []
        for path in beam:
            duration = sum(t.recording.duration_ms or 0 for t in path)
            used = set().union(*(identities[t.id] for t in path))
            counts = Counter(name for t in path for name in names[t.id])
            recent = (
                set().union(*(names[t.id] for t in path[-c.rules.artist_gap :]))
                if c.rules.artist_gap
                else set()
            )
            for t in pool:
                if (
                    duration + t.recording.duration_ms <= target
                    and not identities[t.id] & used
                    and not recent & names[t.id]
                    and all(counts[name] < c.rules.max_tracks_per_artist for name in names[t.id])
                ):
                    expanded.append((*path, t))
        if not expanded:
            break
        ranked = sorted(expanded, key=rank)
        # Keep some shorter prefixes: retaining only the fullest prefixes can
        # strand the beam with a gap smaller than every remaining recording.
        reserve = c.beam_width // 4
        shorter = (
            sorted(
                expanded,
                key=lambda path: (sum(t.recording.duration_ms for t in path), rank(path)),
            )[:reserve]
            if reserve
            else []
        )
        chosen = {}
        for path in [*ranked[: c.beam_width - reserve], *shorter, *ranked]:
            chosen.setdefault(tuple(t.id for t in path), path)
            if len(chosen) == c.beam_width:
                break
        beam = sorted(chosen.values(), key=rank)
        if not best or rank(beam[0]) < rank(best):
            best = beam[0]
    # Swapping preserves selected recordings, duration and artist counts; only
    # recent-artist separation needs rechecking. Cache names across expansions.
    best_rank = rank(best)
    for _ in range(2):
        improved = False
        for i in range(len(best)):
            for j in range(i + 1, len(best)):
                trial = list(best)
                trial[i], trial[j] = trial[j], trial[i]
                gap = c.rules.artist_gap
                separated = not gap or all(
                    not names[t.id] & names[previous.id]
                    for k, t in enumerate(trial)
                    for previous in trial[max(0, k - gap) : k]
                )
                if separated and (trial_rank := rank(tuple(trial))) < best_rank:
                    best = tuple(trial)
                    best_rank = trial_rank
                    improved = True
        if not improved:
            break
    duration = sum(t.recording.duration_ms or 0 for t in best)
    warnings = []
    if duration < target * 0.95:
        warnings.append(
            f"underfilled:{duration // 1000}/{c.duration}s; constraints or candidate library limit coverage"
        )
    if any(
        (c.tempo and not t.features.bpm)
        or (c.mood and not t.features.mood)
        or (c.energy and t.features.energy is None)
        or (c.transitions.harmonic_key and (not t.features.key or not t.features.scale))
        for t in best
    ):
        warnings.append(
            "missing_features: curves or transitions are only partially evaluated; no features were inferred"
        )
    digest = hashlib.sha256(
        (c.model_dump_json() + "|".join(t.id for t in best)).encode()
    ).hexdigest()[:24]
    return Playlist(id=f"compiled:{digest}", title=c.title, tracks=best, warnings=tuple(warnings))
