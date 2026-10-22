from difflib import SequenceMatcher

from pydantic import Field

from harmonia.domain.models import Model, Track
from harmonia.domain.text import base_title, normalize, version_details


class Match(Model):
    track: Track
    confidence: float = Field(ge=0, le=1)
    reasons: tuple[str, ...]


class MatchResult(Model):
    match: Match | None = None
    alternatives: tuple[Match, ...] = ()
    needs_review: bool = True
    reasons: tuple[str, ...] = ()


def edition(track: Track) -> frozenset[str]:
    return version_details(track.recording.title) | (
        version_details(track.album.title) if track.album else frozenset()
    )


def identity(track: Track) -> str:
    r = track.recording
    # Version remains part of identity even when a catalogue reuses an ISRC.
    code = r.musicbrainz_id or r.isrc
    if not code:
        code = base_title(r.title) + ":" + "|".join(sorted(normalize(a.name) for a in r.artists))
    return code + ":" + ",".join(sorted(r.versions)) + ":" + "|".join(sorted(edition(track)))


def recording_keys(track: Track) -> frozenset[str]:
    """Conservative duplicate keys across partially enriched catalogue entries."""
    r = track.recording
    suffix = ":" + ",".join(sorted(r.versions)) + ":" + "|".join(sorted(edition(track)))
    keys = {
        "text:"
        + base_title(r.title)
        + ":"
        + "|".join(sorted(normalize(a.name) for a in r.artists))
        + suffix
    }
    if r.isrc:
        keys.add("isrc:" + r.isrc + suffix)
    if r.musicbrainz_id:
        keys.add("mbid:" + r.musicbrainz_id + suffix)
    return frozenset(keys)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def score(source: Track, target: Track) -> Match | None:
    a, b = source.recording, target.recording
    if a.versions != b.versions:
        return None
    # Identifier conflicts are evidence against a match, not an excuse to fuzzy-match.
    if a.musicbrainz_id and b.musicbrainz_id and a.musicbrainz_id != b.musicbrainz_id:
        return None
    title = similarity(base_title(a.title), base_title(b.title))
    artists_a = {normalize(x.name) for x in a.artists}
    artists_b = {normalize(x.name) for x in b.artists}
    artist = max(similarity(x, y) for x in artists_a for y in artists_b)
    exact_isrc = bool(a.isrc and a.isrc == b.isrc)
    exact_mbid = bool(a.musicbrainz_id and a.musicbrainz_id == b.musicbrainz_id)
    # Even exact identifiers can be bad catalogue metadata.
    if title < 0.55 or artist < 0.65:
        return None
    reasons = ["same_version"]
    delta = None
    if a.duration_ms and b.duration_ms:
        delta = abs(a.duration_ms - b.duration_ms)
        if delta > max(12_000, a.duration_ms * 0.08):
            return None
    if exact_isrc:
        confidence = 0.99
        reasons.insert(0, "exact_isrc")
    elif exact_mbid:
        confidence = 0.98
        reasons.insert(0, "exact_musicbrainz_recording")
    else:
        if a.isrc and b.isrc and a.isrc != b.isrc:
            reasons.append("different_isrc")
        confidence = 0.51 * title + 0.32 * artist
        if title == 1 and artists_a == artists_b:
            reasons.append("normalized_artist_title")
        else:
            reasons.append("fuzzy_artist_title")
        if delta is not None:
            confidence += 0.10 * max(0, 1 - delta / 12_000)
        if source.album and target.album:
            album = similarity(normalize(source.album.title), normalize(target.album.title))
            confidence += 0.04 * album
            if album == 1:
                reasons.append("same_album")
        if artists_a != artists_b:
            confidence = min(confidence, 0.84)
            reasons.append("artist_credits_differ")
        if a.isrc and b.isrc and a.isrc != b.isrc:
            confidence = min(confidence, 0.86)
    if delta is not None:
        reasons.append(f"duration_delta_ms:{delta}")
        if delta > 5000:
            confidence = min(confidence, 0.86)
            reasons.append("duration_requires_review")
    if title < 0.9 or artists_a != artists_b:
        confidence = min(confidence, 0.84)
    if edition(source) != edition(target):
        confidence = min(confidence, 0.84)
        reasons.append("version_details_require_review")
    return Match(track=target, confidence=round(min(confidence, 1), 6), reasons=tuple(reasons))


def match_track(source: Track, candidates: list[Track], threshold: float = 0.90) -> MatchResult:
    scored = {c.id: result for c in candidates if (result := score(source, c)) is not None}
    ordered = sorted(scored.values(), key=lambda m: (-m.confidence, m.track.id))
    if not ordered or ordered[0].confidence < 0.65:
        return MatchResult(reasons=("no_compatible_candidate",))
    best = ordered[0]
    ambiguous = len(ordered) > 1 and best.confidence - ordered[1].confidence < 0.025
    return MatchResult(
        match=best,
        alternatives=tuple(ordered[1:6]),
        needs_review=best.confidence < threshold or ambiguous,
        reasons=("ambiguous_candidates",) if ambiguous else (),
    )
