/** Pure frame-quality metrics behind auto-capture.

The scanner samples the guide crop a few times a second and only fires a scan
once the picture is worth sending: bright enough to read, not mid-move, and
sharp enough that the set symbol and number survive. Every function here is
pure and canvas-free (it takes pixels, returns numbers) so the thresholds can
be unit-tested without a camera or a browser.

Thresholds are tuned for the downscaled sample (`QUALITY_SAMPLE_WIDTH` wide),
not the full-resolution frame — resampling smooths noise, so a sharper source
is needed than the raw numbers suggest.
*/

/**
 * Width the guide crop is downscaled to before measuring.
 *
 * 160px keeps a full pass (gray + Laplacian + diff) at a fraction of a
 * millisecond, so the sampling loop never competes with the video decode.
 */
export const QUALITY_SAMPLE_WIDTH = 160;

/**
 * Minimum Laplacian variance: below this the card's text is mush.
 *
 * Set for a hand-held phone, not a tripod: the server rectifies, refines and
 * (when unsure) asks for a burst, so a frame only has to be readable, not crisp.
 */
export const SHARPNESS_MIN = 35;

/** Maximum mean absolute frame-to-frame difference tolerated as "still". */
export const MOTION_MAX = 10;

/**
 * Motion below this counts as "settled": the card is held in the guide with
 * normal hand tremor. Enough settled samples capture even if no single pair of
 * samples was perfectly still (see `PATIENCE_SAMPLES`).
 */
export const MOTION_SETTLED_MAX = 22;

/** Least detail a settled sample needs to count as "a card is there" (an empty table is flat). */
export const PATIENCE_SHARPNESS_MIN = 12;

/** Mean luma below this is too dark for OCR and the set symbol. */
export const BRIGHTNESS_MIN = 50;

/** Mean luma above this means glare is washing the card out. */
export const BRIGHTNESS_MAX = 210;

/** Consecutive good samples before auto-capture fires (~0.2 s at 100 ms). */
export const STABLE_SAMPLES_REQUIRED = 2;

/**
 * Consecutive settled, well-exposed samples after which auto-capture fires
 * anyway (~0.6 s). A shaky hand never produces a textbook-still streak; the
 * server copes with a slightly soft frame, so waiting longer only annoys.
 */
export const PATIENCE_SAMPLES = 6;

/**
 * Consecutive samples a problem must persist before its hint is shown, so a
 * single wobble does not flash "Hold still" (~0.4 s).
 */
export const HINT_AFTER_SAMPLES = 4;

/**
 * Consecutive moving/blurry samples that re-arm auto-capture after a result.
 *
 * Two is enough to mean "the card was moved or swapped", while a single noisy
 * sample on a card left lying in the guide cannot re-trigger a scan.
 */
export const CLEARED_SAMPLES_REQUIRED = 2;

/** The three fields these metrics read from a canvas `ImageData`. */
export type FrameSample = Pick<ImageData, "data" | "width" | "height">;

/** Why a frame is not worth scanning — `ok` means it is. */
export type FrameVerdict = "ok" | "blurry" | "moving" | "dark" | "bright";

/** Rec. 601 luma of each pixel, one byte per pixel. */
export function toGray(sample: FrameSample): Uint8ClampedArray {
  const { data, width, height } = sample;
  const gray = new Uint8ClampedArray(width * height);
  for (let pixel = 0, i = 0; pixel < gray.length; pixel += 1, i += 4) {
    gray[pixel] = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
  }
  return gray;
}

/**
 * Focus measure: variance of the 4-neighbour Laplacian over interior pixels.
 *
 * A blurred image has little high-frequency energy, so its second derivative
 * stays near zero and the variance collapses; edges push it up sharply.
 * Frames too small to have an interior score 0.
 */
export function sharpness(
  gray: Uint8ClampedArray,
  width: number,
  height: number,
): number {
  if (width < 3 || height < 3) return 0;
  let sum = 0;
  let sumSquares = 0;
  let count = 0;
  for (let y = 1; y < height - 1; y += 1) {
    const row = y * width;
    for (let x = 1; x < width - 1; x += 1) {
      const i = row + x;
      const laplacian =
        4 * gray[i] -
        gray[i - 1] -
        gray[i + 1] -
        gray[i - width] -
        gray[i + width];
      sum += laplacian;
      sumSquares += laplacian * laplacian;
      count += 1;
    }
  }
  if (count === 0) return 0;
  const mean = sum / count;
  return sumSquares / count - mean * mean;
}

/** Mean absolute luma difference against the previous sample; 0 without a comparable one. */
export function motion(
  prev: Uint8ClampedArray | null,
  gray: Uint8ClampedArray,
): number {
  if (prev === null || prev.length !== gray.length || gray.length === 0)
    return 0;
  let total = 0;
  for (let i = 0; i < gray.length; i += 1) {
    total += Math.abs(gray[i] - prev[i]);
  }
  return total / gray.length;
}

/** Mean luma of the sample. */
export function brightness(gray: Uint8ClampedArray): number {
  if (gray.length === 0) return 0;
  let total = 0;
  for (let i = 0; i < gray.length; i += 1) {
    total += gray[i];
  }
  return total / gray.length;
}

/**
 * Judge one sampled frame, returning its gray buffer for the next comparison
 * plus the raw motion and sharpness (for the patience path).
 *
 * Checks run cheapest-and-most-actionable first: exposure problems are what
 * the user can fix instantly, motion invalidates any sharpness reading, and
 * blur is only meaningful on a still, well-exposed frame.
 */
export function assessFrame(
  prev: Uint8ClampedArray | null,
  sample: FrameSample,
): {
  verdict: FrameVerdict;
  gray: Uint8ClampedArray;
  motion: number;
  sharpness: number;
} {
  const gray = toGray(sample);
  const level = brightness(gray);
  if (level < BRIGHTNESS_MIN)
    return { verdict: "dark", gray, motion: 0, sharpness: 0 };
  if (level > BRIGHTNESS_MAX)
    return { verdict: "bright", gray, motion: 0, sharpness: 0 };
  const moved = motion(prev, gray);
  const focus = sharpness(gray, sample.width, sample.height);
  if (moved > MOTION_MAX)
    return { verdict: "moving", gray, motion: moved, sharpness: focus };
  if (focus < SHARPNESS_MIN)
    return { verdict: "blurry", gray, motion: moved, sharpness: focus };
  return { verdict: "ok", gray, motion: moved, sharpness: focus };
}

/**
 * Whether a sample counts towards the patience path: settled in the guide,
 * and with enough detail that a card (not an empty table) is there.
 */
export function isSettled(result: {
  verdict: FrameVerdict;
  motion: number;
  sharpness: number;
}): boolean {
  return (
    result.verdict !== "dark" &&
    result.verdict !== "bright" &&
    result.motion <= MOTION_SETTLED_MAX &&
    result.sharpness >= PATIENCE_SHARPNESS_MIN
  );
}

const HINTS: Record<FrameVerdict, string> = {
  ok: "",
  blurry: "Hold still",
  moving: "Hold still",
  dark: "More light",
  bright: "Reduce glare",
};

/** Short on-screen coaching for a verdict; empty string when the frame is good. */
export function hintFor(verdict: FrameVerdict): string {
  return HINTS[verdict];
}
