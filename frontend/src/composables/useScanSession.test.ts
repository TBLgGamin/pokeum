import { beforeEach, expect, it, vi } from "vitest";
import { ref } from "vue";

import { useScanSession } from "@/composables/useScanSession";
import type * as ApiModule from "@/lib/api";
import {
  ApiError,
  identify,
  identifyBurst,
  type CandidateOut,
  type IdentifyResponse,
} from "@/lib/api";
import { captureGuideCrop } from "@/lib/capture";
import type * as ImageModule from "@/lib/image";
import { downscaleForUpload, GUIDE_CROP_MARGIN } from "@/lib/image";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof ApiModule>()),
  identify: vi.fn(),
  identifyBurst: vi.fn(),
}));
vi.mock("@/lib/capture", () => ({ captureGuideCrop: vi.fn() }));
vi.mock("@/lib/image", async (importOriginal) => ({
  ...(await importOriginal<typeof ImageModule>()),
  downscaleForUpload: vi.fn(),
}));

const identifyMock = vi.mocked(identify);
const burstMock = vi.mocked(identifyBurst);
const captureMock = vi.mocked(captureGuideCrop);
const downscaleMock = vi.mocked(downscaleForUpload);

function card(id: string): CandidateOut {
  return {
    card_id: id,
    name: `Card ${id}`,
    set: { id: "set", name: "Set", code: "SET" },
    number: id,
    rarity: null,
    image_url: null,
    confidence: 0.9,
    signals: {},
  };
}

function verdict(
  status: IdentifyResponse["status"],
  match: CandidateOut | null = card("1"),
): IdentifyResponse {
  return { status, match, alternates: [card("2")], ocr: null };
}

function setup(cameraReady = true) {
  return useScanSession({
    video: ref({} as HTMLVideoElement),
    getFrameRect: () => null,
    cameraReady: ref(cameraReady),
  });
}

const blob = new Blob(["frame"], { type: "image/jpeg" });

beforeEach(() => {
  vi.resetAllMocks();
  captureMock.mockResolvedValue(blob);
  downscaleMock.mockImplementation(async (file) => file);
  // By default a burst agrees with the single frame it followed.
  burstMock.mockImplementation(() => identifyMock.mock.results.at(-1)?.value);
});

it("looks again with a burst when the first frame is not confident", async () => {
  const second = new Blob(["second"], { type: "image/jpeg" });
  captureMock.mockResolvedValueOnce(blob).mockResolvedValue(second);
  identifyMock.mockResolvedValue(verdict("uncertain"));
  burstMock.mockResolvedValue(verdict("confident", card("7")));
  const session = setup();

  await session.scanCamera();

  expect(burstMock).toHaveBeenCalledWith(
    [blob, second, second],
    5,
    GUIDE_CROP_MARGIN,
  );
  expect(session.pending.value?.result.status).toBe("confident");
  expect(session.pending.value?.match.card_id).toBe("7");
});

it("sends no burst after a confident frame or for an uploaded photo", async () => {
  identifyMock.mockResolvedValue(verdict("confident"));
  const session = setup();
  await session.scanCamera();

  identifyMock.mockResolvedValue(verdict("uncertain"));
  await session.scanFile(blob);

  expect(burstMock).not.toHaveBeenCalled();
  expect(session.pending.value?.result.status).toBe("uncertain");
});

it("sends the guide crop with its margin and holds a confident match for the user", async () => {
  identifyMock.mockResolvedValue(verdict("confident"));
  const session = setup();

  await session.scanCamera();

  expect(identifyMock).toHaveBeenCalledWith(blob, 5, false, GUIDE_CROP_MARGIN);
  expect(session.pending.value?.match.card_id).toBe("1");
  expect(session.pending.value?.result.alternates).toHaveLength(1);
  expect(session.failure.value).toBeNull();
  expect(session.scanning.value).toBe(false);
});

it("treats uncertain and ambiguous verdicts with a match as confirmable", async () => {
  const session = setup();

  identifyMock.mockResolvedValue(verdict("uncertain"));
  await session.scanCamera();
  expect(session.pending.value?.result.status).toBe("uncertain");

  identifyMock.mockResolvedValue(verdict("ambiguous"));
  await session.scanCamera();
  expect(session.pending.value?.result.status).toBe("ambiguous");
});

it("explains a miss and keeps nothing pending", async () => {
  const session = setup();

  identifyMock.mockResolvedValue(verdict("no_card_detected", null));
  await session.scanCamera();
  expect(session.failure.value).toEqual({
    kind: "miss",
    message: "No card found. Center it in the frame.",
  });
  expect(session.pending.value).toBeNull();

  identifyMock.mockResolvedValue(verdict("no_match", null));
  await session.scanCamera();
  expect(session.failure.value?.message).toBe(
    "No match. Try a steadier angle.",
  );
});

it("reports a failed request in plain words", async () => {
  const session = setup();

  identifyMock.mockRejectedValue(
    new ApiError(503, "identify failed: HTTP 503"),
  );
  await session.scanCamera();
  expect(session.failure.value).toEqual({
    kind: "error",
    message: "Recognizer is busy. Try again.",
  });
  expect(session.scanning.value).toBe(false);
});

it("sends nothing when the camera is not ready", async () => {
  const session = setup(false);

  await session.scanCamera();

  expect(identifyMock).not.toHaveBeenCalled();
  expect(session.failure.value?.message).toBe(
    "Camera unavailable. Upload a photo instead.",
  );
});

it("downscales an uploaded photo and sends it without a guide margin", async () => {
  identifyMock.mockResolvedValue(verdict("confident"));
  const photo = new Blob(["photo"], { type: "image/jpeg" });
  const small = new Blob(["small"], { type: "image/jpeg" });
  downscaleMock.mockResolvedValue(small);
  const session = setup();

  await session.scanFile(photo);

  expect(downscaleMock).toHaveBeenCalledWith(photo);
  expect(identifyMock).toHaveBeenCalledWith(small, 5, false, undefined);
});

it("ignores a second scan while one is in flight", async () => {
  let release: (value: IdentifyResponse) => void = () => undefined;
  identifyMock.mockReturnValue(new Promise((resolve) => (release = resolve)));
  const session = setup();

  const first = session.scanCamera();
  await vi.waitFor(() => expect(session.scanning.value).toBe(true));
  await session.scanCamera();
  release(verdict("confident"));
  await first;

  expect(identifyMock).toHaveBeenCalledTimes(1);
});

it("hands back the match, or the printing the user chose, and clears the verdict", async () => {
  identifyMock.mockResolvedValue(verdict("confident"));
  const session = setup();

  await session.scanCamera();
  expect(session.accept()?.card_id).toBe("1");
  expect(session.pending.value).toBeNull();

  await session.scanCamera();
  expect(session.accept(card("2"))?.card_id).toBe("2");

  expect(session.accept()).toBeNull();
});

it("dismisses a verdict without saving and clears a failure on request", async () => {
  const session = setup();

  identifyMock.mockResolvedValue(verdict("confident"));
  await session.scanCamera();
  session.dismiss();
  expect(session.pending.value).toBeNull();

  identifyMock.mockResolvedValue(verdict("no_match", null));
  await session.scanCamera();
  session.clearFailure();
  expect(session.failure.value).toBeNull();
});
