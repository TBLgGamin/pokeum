"""Name signal: read the card's title band and match it to catalogue names.

The printed name is the largest, cleanest text on a card, and it survives where
the tiny collector-number strip does not (glare on the bottom edge, a strip cut
off by the frame). It cannot identify a printing on its own — "Pikachu" names
well over a hundred cards — so it is a soft signal: fusion lifts every card of
the matched name family a little and never pushes anyone down.

Matching runs on a space-free, accent-free lowercase key so OCR quirks like a
dropped space ("Squawkabillyex") still hit exactly. Every contiguous token
window of every line is tried, so stage labels and HP text sharing a line with
the name ("BASIC Ditto", "TAGE2 Slaking") fall away; the longest exact hit wins
("Pikachu on the Ball" over "Pikachu"). Only when nothing hits exactly is a
fuzzy match attempted, and only for keys long enough to be distinctive.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

import numpy as np

from app.core import constants
from app.models import NameObservation
from app.signals.base import OcrEngine
from app.vision import regions

logger = logging.getLogger(__name__)

# Lines that carry *another* card's name ("Evolves from Vigoroth", WOTC's "Put
# Alakazam on the Stage 1 card") must never be matched.
_SKIP_LINE = re.compile(r"^\s*(evolves|put\b)", re.IGNORECASE)
# Tokens that sit on the title line but are never a name by themselves.
_STOP_TOKENS = frozenset(
    {"basic", "stage", "stage1", "stage2", "tage1", "tage2", "hp", "trainer", "stadium", "item"}
    | {"supporter", "tool", "pokemon", "ex", "v", "gx", "vmax", "vstar"}
)
_MAX_WINDOW_TOKENS = 5


def name_words(text: str) -> list[str]:
    """Return the lowercase, accent-free alphanumeric words of ``text``."""
    folded = unicodedata.normalize("NFKD", text)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", ascii_only.replace("'", ""))


def name_key(text: str) -> str:
    """Return the space-free matching key of a name (``"Pikachu ex"`` → ``pikachuex``)."""
    return "".join(name_words(text))


class NameMatcher:
    """Matches OCR title text against the catalogue's card names."""

    def __init__(self, cards: Iterable[tuple[str, str]]) -> None:
        """Index ``(card_id, name)`` pairs by name key and by name word prefix."""
        self._ids_by_key: dict[str, list[str]] = defaultdict(list)
        self._words_by_key: dict[str, tuple[str, ...]] = {}
        self._display: dict[str, str] = {}
        for card_id, name in cards:
            words = tuple(name_words(name))
            if not words:
                continue
            key = "".join(words)
            self._ids_by_key[key].append(card_id)
            self._words_by_key[key] = words
            self._display.setdefault(key, name)
        self._keys_by_length: dict[int, list[str]] = defaultdict(list)
        for key in self._ids_by_key:
            self._keys_by_length[len(key)].append(key)

    def __len__(self) -> int:
        """Return the number of distinct names indexed."""
        return len(self._ids_by_key)

    def match(self, lines: list[tuple[str, float]]) -> NameObservation | None:
        """Return the best catalogue name found in OCR title lines, or ``None``."""
        exact: tuple[int, float, str, str] | None = None  # (key length, conf, key, raw)
        windows: list[tuple[str, float, str]] = []
        for text, conf in lines:
            if _SKIP_LINE.match(text):
                continue
            words = name_words(text)
            for start in range(len(words)):
                for end in range(start + 1, min(len(words), start + _MAX_WINDOW_TOKENS) + 1):
                    window = words[start:end]
                    if all(word in _STOP_TOKENS or word.isdigit() for word in window):
                        continue
                    key = "".join(window)
                    if key in self._ids_by_key:
                        candidate = (len(key), conf, key, text)
                        if exact is None or candidate[:2] > exact[:2]:
                            exact = candidate
                    elif len(key) >= constants.NAME_FUZZY_MIN_LENGTH:
                        windows.append((key, conf, text))
        if exact is not None:
            _, conf, key, raw = exact
            return self._observation(key, raw, similarity=1.0, confidence=conf)
        return self._fuzzy(windows)

    def family_ids(self, key: str) -> frozenset[str]:
        """Return the cards whose name contains ``key``'s words as a whole-word run.

        "Charizard" covers "Charizard ex" and "Charizard V" (the suffix is often
        a separate glyph OCR misses) and "Team Magma's Baltoy"-style owner
        prefixes (often outside the read), but "Mew" does not cover "Mewtwo".
        """
        words = self._words_by_key.get(key)
        if words is None:
            return frozenset()
        size = len(words)
        ids: list[str] = []
        for other, other_words in self._words_by_key.items():
            if any(
                other_words[start : start + size] == words
                for start in range(len(other_words) - size + 1)
            ):
                ids.extend(self._ids_by_key[other])
        return frozenset(ids)

    def _fuzzy(self, windows: list[tuple[str, float, str]]) -> NameObservation | None:
        """Return the closest fuzzy match among long-enough windows, if any."""
        best: tuple[float, float, str, str] | None = None  # (similarity, conf, key, raw)
        for key, conf, raw in windows:
            low = int(len(key) * 0.8)
            high = int(len(key) * 1.25) + 1
            pool = [k for length in range(low, high + 1) for k in self._keys_by_length[length]]
            hits = difflib.get_close_matches(
                key, pool, n=1, cutoff=constants.NAME_MATCH_MIN_SIMILARITY
            )
            if not hits:
                continue
            similarity = difflib.SequenceMatcher(None, key, hits[0]).ratio()
            candidate = (similarity, conf, hits[0], raw)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is None:
            return None
        similarity, conf, key, raw = best
        return self._observation(key, raw, similarity=similarity, confidence=conf)

    def _observation(
        self, key: str, raw: str, *, similarity: float, confidence: float
    ) -> NameObservation:
        """Build the observation for a matched key, with its name family."""
        return NameObservation(
            raw_text=raw,
            name=self._display[key],
            similarity=similarity,
            confidence=confidence,
            card_ids=self.family_ids(key),
        )


def read_name(engine: OcrEngine, card: np.ndarray, matcher: NameMatcher) -> NameObservation | None:
    """OCR a rectified card's title band and match it to a catalogue name."""
    lines = engine.read_text(regions.title_band(card))
    logger.debug("name ocr raw lines: %s", [(text, round(conf, 2)) for text, conf in lines])
    return matcher.match(lines)
