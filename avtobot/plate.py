"""Guess a Russian plate from whatever a person typed."""

from __future__ import annotations

import re

LETTERS = set("АВЕКМНОРСТУХ")
LATIN = {
    "A": "А",
    "B": "В",
    "E": "Е",
    "K": "К",
    "M": "М",
    "H": "Н",
    "N": "Н",
    "O": "О",
    "P": "Р",
    "C": "С",
    "T": "Т",
    "Y": "У",
    "U": "У",
    "X": "Х",
}
# Bonus only. An unknown new code still passes the shape check.
KNOWN_REGIONS = {
    "01", "02", "102", "702", "03", "103", "04", "05", "06", "07", "08", "09",
    "10", "11", "12", "13", "113", "14", "15", "16", "116", "716", "17", "18",
    "19", "21", "121", "22", "122", "23", "93", "123", "193", "24", "84", "88",
    "124", "25", "125", "26", "126", "27", "28", "29", "30", "31", "32", "33",
    "34", "134", "35", "36", "136", "37", "38", "85", "138", "39", "91", "40",
    "41", "82", "42", "142", "43", "44", "45", "46", "47", "147", "48", "49",
    "50", "90", "150", "190", "750", "51", "52", "152", "53", "54", "154",
    "754", "55", "155", "56", "156", "57", "58", "59", "81", "159", "60", "61",
    "161", "761", "62", "63", "163", "763", "64", "164", "65", "66", "96",
    "196", "67", "68", "69", "70", "71", "72", "172", "73", "173", "74", "174",
    "774", "75", "80", "76", "77", "97", "99", "177", "197", "199", "777",
    "797", "799", "78", "98", "178", "198", "79", "82", "83", "86", "186",
    "87", "89", "92", "192", "94", "95",
}

SCHEMAS = (
    ("car", "LDDDLL"),
    ("trailer", "LLDDDD"),
    ("moto", "DDDDLL"),
)


def _fold(char: str) -> str:
    upper = char.upper().replace("Ё", "Е")
    return LATIN.get(upper, upper)


def _repair(char: str, kind: str) -> tuple[str, int] | None:
    if kind == "L":
        if char in LETTERS:
            return char, 0
        if char == "0":
            return "О", 1
        if char == "8":
            return "В", 1
        return None
    if char.isdigit():
        return char, 0
    if char == "О":
        return "0", 1
    if char == "З":
        return "3", 1
    return None


def _region_ok(region: str) -> bool:
    if not region.isdigit() or region == "00" or len(region) not in {2, 3}:
        return False
    if len(region) == 3 and region[0] not in {"1", "2", "7", "9"}:
        return False
    return True


def _score_window(window: str, whole: bool, only: str | None = None) -> tuple[int, str, str] | None:
    best: tuple[int, str, str] | None = None
    for kind, pattern in SCHEMAS:
        if only is not None and kind != only:
            continue
        if len(window) not in {len(pattern) + 2, len(pattern) + 3}:
            continue
        body = window[: len(pattern)]
        region = window[len(pattern) :]
        built = []
        repairs = 0
        failed = False
        for char, slot in zip(body, pattern, strict=True):
            fixed = _repair(char, slot)
            if fixed is None:
                failed = True
                break
            built.append(fixed[0])
            repairs += fixed[1]
        if failed or repairs > 2:
            continue
        region_built = []
        for char in region:
            fixed = _repair(char, "D")
            if fixed is None:
                failed = True
                break
            region_built.append(fixed[0])
            repairs += fixed[1]
        if failed or repairs > 2:
            continue
        region_text = "".join(region_built)
        if not _region_ok(region_text):
            continue
        plate = "".join(built) + region_text
        score = 100 - repairs * 4
        if region_text in KNOWN_REGIONS:
            score += 6
        if kind == "car":
            score += 3
        if whole:
            score += 5
        if best is None or score > best[0]:
            best = (score, plate, kind)
    return best


