import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { cardCode, cardCount, noResultMessage, scanErrorMessage, variantLabel } from "@/lib/copy";

describe("noResultMessage", () => {
  it("tells the user where to put the card when none was found", () => {
    expect(noResultMessage("no_card_detected")).toBe("No card found. Center it in the frame.");
  });

  it("asks for a steadier angle for every other miss", () => {
    expect(noResultMessage("no_match")).toBe("No match. Try a steadier angle.");
    expect(noResultMessage("uncertain")).toBe("No match. Try a steadier angle.");
  });
});

describe("scanErrorMessage", () => {
  it("separates a busy recognizer, a lost connection and everything else", () => {
    expect(scanErrorMessage(new ApiError(503, "identify failed: HTTP 503"))).toBe(
      "Recognizer is busy. Try again.",
    );
    expect(scanErrorMessage(new TypeError("Failed to fetch"))).toBe(
      "Can't reach the recognizer. Check your connection.",
    );
    expect(scanErrorMessage(new ApiError(500, "identify failed: HTTP 500"))).toBe(
      "Scan failed. Try again.",
    );
    expect(scanErrorMessage("boom")).toBe("Scan failed. Try again.");
  });

  it("never leaks the HTTP detail into the copy", () => {
    expect(scanErrorMessage(new ApiError(400, "identify failed: HTTP 400"))).not.toMatch(/HTTP/);
  });
});

describe("cardCode", () => {
  it("joins the set code and number, and falls back to the number alone", () => {
    expect(cardCode({ set: { id: "sv02", name: "Paldea Evolved", code: "PAL" }, number: "025" })).toBe(
      "PAL · 025",
    );
    expect(cardCode({ set: { id: "np", name: "Promos", code: null }, number: "1" })).toBe("1");
  });
});

describe("variantLabel", () => {
  it("lists only the variants that are present, sentence-cased", () => {
    expect(
      variantLabel({
        variants: [
          { kind: "reverse_holo", present: true, confidence: 0.7 },
          { kind: "first_edition", present: true, confidence: 0.6 },
          { kind: "promo", present: false, confidence: 0.2 },
        ],
      }),
    ).toBe("Reverse holo · First edition");
  });

  it("is empty for a plain print, with or without variant data", () => {
    expect(variantLabel({ variants: [] })).toBe("");
    expect(variantLabel({})).toBe("");
  });
});

describe("cardCount", () => {
  it("pluralises", () => {
    expect(cardCount(1)).toBe("1 card");
    expect(cardCount(0)).toBe("0 cards");
    expect(cardCount(24)).toBe("24 cards");
  });
});
