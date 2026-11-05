"""Perceptual-hash signal.

Perceptual hashes are cheap and match clean, well-aligned images extremely well;
they degrade under glare and steep angles, which is why fusion also leans on
embeddings and OCR. We compute a small family per card — a DCT hash (``phash``),
a gradient hash (``dhash``), and a per-RGB-channel ``phash`` for colour
sensitivity — and score a query by its closest match across the family.

All hashes share a bit length (``HASH_BITS``), so matching is one XOR-and-popcount
over a bit matrix: fast enough to scan the whole catalogue with NumPy, no ANN
index required.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import imagehash
import numpy as np
from PIL import Image

from app.core import constants
from app.models import SignalScore

logger = logging.getLogger(__name__)

# Grayscale hashes plus one phash per colour channel.
HASH_KINDS = ("phash", "dhash", "phash_r", "phash_g", "phash_b")
_CHANNEL_INDEX = {"phash_r": 0, "phash_g": 1, "phash_b": 2}


def _to_pil(image: np.ndarray | Image.Image) -> Image.Image:
    """Coerce a NumPy RGB array or PIL image to an RGB PIL image."""
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    return Image.fromarray(image.astype(np.uint8), mode="RGB")


def compute_hashes(image: np.ndarray | Image.Image) -> dict[str, str]:
    """Compute the perceptual-hash family for one image.

    Args:
        image: RGB image as a NumPy array or PIL image.

    Returns:
        Mapping of hash kind to its hex string (see :data:`HASH_KINDS`).
    """
    pil = _to_pil(image)
    size = constants.HASH_SIZE
    result = {
        "phash": str(imagehash.phash(pil, hash_size=size)),
        "dhash": str(imagehash.dhash(pil, hash_size=size)),
    }
    channels = pil.split()
    for kind, idx in _CHANNEL_INDEX.items():
        result[kind] = str(imagehash.phash(channels[idx], hash_size=size))
    return result


def hex_to_bits(hex_str: str) -> np.ndarray:
    """Convert an ImageHash hex string to a flat ``uint8`` bit array."""
    flat = imagehash.hex_to_hash(hex_str).hash.flatten()
    return flat.astype(np.uint8)


def _hex_to_packed(hex_str: str) -> np.ndarray:
    """Decode a hash hex string directly into packed ``uint8`` bytes."""
    return np.frombuffer(bytes.fromhex(hex_str), dtype=np.uint8)


class HashIndex:
    """In-memory bit matrices for hash matching across the catalogue."""

    def __init__(self, card_ids: list[str], matrices: dict[str, np.ndarray]) -> None:
        """Create an index from parallel card ids and per-kind bit matrices.

        Args:
            card_ids: Row order for every matrix.
            matrices: Kind → ``(N, HASH_BITS / 8)`` packed ``uint8`` matrix.
                A kind whose rows are absent for some cards is simply omitted.
        """
        self.card_ids = card_ids
        self.matrices = matrices
        # Built on first subset lookup only; full scans never need it.
        self._rows: dict[str, int] | None = None

    @classmethod
    def from_store_rows(cls, rows: list[tuple[str, dict[str, str]]]) -> HashIndex:
        """Build an index from ``(card_id, {kind: hex})`` rows.

        Only cards that carry the full grayscale ``phash`` are indexed; a kind is
        included when every indexed card has it, keeping matrices rectangular.

        Args:
            rows: Stored hash rows, typically ``ReferenceStore.iter_hashes()``.

        Returns:
            A ready-to-query :class:`HashIndex` (possibly empty).
        """
        usable = [(cid, values) for cid, values in rows if values.get("phash")]
        card_ids = [cid for cid, _ in usable]
        matrices: dict[str, np.ndarray] = {}
        for kind in HASH_KINDS:
            if usable and all(values.get(kind) for _, values in usable):
                # Direct hex decoding avoids constructing one ImageHash and a
                # 256-byte unpacked row per card during service startup.
                matrices[kind] = np.vstack([_hex_to_packed(values[kind]) for _, values in usable])
        return cls(card_ids, matrices)

    def __len__(self) -> int:
        """Return the number of indexed cards."""
        return len(self.card_ids)

    def query(
        self, image: np.ndarray | Image.Image, top_k: int = constants.RETRIEVAL_SHORTLIST
    ) -> list[SignalScore]:
        """Score the catalogue against one image by closest hash match.

        Args:
            image: Query image (rectified card).
            top_k: Number of best matches to return.

        Returns:
            The ``top_k`` highest-scoring cards, best first.
        """
        return self.query_hashes(compute_hashes(image), top_k=top_k)

    def query_hashes(
        self, query_hashes: dict[str, str], top_k: int = constants.RETRIEVAL_SHORTLIST
    ) -> list[SignalScore]:
        """Score the catalogue against an already-computed hash family.

        For each card the Hamming distance is taken per hash kind and the minimum
        across kinds is kept, then mapped to a similarity in ``[0, 1]``. Callers
        that need a second pass over a subset compute the family once and reuse
        it here and in :meth:`scores_for`.

        Args:
            query_hashes: The query's ``{kind: hex}`` family.
            top_k: Number of best matches to return.

        Returns:
            The ``top_k`` highest-scoring cards, best first.
        """
        scores = self._similarities(query_hashes)
        if scores is None:
            return []
        order = np.argsort(scores)[::-1][:top_k]
        return [SignalScore(self.card_ids[i], float(scores[i])) for i in order]

    def scores_for(
        self, query_hashes: dict[str, str], card_ids: Iterable[str]
    ) -> list[SignalScore]:
        """Score a named subset of cards against an already-computed hash family.

        Args:
            query_hashes: The query's ``{kind: hex}`` family.
            card_ids: Cards to score; ids absent from the index are skipped.

        Returns:
            One score per known id, best first.
        """
        scores = self._similarities(query_hashes)
        if scores is None:
            return []
        rows = self._row_index()
        picked = [(cid, rows[cid]) for cid in card_ids if cid in rows]
        picked.sort(key=lambda item: scores[item[1]], reverse=True)
        return [SignalScore(cid, float(scores[row])) for cid, row in picked]

    def _similarities(self, query_hashes: dict[str, str]) -> np.ndarray | None:
        """Return per-card similarity in ``[0, 1]``, or ``None`` when unusable."""
        if not self.card_ids or not self.matrices:
            return None
        best = np.full(len(self.card_ids), constants.HASH_BITS, dtype=np.int32)
        for kind, matrix in self.matrices.items():
            if not query_hashes.get(kind):
                continue
            packed = _hex_to_packed(query_hashes[kind])
            distances = np.bitwise_count(np.bitwise_xor(matrix, packed)).sum(axis=1, dtype=np.int32)
            best = np.minimum(best, distances)
        return 1.0 - best.astype(np.float32) / float(constants.HASH_BITS)

    def _row_index(self) -> dict[str, int]:
        """Return a lazily built ``{card_id: row}`` map for subset lookups."""
        if self._rows is None:
            self._rows = {cid: row for row, cid in enumerate(self.card_ids)}
        return self._rows
