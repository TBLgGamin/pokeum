import { describe, expect, it } from "vitest";

import {
  assessFrame,
  hintFor,
  MOTION_MAX,
  motion,
  SHARPNESS_MIN,
  sharpness,
  toGray,
  type FrameSample,
} from "@/lib/quality";

/** Build an opaque grayscale sample; `luma` returns 0-255 per pixel. */
function sample(
  width: number,
  height: number,
  luma: (x: number, y: number) => number,
): FrameSample {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      const value = luma(x, y);
      data[i] = value;
      data[i + 1] = value;
      data[i + 2] = value;
      data[i + 3] = 255;
    }
  }
  return { data, width, height };
}

const FLAT = sample(24, 32, () => 128);
const CHECKS = sample(24, 32, (x, y) => ((x + y) % 2 === 0 ? 0 : 255));
const CHECKS_INVERTED = sample(24, 32, (x, y) => ((x + y) % 2 === 0 ? 255 : 0));

describe("sharpness", () => {
  it("collapses to zero on a featureless frame, which reads as blurry", () => {
    expect(sharpness(toGray(FLAT), FLAT.width, FLAT.height)).toBe(0);
    expect(assessFrame(null, FLAT).verdict).toBe("blurry");
  });

  it("clears the threshold on a hard-edged frame", () => {
    expect(sharpness(toGray(CHECKS), CHECKS.width, CHECKS.height)).toBeGreaterThan(SHARPNESS_MIN);
  });
});

describe("motion", () => {
  it("is zero between identical frames", () => {
    expect(motion(toGray(CHECKS), toGray(CHECKS))).toBe(0);
  });

  it("exceeds the threshold against an inverted frame", () => {
    expect(motion(toGray(CHECKS), toGray(CHECKS_INVERTED))).toBeGreaterThan(MOTION_MAX);
    expect(assessFrame(toGray(CHECKS), CHECKS_INVERTED).verdict).toBe("moving");
  });
});

describe("assessFrame", () => {
  it("reports an unlit frame as dark", () => {
    expect(assessFrame(null, sample(24, 32, () => 0)).verdict).toBe("dark");
  });

  it("passes a still, well-exposed, sharp frame with no hint to show", () => {
    const { verdict } = assessFrame(toGray(CHECKS), CHECKS);
    expect(verdict).toBe("ok");
    expect(hintFor(verdict)).toBe("");
  });
});
