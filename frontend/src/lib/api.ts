/** Typed client for the pokeum recognizer API.

Defaults to same-origin `/api` (the Vite proxy). Set `VITE_API_BASE` to a full
URL (e.g. a tunneled API host) to call the recognizer cross-origin — the API
sends permissive CORS headers.
*/

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

/** A non-2xx answer from the API; `status` lets callers tell "busy" from "broken". */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface CardSet {
  id: string;
  name: string;
  code: string | null;
}

export interface VariantGuess {
  kind: string;
  present: boolean;
  confidence: number;
}

export interface CandidateOut {
  card_id: string;
  name: string;
  set: CardSet;
  number: string;
  rarity: string | null;
  image_url: string | null;
  confidence: number;
  signals: Record<string, number>;
  variants?: VariantGuess[];
}

/** The card name read from the title band (title OCR) and used during fusion. */
export interface NameRead {
  name: string;
  similarity: number;
  confidence: number;
}

export interface IdentifyResponse {
  status:
    "confident" | "uncertain" | "ambiguous" | "no_match" | "no_card_detected";
  match: CandidateOut | null;
  alternates: CandidateOut[];
  ocr: {
    number: string | null;
    number_total: number | null;
    set_code: string | null;
    confidence?: number;
  } | null;
  /** Title-OCR read; absent from older servers and null when nothing was read. */
  name?: NameRead | null;
}

/**
 * POST a captured frame to the recognizer.
 *
 * With `requireDetection`, the server answers `no_card_detected` when no card
 * quad is found. `guideMargin` instead lets a guided camera capture fall back
 * to its known inner card region when quad detection fails.
 */
export async function identify(
  blob: Blob,
  topK = 5,
  requireDetection = false,
  guideMargin?: number,
): Promise<IdentifyResponse> {
  const form = new FormData();
  form.append("file", blob, "frame.jpg");
  const query = new URLSearchParams({ top_k: String(topK) });
  if (requireDetection) query.set("require_detection", "true");
  if (guideMargin !== undefined) query.set("guide_margin", String(guideMargin));
  const response = await fetch(`${API_BASE}/identify?${query}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      `identify failed: HTTP ${response.status}`,
    );
  }
  return (await response.json()) as IdentifyResponse;
}

/**
 * POST several frames of one card; the server merges them into one verdict.
 *
 * Used when a single frame was not decisive: frames are recognized in order
 * and combined by mean confidence, so one glared frame cannot carry the answer.
 */
export async function identifyBurst(
  blobs: Blob[],
  topK = 5,
  guideMargin?: number,
): Promise<IdentifyResponse> {
  const form = new FormData();
  blobs.forEach((blob, index) =>
    form.append("files", blob, `frame-${index}.jpg`),
  );
  const query = new URLSearchParams({ top_k: String(topK) });
  if (guideMargin !== undefined) query.set("guide_margin", String(guideMargin));
  const response = await fetch(`${API_BASE}/identify/burst?${query}`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new ApiError(
      response.status,
      `identify burst failed: HTTP ${response.status}`,
    );
  }
  return (await response.json()) as IdentifyResponse;
}

/** URL of a card's reference image (served by the API from its cache). */
export function cardImageUrl(cardId: string): string {
  return `${API_BASE}/cards/${encodeURIComponent(cardId)}/image`;
}

/**
 * Best URL for a card's artwork: the TCGdex CDN when the catalogue knows it
 * (fast, cacheable offline, no server disk), else the API image endpoint —
 * which itself redirects to the CDN when the server has no local image cache.
 */
export function cardArtUrl(
  card: Pick<CandidateOut, "card_id" | "image_url">,
): string {
  return card.image_url ?? cardImageUrl(card.card_id);
}

/** Check the API is up, how many cards are indexed, and which embedder answers. */
export async function health(): Promise<{
  status: string;
  cards_indexed: number;
  embedder_id: string;
}> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      `health failed: HTTP ${response.status}`,
    );
  }
  return (await response.json()) as {
    status: string;
    cards_indexed: number;
    embedder_id: string;
  };
}
