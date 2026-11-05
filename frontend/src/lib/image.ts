/** Client-side image utilities for the scan flow. */

/**
 * Longest side sent to the recognizer. Matches the server's own ingest cap:
 * anything larger only slows the upload and gets resized server-side anyway.
 */
const UPLOAD_MAX_SIDE = 1600;

/**
 * Extra margin around the guide frame kept in the capture crop, as a fraction
 * of the frame's size on each side. The server's quad detection needs the
 * card's outline *inside* the image with background contrast around it — a
 * tight crop would cut the contour and detection could never close it.
 */
export const GUIDE_CROP_MARGIN = 0.15;

export interface SourceRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Map the on-screen guide-frame rect to source-video pixel coordinates.
 *
 * The video is painted with `object-cover` (uniformly scaled to cover its box,
 * centered, overflow cropped), so screen pixels and source pixels differ. Both
 * rects must come from `getBoundingClientRect()` in the same viewport — that
 * also absorbs any CSS transform on the video (e.g. its slight zoom), because
 * bounding rects are post-transform.
 *
 * Returns null when the geometry is degenerate (zero-sized video or frame).
 */
export function guideCropSourceRect(
  videoRect: DOMRect,
  frameRect: DOMRect,
  videoWidth: number,
  videoHeight: number,
  margin = GUIDE_CROP_MARGIN,
): SourceRect | null {
  if (
    videoWidth <= 0 ||
    videoHeight <= 0 ||
    videoRect.width <= 0 ||
    videoRect.height <= 0
  ) {
    return null;
  }
  const coverScale = Math.max(
    videoRect.width / videoWidth,
    videoRect.height / videoHeight,
  );
  const offsetX = (videoWidth * coverScale - videoRect.width) / 2;
  const offsetY = (videoHeight * coverScale - videoRect.height) / 2;

  const marginX = frameRect.width * margin;
  const marginY = frameRect.height * margin;
  const screenLeft = frameRect.left - videoRect.left - marginX;
  const screenTop = frameRect.top - videoRect.top - marginY;
  const screenWidth = frameRect.width + 2 * marginX;
  const screenHeight = frameRect.height + 2 * marginY;

  const x = Math.max(0, (screenLeft + offsetX) / coverScale);
  const y = Math.max(0, (screenTop + offsetY) / coverScale);
  const width = Math.min(videoWidth - x, screenWidth / coverScale);
  const height = Math.min(videoHeight - y, screenHeight / coverScale);
  if (width < 1 || height < 1) return null;
  return { x, y, width, height };
}

/**
 * Downscale an image blob so its longer side is at most `maxSide`.
 *
 * Used on the file-upload path, where a photo-library image can be 12MP+.
 * Never makes things worse: when the image is already small enough, or
 * anything in decode/re-encode fails, the original blob is returned unchanged.
 */
export async function downscaleForUpload(
  blob: Blob,
  maxSide = UPLOAD_MAX_SIDE,
): Promise<Blob> {
  try {
    const bitmap = await createImageBitmap(blob);
    const longest = Math.max(bitmap.width, bitmap.height);
    if (longest <= maxSide) {
      bitmap.close();
      return blob;
    }
    const scale = maxSide / longest;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext("2d");
    if (!context) {
      bitmap.close();
      return blob;
    }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const resized = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob((result) => resolve(result), "image/jpeg", 0.92);
    });
    return resized ?? blob;
  } catch {
    return blob;
  }
}
