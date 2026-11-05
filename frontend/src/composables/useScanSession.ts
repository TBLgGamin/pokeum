/** One scan, from captured pixels to a verdict the user can act on.
 *
 * Everything between "an image exists" and "the recognizer answered" lives
 * here, and `recognize` is the single place an image becomes an `identify`
 * call — so camera capture, photo upload and any future capture strategy all
 * go through one seam while the views stay about presentation.
 */

import { ref, shallowRef, type Ref } from "vue";

import {
  identify,
  identifyBurst,
  type CandidateOut,
  type IdentifyResponse,
} from "@/lib/api";
import { captureGuideCrop } from "@/lib/capture";
import { noResultMessage, scanErrorMessage } from "@/lib/copy";
import { GUIDE_CROP_MARGIN, downscaleForUpload } from "@/lib/image";

/** Candidates requested per scan: the match plus the alternates the chooser can offer. */
const TOP_K = 5;
/** Extra camera frames sent with the first when it was not confident (server cap: 4 total). */
const BURST_EXTRA_FRAMES = 2;
/** Gap between burst frames: long enough for glare and motion blur to move. */
const BURST_FRAME_GAP_MS = 80;

/** A verdict worth putting in front of the user. */
export interface PendingScan {
  /** The recognizer's answer, including the ranked alternates. */
  result: IdentifyResponse;
  /** `result.match`, narrowed: a pending scan always has a card to confirm. */
  match: CandidateOut;
}

/** A scan that produced nothing to confirm. */
export interface ScanFailure {
  /** `miss`: the recognizer answered "no"; `error`: the request itself failed. */
  kind: "miss" | "error";
  /** Plain words: what happened and what to do next. */
  message: string;
}

export interface ScanSessionOptions {
  /** The live preview element. */
  video: Ref<HTMLVideoElement | null>;
  /** On-screen rect of the guide frame, or null while it is not rendered. */
  getFrameRect: () => DOMRect | null;
  /** Whether the preview is delivering frames. */
  cameraReady: Ref<boolean>;
}

export interface ScanSession {
  /** A request is in flight. */
  scanning: Ref<boolean>;
  /** The verdict awaiting the user's decision, if any. */
  pending: Ref<PendingScan | null>;
  /** Why the last scan gave nothing to confirm, until the next scan starts. */
  failure: Ref<ScanFailure | null>;
  /** Scan the guide crop of the live preview. */
  scanCamera: () => Promise<void>;
  /** Scan a photo chosen from the library. */
  scanFile: (file: Blob) => Promise<void>;
  /** Confirm the pending card (or the printing the user picked); returns what to save. */
  accept: (chosen?: CandidateOut) => CandidateOut | null;
  /** Drop the pending verdict without saving anything. */
  dismiss: () => void;
  /** Forget the last failure. */
  clearFailure: () => void;
}

/** Create the scan state machine for one scanner view. */
export function useScanSession(options: ScanSessionOptions): ScanSession {
  const scanning = ref(false);
  const pending = shallowRef<PendingScan | null>(null);
  const failure = shallowRef<ScanFailure | null>(null);

  async function scanCamera(): Promise<void> {
    if (scanning.value) return;
    const blob = await captureFrame();
    if (!blob) {
      failure.value = {
        kind: "error",
        message: "Camera unavailable. Upload a photo instead.",
      };
      return;
    }
    // Quad rectification remains preferred, but the server can recover this
    // known guide region when glare or a low-contrast background breaks its edge.
    await recognize(blob, {
      guideMargin: GUIDE_CROP_MARGIN,
      moreFrames: captureMoreFrames,
    });
  }

  async function scanFile(file: Blob): Promise<void> {
    if (scanning.value) return;
    await recognize(await downscaleForUpload(file));
  }

  function captureFrame(): Promise<Blob | null> {
    const video = options.video.value;
    return video && options.cameraReady.value
      ? captureGuideCrop(video, options.getFrameRect())
      : Promise.resolve(null);
  }

  /** A few more frames of the card still in view, spaced so glare and blur move. */
  async function captureMoreFrames(): Promise<Blob[]> {
    const frames: Blob[] = [];
    for (let i = 0; i < BURST_EXTRA_FRAMES; i++) {
      await new Promise((resolve) => setTimeout(resolve, BURST_FRAME_GAP_MS));
      const frame = await captureFrame();
      if (frame) frames.push(frame);
    }
    return frames;
  }

  /** The capture → identify seam: every image the recognizer sees passes through here. */
  async function recognize(
    blob: Blob,
    request: {
      requireDetection?: boolean;
      guideMargin?: number;
      /** Supplies extra frames when the first one was not decisive (camera only). */
      moreFrames?: () => Promise<Blob[]>;
    } = {},
  ): Promise<void> {
    if (scanning.value) return;

    scanning.value = true;
    pending.value = null;
    failure.value = null;

    try {
      let result = await identify(
        blob,
        TOP_K,
        request.requireDetection ?? false,
        request.guideMargin,
      );
      if (result.status !== "confident" && request.moreFrames) {
        // One frame was not enough: look again and let the server merge the
        // frames, so a glare streak or a blurred moment does not decide.
        const extra = await request.moreFrames();
        if (extra.length > 0) {
          result = await identifyBurst(
            [blob, ...extra],
            TOP_K,
            request.guideMargin,
          );
        }
      }
      // `ambiguous` still carries the right artwork, so it is confirmable too.
      const confirmable =
        result.status === "confident" ||
        result.status === "uncertain" ||
        result.status === "ambiguous";
      if (result.match && confirmable) {
        pending.value = { result, match: result.match };
      } else {
        failure.value = {
          kind: "miss",
          message: noResultMessage(result.status),
        };
      }
    } catch (error) {
      failure.value = { kind: "error", message: scanErrorMessage(error) };
    } finally {
      scanning.value = false;
    }
  }

  function accept(chosen?: CandidateOut): CandidateOut | null {
    const scan = pending.value;
    if (!scan) return null;
    pending.value = null;
    return chosen ?? scan.match;
  }

  function dismiss(): void {
    pending.value = null;
  }

  function clearFailure(): void {
    failure.value = null;
  }

  return {
    scanning,
    pending,
    failure,
    scanCamera,
    scanFile,
    accept,
    dismiss,
    clearFailure,
  };
}
