import { expect, it, vi } from "vitest";
import { ref } from "vue";

type Scene = "sharp" | "flat";

let scene: Scene = "sharp";
let phase = 0;

/** Pixels the stubbed canvas hands back: a checkerboard, or a flat gray blur. */
function pixels(width: number, height: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      const value = scene === "flat" ? 128 : (x + y + phase) % 2 === 0 ? 0 : 255;
      data[i] = value;
      data[i + 1] = value;
      data[i + 2] = value;
      data[i + 3] = 255;
    }
  }
  return data;
}

const context = {
  drawImage: () => undefined,
  getImageData: (_x: number, _y: number, width: number, height: number) => ({
    data: pixels(width, height),
    width,
    height,
  }),
};
// jsdom has no 2D context, and the composable creates its sampling canvas at
// import time — so the stub has to be installed before the module is loaded,
// which is why this import is dynamic.
HTMLCanvasElement.prototype.getContext = (() =>
  context) as unknown as HTMLCanvasElement["getContext"];
const { useAutoCapture } = await import("@/composables/useAutoCapture");

it("captures once per card and re-arms only after the frame changes", () => {
  vi.useFakeTimers();
  const video = ref({
    videoWidth: 640,
    videoHeight: 480,
    getBoundingClientRect: () => new DOMRect(0, 0, 640, 480),
  } as unknown as HTMLVideoElement);
  const enabled = ref(true);
  const busy = ref(false);
  let fired = 0;
  const auto = useAutoCapture({
    video,
    getFrameRect: () => new DOMRect(150, 60, 220, 300),
    enabled,
    busy,
    onStable: () => {
      fired += 1;
    },
  });

  auto.start();
  vi.advanceTimersByTime(150 * 3);
  expect(fired).toBe(1);
  expect(auto.armed.value).toBe(false);
  expect(auto.hint.value).toBe("");

  // A card left lying in the guide must never be scanned twice.
  vi.advanceTimersByTime(150 * 10);
  expect(fired).toBe(1);

  // Swapping cards: two moving samples re-arm, the next stable streak fires.
  phase = 1;
  vi.advanceTimersByTime(150);
  phase = 0;
  vi.advanceTimersByTime(150);
  expect(auto.armed.value).toBe(true);
  expect(auto.hint.value).toBe("Hold still");
  vi.advanceTimersByTime(150 * 3);
  expect(fired).toBe(2);

  // Out-of-focus frames coach instead of capturing.
  scene = "flat";
  vi.advanceTimersByTime(150 * 6);
  expect(fired).toBe(2);
  expect(auto.hint.value).toBe("Hold still");

  // Auto off, camera down, or a result on screen: no hint, no capture.
  enabled.value = false;
  vi.advanceTimersByTime(150);
  expect(auto.hint.value).toBe("");
  expect(fired).toBe(2);

  auto.stop();
  vi.useRealTimers();
});