def _groups(text: str) -> list[str]:
    chunks = re.findall(r"[0-9A-Za-zА-Яа-яЁё]+", text)
    return ["".join(_fold(char) for char in chunk) for chunk in chunks if chunk]


def _structured(parts: list[str]) -> tuple[int, str, str] | None:
    shapes = (
        ((1, 3, 2), "car"),
        ((4, 2), "moto"),
        ((2, 4), "trailer"),
    )
    if len(parts) < 3:
        if len(parts) == 2 and len(parts[1]) in {2, 3}:
            scored = _score_window(parts[0] + parts[1], True)
            if scored is not None:
                return scored[0] + 8, scored[1], scored[2]
        return None
    body, kind = parts[:-1], ""
    lengths = tuple(len(part) for part in body)
    for shape, name in shapes:
        if lengths == shape and len(parts[-1]) in {2, 3}:
            kind = name
            break
    if not kind and len(parts) == 3 and lengths[0] == 1 and lengths[1] == 3 and len(parts[2]) in {4, 5}:
        kind = "car"
    if not kind:
        return None
    scored = _score_window("".join(parts), True, only=kind)
    if scored is None:
        return None
    return scored[0] + 12, scored[1], scored[2]


def _ranked(text: str) -> list[tuple[str, int, str]]:
    raw = re.sub(r"(?i)\b(rus|ru|рф)\b", " ", text or "")
    folded = "".join(_fold(char) for char in raw if char.isalnum())
    found: dict[str, tuple[int, str]] = {}
    groups = _groups(raw)
    for start in range(len(groups)):
        for size in (2, 3, 4):
            grouped = _structured(groups[start : start + size])
            if grouped is None:
                continue
            score, number, kind = grouped
            previous = found.get(number)
            if previous is None or score > previous[0]:
                found[number] = (score, kind)
    for size in (8, 9):
        if len(folded) < size:
            continue
        for start in range(0, len(folded) - size + 1):
            window = folded[start : start + size]
            scored = _score_window(window, whole=(start == 0 and start + size == len(folded)))
            if scored is None:
                continue
            score, number, kind = scored
            previous = found.get(number)
            if previous is None or score > previous[0]:
                found[number] = (score, kind)
    ranked = sorted(found.items(), key=lambda item: (-item[1][0], -len(item[0]), item[0]))
    return [(number, meta[0], meta[1]) for number, meta in ranked]


def candidates(text: str) -> list[tuple[str, str]]:
    return [(number, kind) for number, _score, kind in _ranked(text)]


def choose(text: str) -> list[tuple[str, str]]:
    """One plate when the guess is clear. A few, when two real plates are close."""
    ranked = _ranked(text)
    if not ranked:
        return []
    kept: list[tuple[str, int, str]] = []
    for number, score, kind in ranked:
        shorter = False
        for other, other_score, _other_kind in ranked:
            if other != number and other.startswith(number) and len(other) > len(number) and other_score >= score:
                shorter = True
                break
        if not shorter:
            kept.append((number, score, kind))
    best = kept[0][1]
    close = [(number, kind) for number, score, kind in kept if best - score < 8]
    return close[:3]


def normalize(text: str) -> str | None:
    ranked = _ranked(text)
    if not ranked:
        return None
    return ranked[0][0]


def show(plate: str) -> str:
    for kind, pattern in SCHEMAS:
        if len(plate) >= len(pattern) and _matches(plate, kind):
            return plate[: len(pattern)] + " " + plate[len(pattern) :]
    return plate


def _matches(plate: str, kind: str) -> bool:
    pattern = dict(SCHEMAS)[kind]
    if len(plate) not in {len(pattern) + 2, len(pattern) + 3}:
        return False
    for char, slot in zip(plate, pattern, strict=False):
        if slot == "L" and char not in LETTERS:
            return False
        if slot == "D" and not char.isdigit():
            return False
    return _region_ok(plate[len(pattern) :])
