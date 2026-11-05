/** Hands-free capture: fire a scan once the guide frame holds a good picture.

Sampling the guide crop (never the whole video) keeps the measurement on the
pixels that get uploaded, and keeps it cheap enough to run several times a
second. The composable owns only the sampling loop and the arm/disarm state
machine; every metric lives in `@/lib/quality`, which is pure and tested.
*/

import { onScopeDispose, ref, type Ref } from "vue";

import { guideCropSourceRect } from "@/lib/image";
import {
  assessFrame,
  CLEARED_SAMPLES_REQUIRED,
  HINT_AFTER_SAMPLES,
  hintFor,
  isSettled,
  PATIENCE_SAMPLES,
  QUALITY_SAMPLE_WIDTH,
  STABLE_SAMPLES_REQUIRED,
  type FrameVerdict,
} from "@/lib/quality";

/**
 * Sampling period.
 *
 * 10 samples/s: a 160px-wide sample costs well under a millisecond, and the
 * faster loop is what lets a capture fire ~0.2 s after the card settles.
 */
const QUALITY_SAMPLE_INTERVAL_MS = 100;

// One offscreen canvas for the whole app: there is a single camera preview,
// and reallocating a canvas per tick would churn GPU-backed memory. The
// willReadFrequently hint keeps the surface CPU-side, which is what repeated
// getImageData wants.
const sampleCanvas = document.createElement("canvas");
const sampleContext = sampleCanvas.getContext("2d", {
  willReadFrequently: true,
});

export interface AutoCaptureOptions {
  /** The live preview element being sampled. */
  video: Ref<HTMLVideoElement | null>;
  /** On-screen rect of the guide frame, or null while it is not rendered. */
  getFrameRect: () => DOMRect | null;
  /** Sampling runs only while this is true (camera up, no result sheet open). */
  enabled: Ref<boolean>;
  /** A scan is in flight; samples taken now would race the result. */
  busy: Ref<boolean>;
  /** Called once per armed stable streak — the capture trigger. */
  onStable: () => void;
}

export interface AutoCapture {
  /** Coaching text for the current frame; empty when the frame is good. */
  hint: Ref<string>;
  /** Whether the next stable streak will fire a capture. */
  armed: Ref<boolean>;
  /** (Re)start the sampling loop, armed and with no history. */
  start: () => void;
  /** Stop sampling and clear the hint. */
  stop: () => void;
  /** Suppress capture until the frame changes (the current card was handled). */
  disarm: () => void;
}

/** Wire auto-capture to a preview element and a guide frame. */
export function useAutoCapture(options: AutoCaptureOptions): AutoCapture {
  const hint = ref("");
  const armed = ref(true);

  let timer: number | undefined;
  let prevGray: Uint8ClampedArray | null = null;
  let stableCount = 0;
  let settledCount = 0;
  let clearedCount = 0;
  let problem: FrameVerdict = "ok";
  let problemCount = 0;

  function resetStreaks(): void {
    stableCount = 0;
    settledCount = 0;
    problem = "ok";
    problemCount = 0;
    hint.value = "";
  }

  function disarm(): void {
    armed.value = false;
    clearedCount = 0;
    resetStreaks();
  }

  function tick(): void {
    if (!options.enabled.value) {
      // Camera down or a result on screen: the streak so far says nothing
      // about the frame the user will present next.
      prevGray = null;
      resetStreaks();
      return;
    }
    const element = options.video.value;
    if (
      options.busy.value ||
      !element ||
      element.videoWidth === 0 ||
      !sampleContext
    )
      return;

    const frameRect = options.getFrameRect();
    const crop = frameRect
      ? guideCropSourceRect(
          element.getBoundingClientRect(),
          frameRect,
          element.videoWidth,
          element.videoHeight,
        )
      : null;
    if (!crop) return;

    const width = QUALITY_SAMPLE_WIDTH;
    const height = Math.max(1, Math.round((crop.height / crop.width) * width));
    if (sampleCanvas.width !== width || sampleCanvas.height !== height) {
      sampleCanvas.width = width;
      sampleCanvas.height = height;
      // A different geometry makes the previous buffer incomparable.
      prevGray = null;
    }
    sampleContext.drawImage(
      element,
      crop.x,
      crop.y,
      crop.width,
      crop.height,
      0,
      0,
      width,
      height,
    );
    const sample = assessFrame(
      prevGray,
      sampleContext.getImageData(0, 0, width, height),
    );
    prevGray = sample.gray;
    const { verdict } = sample;

    if (!armed.value) {
      // This card was handled: no coaching, and only real movement re-arms,
      // so a card left lying in the guide is never scanned twice while
      // swapping cards resumes hands-free scanning.
      if (verdict === "moving" || verdict === "blurry") {
        clearedCount += 1;
        if (clearedCount >= CLEARED_SAMPLES_REQUIRED) {
          armed.value = true;
          clearedCount = 0;
          resetStreaks();
        }
      } else {
        clearedCount = 0;
      }
      return;
    }

    stableCount = verdict === "ok" ? stableCount + 1 : 0;
    settledCount = isSettled(sample) ? settledCount + 1 : 0;
    if (
      stableCount >= STABLE_SAMPLES_REQUIRED ||
      settledCount >= PATIENCE_SAMPLES
    ) {
      disarm();
      options.onStable();
      return;
    }

    // Coach only on a problem that persists; a single wobble should not flash.
    if (verdict === "ok") {
      problem = "ok";
      problemCount = 0;
      hint.value = "";
    } else {
      problemCount = verdict === problem ? problemCount + 1 : 1;
      problem = verdict;
      if (problemCount >= HINT_AFTER_SAMPLES) hint.value = hintFor(verdict);
    }
  }

  function stop(): void {
    window.clearInterval(timer);
    timer = undefined;
    hint.value = "";
    prevGray = null;
  }

  function start(): void {
    stop();
    armed.value = true;
    clearedCount = 0;
    resetStreaks();
    timer = window.setInterval(tick, QUALITY_SAMPLE_INTERVAL_MS);
  }

  onScopeDispose(stop);

  return { hint, armed, start, stop, disarm };
}
