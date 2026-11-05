import { createApp, nextTick, type App as VueApp } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { CandidateOut, IdentifyResponse } from "@/lib/api";
import { COLLECTION_VERSION } from "@/lib/collection";

// A dynamic import on purpose: jsdom has no canvas 2D context and `useAutoCapture`
// creates its sampling canvas at import time, so the stub must exist before App loads.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
const { default: App } = await import("@/App.vue");

function card(id: string, name: string, setName: string, code: string): CandidateOut {
  return {
    card_id: id,
    name,
    set: { id: id.split("-")[0], name: setName, code },
    number: "4",
    rarity: null,
    image_url: null,
    confidence: 0.9,
    signals: {},
  };
}

const CHARIZARD = card("base1-4", "Charizard", "Base Set", "BS");
const CHARIZARD_B2 = card("base4-4", "Charizard", "Base Set 2", "B2");

function verdict(status: IdentifyResponse["status"], alternates: CandidateOut[] = []): IdentifyResponse {
  return { status, match: status.startsWith("no_") ? null : CHARIZARD, alternates, ocr: null };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Make every API call answer with `respond()`. */
function answerApi(respond: () => Response | Promise<Response>): void {
  vi.stubGlobal("fetch", vi.fn(async () => respond()));
}

let app: VueApp | null = null;

function mountApp(): void {
  const root = document.createElement("div");
  document.body.append(root);
  app = createApp(App);
  app.mount(root);
}

const text = (): string => document.body.textContent ?? "";
const dialog = (): Element | null => document.querySelector('[role="dialog"], [role="alertdialog"]');

function control(name: string): HTMLButtonElement {
  const found = [...document.querySelectorAll("button")].find(
    (button) => (button.getAttribute("aria-label") ?? button.textContent ?? "").trim() === name,
  );
  if (!found) throw new Error(`no button named "${name}"; page text: ${text().slice(0, 300)}`);
  return found;
}

function upload(): void {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(input, "files", {
    value: [new File(["photo"], "card.jpg", { type: "image/jpeg" })],
    configurable: true,
  });
  input.dispatchEvent(new Event("change"));
}

function goTo(hash: "#scan" | "#collection"): void {
  window.location.hash = hash;
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function collectionLink(): HTMLAnchorElement {
  return document.querySelector<HTMLAnchorElement>('nav[aria-label="Main"] a[href="#collection"]')!;
}

function seedCollection(entries: { card: CandidateOut; quantity: number }[]): void {
  localStorage.setItem(
    "pokeum.collection",
    JSON.stringify({
      version: COLLECTION_VERSION,
      entries: entries.map((entry) => ({ ...entry, scannedAt: "2026-01-01T00:00:00.000Z" })),
    }),
  );
}

beforeEach(() => {
  window.location.hash = "";
  // Denied camera: the scanner falls back to its upload-only state.
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")) },
    configurable: true,
  });
  Object.defineProperty(window, "matchMedia", {
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }),
    configurable: true,
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    },
  );
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  app?.unmount();
  app = null;
  document.body.innerHTML = "";
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("opens on the scanner and offers an upload when the camera is denied", async () => {
  mountApp();

  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));
  expect(document.querySelector('nav[aria-label="Main"] a[aria-current="page"]')?.textContent).toContain(
    "Scan",
  );
  expect(control("Upload photo")).toBeTruthy();
});

it("saves an uploaded photo's match and counts it in the header", async () => {
  answerApi(() => json(verdict("confident")));
  mountApp();
  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));

  upload();
  await vi.waitFor(() => expect(dialog()?.textContent).toContain("Charizard"));
  expect(dialog()?.textContent).toContain("BS · 4");
  expect(dialog()?.textContent).not.toContain("Low confidence");

  control("Save card").click();
  await vi.waitFor(() => expect(dialog()).toBeNull());
  expect(text()).toContain("Saved Charizard");
  expect(collectionLink().textContent).toContain("1");
});

it("flags an uncertain match in words", async () => {
  answerApi(() => json(verdict("uncertain")));
  mountApp();
  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));

  upload();

  await vi.waitFor(() => expect(dialog()?.textContent).toContain("Low confidence"));
});

