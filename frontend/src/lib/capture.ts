/** Turn the live camera preview into the image the recognizer is sent. */

import { guideCropSourceRect } from "@/lib/image";

/** JPEG quality of a camera capture: the collector number must stay legible. */
const CAPTURE_JPEG_QUALITY = 0.92;

/**
 * Encode the guide-frame crop (plus its margin) of the preview as a JPEG, so
 * the recognizer gets mostly-card pixels instead of the whole scene. With no
 * usable guide geometry the full frame is sent instead.
 *
 * Resolves null until the preview has decoded its first frame.
 *
 * @param video the live preview element
 * @param frameRect on-screen rect of the guide frame, or null when unknown
 */
export function captureGuideCrop(
  video: HTMLVideoElement,
  frameRect: DOMRect | null,
): Promise<Blob | null> {
  if (video.videoWidth === 0) return Promise.resolve(null);

  const crop = frameRect
    ? guideCropSourceRect(
        video.getBoundingClientRect(),
        frameRect,
        video.videoWidth,
        video.videoHeight,
      )
    : null;
  const source = crop ?? {
    x: 0,
    y: 0,
    width: video.videoWidth,
    height: video.videoHeight,
  };

  const canvas = document.createElement("canvas");
  canvas.width = Math.round(source.width);
  canvas.height = Math.round(source.height);
  canvas
    .getContext("2d")
    ?.drawImage(
      video,
      source.x,
      source.y,
      source.width,
      source.height,
      0,
      0,
      canvas.width,
      canvas.height,
    );
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", CAPTURE_JPEG_QUALITY);
  });
}
