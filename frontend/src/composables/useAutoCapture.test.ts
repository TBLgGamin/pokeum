import { expect, it, vi } from "vitest";
import { ref } from "vue";

type Scene = "sharp" | "flat" | "shaky";

let scene: Scene = "sharp";
let phase = 0;

/** Pixels the stubbed canvas hands back: a checkerboard, or a flat gray blur. */
function pixels(width: number, height: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      // "shaky": the checkerboard jitters every sample (hand tremor), so no
      // pair of samples is perfectly still, but it stays settled in the guide.
      const jitter =
        scene === "shaky" && phase % 2 === 1 && x % 16 === 0 ? 1 : 0;
      const value =
        scene === "flat" ? 128 : (x + y + jitter) % 2 === 0 ? 0 : 255;
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

const TICK = 100;

function setup() {
  const video = ref({
    videoWidth: 640,
    videoHeight: 480,
    getBoundingClientRect: () => new DOMRect(0, 0, 640, 480),
  } as unknown as HTMLVideoElement);
  const enabled = ref(true);
  const state = { fired: 0 };
  const auto = useAutoCapture({
    video,
    getFrameRect: () => new DOMRect(150, 60, 220, 300),
    enabled,
    busy: ref(false),
    onStable: () => {
      state.fired += 1;
    },
  });
  return { auto, enabled, state };
}

function moveCard(): void {
  // Two very different samples: the card was lifted or swapped.
  scene = "flat";
  vi.advanceTimersByTime(TICK);
  scene = "sharp";
  vi.advanceTimersByTime(TICK);
}

it("captures a still card within a fraction of a second, once per card", () => {
  vi.useFakeTimers();
  scene = "sharp";
  const { auto, enabled, state } = setup();

  auto.start();
  vi.advanceTimersByTime(TICK * 2);
  expect(state.fired).toBe(1);
  expect(auto.armed.value).toBe(false);

  // A card left lying in the guide must never be scanned twice, and a handled
  // card gets no coaching.
  vi.advanceTimersByTime(TICK * 20);
  expect(state.fired).toBe(1);
  expect(auto.hint.value).toBe("");

  // Swapping cards re-arms; the next card captures just as fast.
  moveCard();
  expect(auto.armed.value).toBe(true);
  vi.advanceTimersByTime(TICK * 2);
  expect(state.fired).toBe(2);

  // A result on screen pauses everything.
  enabled.value = false;
  moveCard();
  vi.advanceTimersByTime(TICK * 20);
  expect(state.fired).toBe(2);

  auto.stop();
  vi.useRealTimers();
});

it("does not wait forever on a shaky hand, and coaches only a lasting problem", () => {
  vi.useFakeTimers();
  scene = "flat";
  const { auto, state } = setup();
  auto.start();

  // An empty, featureless guide never captures; the hint waits a moment.
  vi.advanceTimersByTime(TICK * 2);
  expect(auto.hint.value).toBe("");
  vi.advanceTimersByTime(TICK * 10);
  expect(state.fired).toBe(0);
  expect(auto.hint.value).toBe("Hold still");

  // A card held with tremor: never two perfectly still samples in a row, yet
  // the patience path captures it after ~0.6 s.
  scene = "shaky";
  let ticks = 0;
  while (state.fired === 0 && ticks < 20) {
    phase += 1;
    ticks += 1;
    vi.advanceTimersByTime(TICK);
  }
  // One sample to leave the flat scene, then PATIENCE_SAMPLES settled ones.
  expect(state.fired).toBe(1);
  expect(ticks).toBe(7);

  auto.stop();
  vi.useRealTimers();
});
