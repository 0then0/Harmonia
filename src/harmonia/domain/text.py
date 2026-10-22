import re
import unicodedata

# Markers are deliberately boundary-aware: "Deliver" must not become "live".
VERSION_PATTERNS = {
    "live": r"\blive\b|\bconcert\b|\bконцерт\w*",
    "remix": r"\bremix\b|\brework\b|\bремикс\b",
    "remaster": r"\bremaster(?:ed)?\b|\bремастер\w*",
    "acoustic": r"\bacoustic\b|\bunplugged\b|\bакуст\w*",
    "instrumental": r"\binstrumental\b|\bkaraoke\b|\bинструментал\w*",
    "radio_edit": r"\bradio\s+(?:edit|version|mix)\b",
    "extended": r"\bextended(?:\s+(?:mix|version|edit))?\b",
    "sped_up": r"\bsped[\s-]*up\b|\bnightcore\b",
    "slowed": r"\bslowed\b|\bslowed[\s-]*down\b",
    "demo": r"\bdemo\b",
}


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("ё", "е"))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def detect_versions(title: str) -> set[str]:
    # Only descriptors after a separator or within brackets. Bare titles such as
    # "Live Forever" and "Acoustic Dreams" are not version annotations.
    descriptors = re.findall(r"\(([^)]*)\)|\[([^]]*)\]|\s[-–—]\s(.+)$", title)
    text = " ".join(part for match in descriptors for part in match).casefold()
    return {name for name, pattern in VERSION_PATTERNS.items() if re.search(pattern, text)}


def base_title(title: str) -> str:
    def strip(match: re.Match) -> str:
        return " " if detect_versions(f"x ({match.group(0)})") else match.group(0)

    title = re.sub(r"\([^)]*\)|\[[^]]*\]|\s[-–—]\s.+$", strip, title)
    return normalize(title)


def version_details(title: str) -> frozenset[str]:
    """Retain edition identity (remixer, venue, year) after removing generic tags."""
    details = set()
    for match in re.findall(r"\(([^)]*)\)|\[([^]]*)\]|\s[-–—]\s(.+)$", title):
        text = " ".join(match).casefold()
        if not detect_versions(f"x ({text})"):
            continue
        for pattern in VERSION_PATTERNS.values():
            text = re.sub(pattern, " ", text)
        text = re.sub(r"\b(?:version|mix|edit)\b", " ", text)
        if detail := normalize(text):
            details.add(detail)
    return frozenset(details)
