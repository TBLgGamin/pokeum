/** Words the UI shows for scan outcomes and card facts, kept pure so they are testable. */

import { ApiError, type CandidateOut, type IdentifyResponse } from "@/lib/api";

/** Why a scan produced no card to confirm, and what to do about it. */
export function noResultMessage(status: IdentifyResponse["status"]): string {
  return status === "no_card_detected"
    ? "No card found. Center it in the frame."
    : "No match. Try a steadier angle.";
}

/** Plain-words explanation of a scan request that failed outright. */
export function scanErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return "Recognizer is busy. Try again.";
  }
  // fetch() rejects with a TypeError when the server cannot be reached at all.
  if (error instanceof TypeError) {
    return "Can't reach the recognizer. Check your connection.";
  }
  return "Scan failed. Try again.";
}

/** "PAL · 025/193": the set code (when the catalogue has one) and the collector number. */
export function cardCode(card: Pick<CandidateOut, "set" | "number">): string {
  return card.set.code ? `${card.set.code} · ${card.number}` : card.number;
}

/** Printing traits the recognizer saw ("Reverse holo · First edition"); "" for a plain print. */
export function variantLabel(card: Pick<CandidateOut, "variants">): string {
  return (card.variants ?? [])
    .filter((variant) => variant.present)
    .map((variant) => {
      const label = variant.kind.replaceAll("_", " ");
      return label.charAt(0).toUpperCase() + label.slice(1);
    })
    .join(" · ");
}

/** "1 card" / "24 cards". */
export function cardCount(count: number): string {
  return count === 1 ? "1 card" : `${count} cards`;
}
