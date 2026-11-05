"""End-to-end recognition: image in, :class:`RecognitionResult` out.

The :class:`Recognizer` wires the stages together — detect and rectify the card,
run whichever signals are available, fuse them, then assess variants on the
winner. Every heavy collaborator (indexes, embedder, OCR engine, symbol matcher)
is injected, so tests can drive the pipeline with fakes and the API/CLI can build
it once and reuse it.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Executor

import numpy as np

from app.core import constants
from app.models import (
    Candidate,
    CardRef,
    NameObservation,
    OcrObservation,
    RecognitionResult,
    RecognitionStatus,
    SignalScore,
)
from app.recognize import fusion
from app.recognize.rerank import rerank_printings
from app.reference.store import ReferenceStore
from app.signals import hashes
from app.signals.base import Embedder, OcrEngine
from app.signals.embedding import EmbeddingIndex, embed_images
from app.signals.hashes import HashIndex
from app.signals.name import NameMatcher, read_name
from app.signals.ocr import read_card
from app.signals.symbol import SymbolMatcher
from app.variants.assess import assess_variants
from app.vision import regions
from app.vision.detect import detect_card_quad
from app.vision.rectify import card_views, inset_view

logger = logging.getLogger(__name__)

# How many candidates each DEBUG diagnostic line shows per signal and after
# fusion — enough to see who beat whom without flooding the log.
_DEBUG_TOP_N = 5


def _describe(card: CardRef | None, card_id: str) -> str:
    """Return a compact human-readable label for a candidate card."""
    if card is None:
        return card_id
    return f"{card.name} [{card.card_id} {card.number}/{card.number_total or '?'}]"


@dataclasses.dataclass(frozen=True, slots=True)
class _Query:
    """The query's reusable representations, computed once per identify() call."""

    hashes: dict[str, str]
    full: np.ndarray | None
    artwork: np.ndarray | None


@dataclasses.dataclass(frozen=True, slots=True)
class _View:
    """The chosen rectified-card hypothesis and what scoring it already computed."""

    label: str
    card: np.ndarray
    hashes: dict[str, str]
    full: np.ndarray | None


def _top_score(scores: list[SignalScore]) -> float:
    """Return the best score of a ranked shortlist, or 0.0 when it is empty."""
    return scores[0].score if scores else 0.0


def _ranked(result: RecognitionResult) -> list[Candidate]:
    """Return a result's candidates best first, winner included."""
    return ([result.match] if result.match is not None else []) + list(result.alternates)


def _winner_id(result: RecognitionResult) -> str | None:
    """Return the winning card id of a result, or ``None``."""
    return result.match.card.card_id if result.match is not None else None


