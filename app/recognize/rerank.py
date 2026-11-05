"""Second-stage printing discriminator for the ambiguous verdict.

Fusion's dense signals are dominated by the artwork, so two printings of the same
artwork land within ``CONFIDENT_MARGIN`` of each other and the pipeline reports
:attr:`~app.models.RecognitionStatus.AMBIGUOUS`. This module resolves that tie by
re-scoring it on the only regions that can differ between printings of one
artwork: the bottom strip (collector number, set code, illustrator/copyright
line) and the set-symbol zone. The artwork window is deliberately ignored — it is
identical by construction, so it can only add noise.

Comparison reuses the perceptual-hash family from :mod:`app.signals.hashes`, so
the discriminator needs no model: one reference-image decode plus fifteen small
hashes per candidate, over a handful of candidates.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import NamedTuple

import numpy as np

from app.core import constants
from app.core.constants import ERA_MODERN, ERA_WOTC
from app.models import Candidate
from app.signals.hashes import compute_hashes, hex_to_bits
from app.vision import regions
from app.vision.imaging import load_image
from app.vision.rectify import center_crop_to_card

logger = logging.getLogger(__name__)

# A query carries no era prior, so both era layouts' symbol zones are compared
# and the best-agreeing pair wins (see _printing_score).
_SYMBOL_ERAS = (ERA_MODERN, ERA_WOTC)


class _RegionBits(NamedTuple):
    """Hash bits of the regions that separate printings of one artwork.

    Attributes:
        strip: Bottom-strip hash family, kind → flat bit array.
        symbols: One hash family per entry of :data:`_SYMBOL_ERAS`.
    """

    strip: dict[str, np.ndarray]
    symbols: tuple[dict[str, np.ndarray], ...]


def _hash_bits(image: np.ndarray) -> dict[str, np.ndarray]:
    """Return one crop's hash family as unpacked bit arrays.

    Bits are unpacked once per crop rather than per comparison: the query's
    crops are scored against every candidate.
    """
    return {kind: hex_to_bits(value) for kind, value in compute_hashes(image).items()}


def _region_bits(card: np.ndarray) -> _RegionBits:
    """Hash the discriminating regions of one canonical-size card."""
    return _RegionBits(
        strip=_hash_bits(regions.bottom_strip(card)),
        symbols=tuple(_hash_bits(regions.symbol_zone(card, era)) for era in _SYMBOL_ERAS),
    )


def _similarity(left: Mapping[str, np.ndarray], right: Mapping[str, np.ndarray]) -> float:
    """Return the mean Hamming similarity in ``[0, 1]`` across the hash family.

    Retrieval keeps the *closest* hash kind, because one surviving kind is enough
    to find a card among twenty thousand. Discrimination is the opposite problem:
    the two crops are near-identical by construction, so every kind's small
    disagreement is signal and the family is averaged instead of minimized.
    """
    total = 0.0
    for kind, bits in left.items():
        distance = float(np.count_nonzero(bits != right[kind]))
        total += 1.0 - distance / float(constants.HASH_BITS)
    return total / float(len(left))


def _printing_score(query: _RegionBits, reference: _RegionBits) -> float:
    """Combine strip and symbol-zone similarity into one printing score.

    The symbol term is the best of the two era layouts: whichever zone actually
    holds the symbol on this pair of images is the one that can agree strongly,
    and neither side of the comparison knows its own era.
    """
    strip = _similarity(query.strip, reference.strip)
    symbol = max(
        _similarity(mine, theirs)
        for mine, theirs in zip(query.symbols, reference.symbols, strict=True)
    )
    return constants.RERANK_STRIP_WEIGHT * strip + constants.RERANK_SYMBOL_WEIGHT * symbol


def _tie_group_size(candidates: list[Candidate]) -> int:
    """Return the length of the leading run of candidates sharing one card name."""
    if not candidates:
        return 0
    name = candidates[0].card.name
    limit = min(len(candidates), constants.RERANK_TIE_CANDIDATES)
    size = 0
    while size < limit and candidates[size].card.name == name:
        size += 1
    return size


def _reference_bits(card_id: str, image_paths: Mapping[str, str]) -> _RegionBits | None:
    """Hash one candidate's cached reference printing, or ``None`` if unreadable.

    A reference that cannot be read leaves the candidate unranked instead of
    failing the scan: the user is owed the fused answer either way.
    """
    path = image_paths.get(card_id)
    if not path:
        logger.warning("no cached reference image for %s; cannot rerank it", card_id)
        return None
    try:
        image = load_image(path)
    except (OSError, ValueError) as error:
        logger.warning("reference image for %s unusable: %s", card_id, error)
        return None
    return _region_bits(center_crop_to_card(image))


def rerank_printings(
    card: np.ndarray,
    candidates: list[Candidate],
    image_paths: Mapping[str, str],
) -> tuple[list[Candidate], float]:
    """Re-rank the leading same-artwork tie by its printing-specific regions.

    Args:
        card: The rectified query card (canonical size, ``uint8`` RGB).
        candidates: Fused candidates, best first. Only the leading run sharing
            ``candidates[0].card.name`` is re-ranked, capped at
            ``RERANK_TIE_CANDIDATES``; every other candidate keeps its relative
            order and stays below that run.
        image_paths: Card id → cached reference image path. A candidate with no
            entry, or whose file cannot be read, is left unranked and sorts below
            the printings that were scored.

    Returns:
        ``(ordered, margin)``. ``ordered`` holds the same :class:`Candidate`
        objects, possibly reordered, with ``confidence`` and ``per_signal``
        untouched — those remain fusion's numbers. ``margin`` is the winner's
        lead over the runner-up in ``[0, 1]``, or ``0.0`` when the discriminator
        could not decide: fewer than two same-name candidates, fewer than two
        readable references, or no reference reaching ``RERANK_MIN_SCORE`` (in
        the last case the input order is returned unchanged).
    """
    group_size = _tie_group_size(candidates)
    if group_size < 2:
        return list(candidates), 0.0

    query = _region_bits(card)
    scored: list[tuple[float, int, Candidate]] = []
    unscored: list[Candidate] = []
    for position, candidate in enumerate(candidates[:group_size]):
        reference = _reference_bits(candidate.card.card_id, image_paths)
        if reference is None:
            unscored.append(candidate)
            continue
        scored.append((_printing_score(query, reference), position, candidate))

    if not scored or max(score for score, _, _ in scored) < constants.RERANK_MIN_SCORE:
        # Nothing readable resembles this capture's own strip and symbol (glare,
        # a bad rectification, a stale cache): reordering would be noise.
        logger.debug("printing rerank found no usable reference among %d", group_size)
        return list(candidates), 0.0

    # Descending score, original position as the tiebreak: printings the regions
    # cannot separate keep fusion's order rather than the store's.
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    ordered = [candidate for _, _, candidate in scored] + unscored + candidates[group_size:]
    margin = scored[0][0] - scored[1][0] if len(scored) > 1 else 0.0
    logger.debug(
        "printing rerank: %s leads by %.3f over %d scored printings",
        ordered[0].card.card_id,
        margin,
        len(scored),
    )
    return ordered, margin