it("opens an ambiguous verdict as a printing chooser and saves the printing picked", async () => {
  answerApi(() => json(verdict("ambiguous", [CHARIZARD_B2])));
  mountApp();
  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));

  upload();
  await vi.waitFor(() => expect(dialog()?.textContent).toContain("Which printing?"));

  const options = [...dialog()!.querySelectorAll("li button")];
  expect(options).toHaveLength(2);
  (options[1] as HTMLButtonElement).click();
  await vi.waitFor(() => expect(dialog()).toBeNull());

  goTo("#collection");
  await vi.waitFor(() => expect(text()).toContain("Base Set 2"));
  expect(text()).not.toContain("Base Set · BS");
});

it("reaches the same chooser from 'Not this one'", async () => {
  answerApi(() => json(verdict("confident", [CHARIZARD_B2])));
  mountApp();
  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));

  upload();
  await vi.waitFor(() => expect(dialog()?.textContent).toContain("Charizard"));
  control("Not this one").click();

  await vi.waitFor(() => expect(dialog()?.textContent).toContain("Pick the right card"));
});

it("dismisses a verdict with Escape without saving anything", async () => {
  answerApi(() => json(verdict("confident")));
  mountApp();
  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));

  upload();
  await vi.waitFor(() => expect(dialog()).not.toBeNull());
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

  await vi.waitFor(() => expect(dialog()).toBeNull());
  expect(text()).not.toContain("Saved");
  expect(collectionLink().textContent).not.toMatch(/\d/);
});

it("says what to do after a miss, a busy server or a lost connection", async () => {
  mountApp();
  await vi.waitFor(() => expect(text()).toContain("Camera unavailable"));

  answerApi(() => json(verdict("no_match")));
  upload();
  await vi.waitFor(() => expect(text()).toContain("No match. Try a steadier angle."));

  answerApi(() => json(verdict("no_card_detected")));
  upload();
  await vi.waitFor(() => expect(text()).toContain("No card found. Center it in the frame."));

  answerApi(() => json({ detail: "recognizer busy" }, 503));
  upload();
  await vi.waitFor(() => expect(text()).toContain("Recognizer is busy. Try again."));

  answerApi(() => Promise.reject(new TypeError("Failed to fetch")));
  upload();
  await vi.waitFor(() =>
    expect(text()).toContain("Can't reach the recognizer. Check your connection."),
  );
});

it("shows the empty collection with a way back to the scanner", async () => {
  mountApp();
  goTo("#collection");

  await vi.waitFor(() => expect(text()).toContain("No cards yet"));
  expect(document.querySelector("h1")?.textContent).toContain("Collection");
  expect(document.querySelector('main a[href="#scan"]')).toBeTruthy();
});

it("lists the collection, adjusts quantities, and removes a card at zero", async () => {
  seedCollection([{ card: CHARIZARD, quantity: 1 }]);
  mountApp();
  goTo("#collection");

  await vi.waitFor(() => expect(text()).toContain("1 card"));
  expect(text()).toContain("Charizard");

  control("Add one Charizard").click();
  await vi.waitFor(() => expect(text()).toContain("2 cards"));

  control("Remove one Charizard").click();
  control("Remove one Charizard").click();
  await vi.waitFor(() => expect(text()).toContain("No cards yet"));
});

it("exports the collection as CSV", async () => {
  seedCollection([{ card: CHARIZARD, quantity: 2 }]);
  mountApp();
  goTo("#collection");
  await vi.waitFor(() => expect(text()).toContain("2 cards"));

  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  control("Export CSV").click();

  expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  const blob = vi.mocked(URL.createObjectURL).mock.calls[0][0] as Blob;
  expect(await blob.text()).toBe(
    "Quantity,Name,Set,Card Number,Language,Printing\n2,Charizard,Base Set,4,English,Normal\n",
  );
  expect(click).toHaveBeenCalled();
});

it("clears the collection only after a confirmation that names the count", async () => {
  seedCollection([
    { card: CHARIZARD, quantity: 2 },
    { card: CHARIZARD_B2, quantity: 1 },
  ]);
  mountApp();
  goTo("#collection");
  await vi.waitFor(() => expect(text()).toContain("3 cards"));

  control("Clear collection").click();
  await vi.waitFor(() => expect(dialog()?.textContent).toContain("Clear 3 cards?"));

  control("Cancel").click();
  await vi.waitFor(() => expect(dialog()).toBeNull());
  expect(text()).toContain("Base Set 2");

  control("Clear collection").click();
  await vi.waitFor(() => expect(dialog()).not.toBeNull());
  const confirm = [...dialog()!.querySelectorAll("button")].find(
    (button) => button.textContent?.trim() === "Clear collection",
  )!;
  confirm.click();
  await nextTick();

  await vi.waitFor(() => expect(text()).toContain("No cards yet"));
});