class Recognizer:
    """Recognizes cards by fusing hash, embedding, OCR, and symbol signals."""

    def __init__(
        self,
        store: ReferenceStore,
        *,
        hash_index: HashIndex,
        embedder: Embedder | None = None,
        emb_full_index: EmbeddingIndex | None = None,
        emb_art_index: EmbeddingIndex | None = None,
        ocr_engine: OcrEngine | None = None,
        symbol_matcher: SymbolMatcher | None = None,
        executor: Executor | None = None,
    ) -> None:
        """Assemble a recognizer from a catalogue and its signal collaborators.

        Args:
            store: Reference catalogue for card resolution and OCR validation.
            hash_index: Perceptual-hash index (may be empty).
            embedder: Encoder used to embed queries; required for embedding signals.
            emb_full_index: Full-card embedding index, or ``None`` if not built.
            emb_art_index: Artwork-crop embedding index, or ``None`` if not built.
            ocr_engine: OCR engine, or ``None`` to skip the OCR signal.
            symbol_matcher: Set-symbol matcher, or ``None`` to skip it.
            executor: When given, OCR runs on it concurrently with the dense
                signals (they release the GIL); ``None`` keeps everything serial.
        """
        self._store = store
        self._hash_index = hash_index
        self._embedder = embedder
        self._emb_full = emb_full_index
        self._emb_art = emb_art_index
        self._ocr = ocr_engine
        self._symbols = symbol_matcher
        self._executor = executor
        # Lazy caches: the catalogue's printed set codes (OCR validation) and
        # its name index (title matching). Building either twice under a race
        # is harmless; both are immutable once built.
        self._known_set_codes: frozenset[str] | None = None
        self._names: NameMatcher | None = None

    @property
    def store(self) -> ReferenceStore:
        """Return the reference catalogue this recognizer reads from."""
        return self._store

    @property
    def has_embeddings(self) -> bool:
        """Return whether embedding indexes are loaded and usable."""
        return (
            self._embedder is not None and self._emb_full is not None and self._emb_art is not None
        )

    @property
    def embedder_id(self) -> str:
        """Return the loaded encoder's identifier, or ``"none"`` when absent."""
        return self._embedder.identifier if self._embedder is not None else "none"

    def identify(
        self,
        image_rgb: np.ndarray,
        *,
        top_k: int = constants.DEFAULT_TOP_K,
        require_detection: bool = False,
        guide_margin: float | None = None,
    ) -> RecognitionResult:
        """Recognize the card in an RGB image.

        Args:
            image_rgb: The image to identify.
            top_k: Maximum candidates to return.
            require_detection: When ``True``, return ``NO_CARD_DETECTED`` if no
                card quad is found instead of assuming the whole frame is a card.
            guide_margin: Background added around a known centred card guide on
                each side, as a fraction of the guide size. When detection fails,
                the guide region is used instead of the whole frame.

        Returns:
            The recognition result, including the pick, alternates, and variants.
        """
        started = time.perf_counter()
        quad = detect_card_quad(image_rgb)
        if quad is None and require_detection:
            logger.debug(
                "identify: input %dx%d, no card quad -> reporting no_card_detected",
                image_rgb.shape[1],
                image_rgb.shape[0],
            )
            return RecognitionResult(status=RecognitionStatus.NO_CARD_DETECTED)
        view = self._choose_view(image_rgb, quad, guide_margin)
        card = view.card
        from_frame = view.label.startswith("frame")
        used_guide_fallback = from_frame and guide_margin is not None
        if not from_frame:
            quad_status = "detected"
        elif used_guide_fallback:
            quad_status = "NOT used (guide fallback)"
        else:
            quad_status = "NOT used (whole-frame fallback)"
        logger.debug(
            "identify: input %dx%d, card quad %s, view %s",
            image_rgb.shape[1],
            image_rgb.shape[0],
            quad_status,
            view.label,
        )
        t_detect = time.perf_counter()

        # OCR (strip + title) and the dense signals are independent until
        # fusion; with an executor they overlap (ONNX Runtime and NumPy release
        # the GIL).
        text_future = (
            self._executor.submit(self._read_text, card)
            if self._executor is not None and self._ocr is not None
            else None
        )
        dense, query = self._dense_signals(view)
        t_dense = time.perf_counter()
        observation: OcrObservation | None
        name: NameObservation | None
        if text_future is not None:
            observation, name = text_future.result()
        else:
            observation, name = self._read_text(card)
        observation = self._validate_set_code(observation)
        t_ocr = time.perf_counter()

        consistent = self._ocr_consistent_ids(observation)
        # Every candidate any path surfaced (dense shortlists, the OCR-consistent
        # family, the name family) is scored exactly on every signal before fusion.
        self._complete_union(dense, query, consistent, name)
        resolved = self._resolve_candidates(dense)
        t_resolve = time.perf_counter()
        self._add_symbol_signal(dense, card, resolved, observation)

        if logger.isEnabledFor(logging.DEBUG):
            self._log_signal_diagnostics(dense, resolved, observation, consistent, name)
        status, candidates = fusion.fuse(
            dense,
            resolved.get,
            ocr=observation,
            ocr_consistent_ids=consistent,
            name=name,
            top_k=top_k,
        )
        if status is RecognitionStatus.AMBIGUOUS:
            status, candidates = self._resolve_printing(card, candidates)
        if used_guide_fallback and status == RecognitionStatus.UNCERTAIN:
            logger.debug("identify: guide fallback did not clear the confident threshold")
            status = RecognitionStatus.NO_MATCH
        result = self._assemble(status, candidates, card, observation, name)
        if logger.isEnabledFor(logging.DEBUG):
            self._log_fusion_diagnostics(result)
        done = time.perf_counter()
        logger.debug(
            "identify timings ms: total=%.0f detect=%.0f dense=%.0f ocr_wait=%.0f "
            "resolve=%.0f fuse=%.0f",
            (done - started) * 1000,
            (t_detect - started) * 1000,
            (t_dense - t_detect) * 1000,
            (t_ocr - t_dense) * 1000,
            (t_resolve - t_ocr) * 1000,
            (done - t_resolve) * 1000,
        )
        return result

    def identify_burst(
        self,
        images: Sequence[np.ndarray],
        *,
        top_k: int = constants.DEFAULT_TOP_K,
        require_detection: bool = False,
        guide_margin: float | None = None,
    ) -> RecognitionResult:
        """Recognize one card from several frames of it.

        Frames are recognized in order and their candidates merged by mean
        confidence (:func:`fusion.combine_frames`). A frame that finds no card
        is skipped. Recognition stops early once a frame is confident and every
        frame so far agrees on the winner: more frames could only repeat it.

        Args:
            images: RGB frames of the same card, best first.
            top_k: Maximum candidates to return.
            require_detection: Per-frame; see :meth:`identify`.
            guide_margin: Per-frame; see :meth:`identify`.

        Returns:
            The combined result. Variants, OCR and name reads come from the
            frame that was most confident in the combined winner.
        """
        results: list[RecognitionResult] = []
        for image in images:
            result = self.identify(
                image,
                top_k=max(top_k, constants.BURST_FRAME_TOP_K),
                require_detection=require_detection,
                guide_margin=guide_margin,
            )
            if result.status is RecognitionStatus.NO_CARD_DETECTED:
                continue
            results.append(result)
            winners = {_winner_id(r) for r in results}
            if result.status is RecognitionStatus.CONFIDENT and len(winners) == 1:
                break
        if not results:
            status = (
                RecognitionStatus.NO_CARD_DETECTED
                if require_detection
                else RecognitionStatus.NO_MATCH
            )
            return RecognitionResult(status=status)
        if len(results) == 1:
            # Nothing to combine: keep the frame's own verdict, which may carry
            # a printing-discriminator promotion that confidences alone cannot.
            only = results[0]
            ranked = _ranked(only)[:top_k]
            if only.match is None:
                return dataclasses.replace(only, alternates=tuple(ranked))
            return dataclasses.replace(only, alternates=tuple(ranked[1:]))
        status, candidates = fusion.combine_frames([_ranked(r) for r in results], top_k)
        if not candidates:
            return RecognitionResult(status=RecognitionStatus.NO_MATCH)
        winner_id = candidates[0].card.card_id
        if all(
            r.status is RecognitionStatus.CONFIDENT and _winner_id(r) == winner_id for r in results
        ):
            # Every frame was sure of the same card (possibly via the printing
            # discriminator, which settles ties the mean confidence keeps).
            status = RecognitionStatus.CONFIDENT
        logger.debug(
            "burst: %d/%d frame(s) used, combined %s", len(results), len(images), status.value
        )
        source = max(
            results,
            key=lambda r: next(
                (c.confidence for c in _ranked(r) if c.card.card_id == winner_id), 0.0
            ),
        )
        if status is RecognitionStatus.NO_MATCH:
            return RecognitionResult(
                status=status, alternates=tuple(candidates), ocr=source.ocr, name=source.name
            )
        same_winner = _winner_id(source) == winner_id
        return RecognitionResult(
            status=status,
            match=candidates[0],
            alternates=tuple(candidates[1:]),
            variants=source.variants if same_winner else (),
            ocr=source.ocr,
            name=source.name,
        )

    def _choose_view(
        self, image_rgb: np.ndarray, quad: np.ndarray | None, guide_margin: float | None
    ) -> _View:
        """Pick the orientation (and framing) hypothesis that matches best.

        Every orientation is hashed and looked up; a clearly strongest hash
        match settles it. Otherwise each orientation's full-card embedding is
        looked up and the best wins, and a weak winner with a detected quad
        gets one inset retry for a sleeve or border the detector locked onto.
        Hashes and the full embedding of the winner are kept for the dense
        signals, so the common case costs only the extra hash lookups.
        """
        views = card_views(image_rgb, quad, guide_margin=guide_margin)
        families = [hashes.compute_hashes(card) for _, card in views]
        best_hash = [_top_score(self._hash_index.query_hashes(f, top_k=1)) for f in families]
        ranked = sorted(best_hash, reverse=True)
        top = best_hash.index(ranked[0])
        runner_up_score = ranked[1] if len(ranked) > 1 else 0.0
        decisive = (
            ranked[0] >= constants.ORIENT_HASH_DECISIVE
            and ranked[0] - runner_up_score >= constants.ORIENT_HASH_MARGIN
        )
        if decisive or self._embedder is None or self._emb_full is None:
            label, card = views[top]
            return _View(label=label, card=card, hashes=families[top], full=None)
        vectors = embed_images(self._embedder, [card for _, card in views])
        best_full = [_top_score(self._emb_full.query(v, top_k=1)) for v in vectors]
        pick = max(range(len(views)), key=best_full.__getitem__)
        label, card = views[pick]
        chosen = _View(label=label, card=card, hashes=families[pick], full=vectors[pick])
        weak = best_full[pick] < constants.INSET_RETRY_BELOW
        if quad is None or not label.startswith("quad") or not weak:
            return chosen
        inset = inset_view(image_rgb, quad, flipped=label.endswith("rot180"))
        inset_vec = embed_images(self._embedder, [inset])[0]
        inset_score = _top_score(self._emb_full.query(inset_vec, top_k=1))
        logger.debug("view: %s weak (%.3f), inset retry %.3f", label, best_full[pick], inset_score)
        if inset_score <= best_full[pick]:
            return chosen
        return _View(
            label=f"{label}+inset",
            card=inset,
            hashes=hashes.compute_hashes(inset),
            full=inset_vec,
        )

    def _dense_signals(self, view: _View) -> tuple[dict[str, list[SignalScore]], _Query]:
        """Run the hash and embedding signals, keeping the query representations.

        The query hashes and embeddings are returned because the union pass
        rescores more of the catalogue with them; recomputing them there would
        double the cost of the most expensive stage. A full-card embedding the
        view choice already computed is reused.
        """
        card = view.card
        dense: dict[str, list[SignalScore]] = {
            fusion.SIGNAL_HASH: self._hash_index.query_hashes(view.hashes)
        }
        full_vec: np.ndarray | None = view.full
        art_vec: np.ndarray | None = None
        if self._embedder is not None and self._emb_full is not None and self._emb_art is not None:
            if full_vec is None:
                full_vec, art_vec = embed_images(self._embedder, [card, regions.artwork(card)])
            else:
                art_vec = embed_images(self._embedder, [regions.artwork(card)])[0]
            dense[fusion.SIGNAL_EMB_FULL] = self._emb_full.query(full_vec)
            dense[fusion.SIGNAL_EMB_ART] = self._emb_art.query(art_vec)
        return dense, _Query(hashes=view.hashes, full=full_vec, artwork=art_vec)

    def _read_text(self, card: np.ndarray) -> tuple[OcrObservation | None, NameObservation | None]:
        """OCR the collector strip and the title band of one rectified card."""
        if self._ocr is None:
            return None, None
        observation = read_card(self._ocr, card)
        if self._names is None:
            self._names = NameMatcher(self._store.card_names())
        return observation, read_name(self._ocr, card, self._names)

    def _complete_union(
        self,
        dense: dict[str, list[SignalScore]],
        query: _Query,
        consistent: frozenset[str],
        name: NameObservation | None,
    ) -> None:
        """Score the union of all candidates exactly on every dense signal.

        Each dense signal only returns its own top shortlist, and fusion treats
        "absent from a shortlist" as a score of 0.0. A reprint-heavy card can
        then rank 51st on hash while topping both embeddings, and lose a fifth of
        its fused score for nothing. Re-scoring the union removes that penalty;
        it costs one dot product (or hash distance) per missing pair.

        The OCR-consistent family joins the union too: a reprint is a pixel
        near-duplicate of its siblings, so the true printing can sit outside
        every shortlist while its collector number identifies it uniquely. A
        title-name family joins for the same reason when it is small enough.
        """
        union = {score.card_id for scores in dense.values() for score in scores}
        families = (
            ("ocr filter", consistent, constants.OCR_FILTER_MAX_CANDIDATES),
            (
                "name family",
                name.card_ids if name else frozenset(),
                constants.NAME_FAMILY_MAX_CANDIDATES,
            ),
        )
        for label, family, cap in families:
            if not family:
                continue
            if len(family) > cap:
                logger.debug("%s: skipped, %d cards exceeds the cap of %d", label, len(family), cap)
                continue
            added = len(family - union)
            union |= family
            if added:
                logger.debug("%s: added %d card(s) the shortlists missed", label, added)
        scorers: dict[str, Callable[[set[str]], list[SignalScore]]] = {
            fusion.SIGNAL_HASH: lambda ids: self._hash_index.scores_for(query.hashes, ids),
        }
        if self._emb_full is not None and query.full is not None:
            emb_full, full_vec = self._emb_full, query.full
            scorers[fusion.SIGNAL_EMB_FULL] = lambda ids: emb_full.scores_for(full_vec, ids)
        if self._emb_art is not None and query.artwork is not None:
            emb_art, art_vec = self._emb_art, query.artwork
            scorers[fusion.SIGNAL_EMB_ART] = lambda ids: emb_art.scores_for(art_vec, ids)
        for signal, score_ids in scorers.items():
            present = {score.card_id for score in dense.get(signal, ())}
            missing = union - present
            if missing:
                dense[signal] = dense.get(signal, []) + score_ids(missing)

    def _resolve_printing(
        self, card: np.ndarray, candidates: list[Candidate]
    ) -> tuple[RecognitionStatus, list[Candidate]]:
        """Settle an ambiguous same-artwork tie with the printing discriminator.

        Fusion's dense signals see mostly artwork, which is identical across
        printings of one illustration. The discriminator re-scores the tie on the
        regions that can differ (bottom strip, set-symbol zone) against each
        candidate's cached reference render. A decisive margin settles the tie
        outright; anything weaker only reorders it, leaving the caller to choose.
        """
        tie_ids = [candidate.card.card_id for candidate in candidates]
        image_paths = self._store.image_paths(tie_ids)
        if not image_paths:
            logger.debug("printing discriminator: no cached reference images; keeping ambiguous")
            return RecognitionStatus.AMBIGUOUS, candidates
        ordered, margin = rerank_printings(card, candidates, image_paths)
        if margin >= constants.RERANK_PROMOTE_MARGIN:
            logger.debug(
                "printing discriminator: %s wins by %.3f; promoting to confident",
                ordered[0].card.card_id,
                margin,
            )
            return RecognitionStatus.CONFIDENT, ordered
        logger.debug("printing discriminator: margin %.3f too small; reordered only", margin)
        return RecognitionStatus.AMBIGUOUS, ordered

    def _resolve_candidates(self, dense: dict[str, list[SignalScore]]) -> dict[str, CardRef]:
        """Resolve every card id surfaced by the dense signals to a CardRef."""
        ids = {score.card_id for scores in dense.values() for score in scores}
        return self._store.get_cards(ids)

    def _add_symbol_signal(
        self,
        dense: dict[str, list[SignalScore]],
        card: np.ndarray,
        resolved: dict[str, CardRef],
        observation: OcrObservation | None,
    ) -> None:
        """Add per-set symbol scores, but only for the WOTC (no set code) case."""
        if self._symbols is None or len(self._symbols) == 0:
            return
        if observation is not None and observation.set_code:
            logger.debug("symbol: skipped (OCR read set code %s)", observation.set_code)
            return
        set_scores = self._symbols.match(card)
        if logger.isEnabledFor(logging.DEBUG):
            best_sets = sorted(set_scores.items(), key=lambda item: item[1], reverse=True)
            logger.debug(
                "symbol: matched against %d set templates, top: %s",
                len(set_scores),
                ", ".join(f"{sid}={score:.3f}" for sid, score in best_sets[:_DEBUG_TOP_N]),
            )
        symbol_scores = [
            SignalScore(card_id, set_scores.get(ref.set_id, 0.0))
            for card_id, ref in resolved.items()
        ]
        if any(score.score > 0 for score in symbol_scores):
            dense[fusion.SIGNAL_SYMBOL] = symbol_scores

    def _log_signal_diagnostics(
        self,
        dense: dict[str, list[SignalScore]],
        resolved: dict[str, CardRef],
        observation: OcrObservation | None,
        consistent: frozenset[str],
        name: NameObservation | None,
    ) -> None:
        """Log each signal's top candidates and the OCR reads (DEBUG only).

        The point is diagnosing wrong matches: when the pick is bad, this shows
        which signal pulled it in and whether OCR agreed or was blind.
        """
        for signal, scores in dense.items():
            top = ", ".join(
                f"{_describe(resolved.get(s.card_id), s.card_id)}={s.score:.3f}"
                for s in scores[:_DEBUG_TOP_N]
            )
            logger.debug("signal %s top%d: %s", signal, _DEBUG_TOP_N, top or "(empty)")
        if observation is None:
            logger.debug("ocr: not run")
        else:
            logger.debug(
                "ocr: raw=%r number=%s/%s set_code=%s conf=%.2f -> %d catalogue cards consistent",
                observation.raw_text,
                observation.number,
                observation.number_total,
                observation.set_code,
                observation.confidence,
                len(consistent),
            )
        if name is not None:
            logger.debug(
                "name: raw=%r -> %r similarity=%.2f conf=%.2f family=%d",
                name.raw_text,
                name.name,
                name.similarity,
                name.confidence,
                len(name.card_ids),
            )

    def _log_fusion_diagnostics(self, result: RecognitionResult) -> None:
        """Log the fused verdict and ranked top candidates (DEBUG only)."""
        ranked = ([result.match] if result.match is not None else []) + list(result.alternates)
        lines = [
            f"  {rank}. {_describe(candidate.card, candidate.card.card_id)} "
            f"conf={candidate.confidence:.3f} signals={{"
            + ", ".join(f"{k}={v:.3f}" for k, v in sorted(candidate.per_signal.items()))
            + "}"
            for rank, candidate in enumerate(ranked[:_DEBUG_TOP_N], start=1)
        ]
        logger.debug(
            "fused: status=%s variants=[%s]\n%s",
            result.status.value,
            ", ".join(f"{v.kind.value}:{v.confidence:.2f}" for v in result.variants),
            "\n".join(lines) or "  (no candidates)",
        )

    def _validate_set_code(self, observation: OcrObservation | None) -> OcrObservation | None:
        """Drop an OCR set code that does not exist in the catalogue.

        Random uppercase scene text (a webpage header, a brand name) can pass
        the set-code regex. Trusting it is doubly harmful: the symbol signal is
        skipped ("OCR already knows the set") and fusion penalizes every
        candidate through ``is_useful``. Only codes the catalogue actually
        prints survive.
        """
        if observation is None or not observation.set_code:
            return observation
        if self._known_set_codes is None:
            self._known_set_codes = self._store.known_set_codes()
        if observation.set_code in self._known_set_codes:
            return observation
        logger.debug("ocr set code %s not in catalogue; ignoring", observation.set_code)
        return dataclasses.replace(observation, set_code=None)

    def _ocr_consistent_ids(self, observation: OcrObservation | None) -> frozenset[str]:
        """Return card ids whose collector number matches the OCR observation."""
        if observation is None or not observation.number:
            return frozenset()
        return frozenset(
            self._store.find_card_ids_by_number(observation.number, observation.number_total)
        )

    def _assemble(
        self,
        status: RecognitionStatus,
        candidates: list[Candidate],
        card: np.ndarray,
        observation: OcrObservation | None,
        name: NameObservation | None,
    ) -> RecognitionResult:
        """Package the fused candidates and variant guesses into a result."""
        ocr = observation if observation is not None and observation.is_useful else None
        reportable = (
            RecognitionStatus.CONFIDENT,
            RecognitionStatus.UNCERTAIN,
            RecognitionStatus.AMBIGUOUS,
        )
        if status in reportable and candidates:
            winner = candidates[0]
            variants = assess_variants(card, winner.card)
            return RecognitionResult(
                status=status,
                match=winner,
                alternates=tuple(candidates[1:]),
                variants=variants,
                ocr=ocr,
                name=name,
            )
        return RecognitionResult(
            status=RecognitionStatus.NO_MATCH,
            alternates=tuple(candidates),
            ocr=ocr,
            name=name,
        )
