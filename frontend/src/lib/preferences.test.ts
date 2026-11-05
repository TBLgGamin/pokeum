import { beforeEach, expect, it } from "vitest";

import { loadAutoScan, saveAutoScan } from "@/lib/preferences";

beforeEach(() => localStorage.clear());

it("defaults auto-scan to on and remembers an explicit choice", () => {
  expect(loadAutoScan()).toBe(true);

  saveAutoScan(false);
  expect(loadAutoScan()).toBe(false);

  saveAutoScan(true);
  expect(loadAutoScan()).toBe(true);
});
