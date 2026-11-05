import { expect, it } from "vitest";

import type { CandidateOut } from "@/lib/api";
import {
  toChecklist,
  toDeckList,
  toJsonBackup,
  toSpreadsheetCsv,
  toTcgplayerCsv,
  type ScanEntry,
} from "@/lib/exporters";

function entry(card: Partial<CandidateOut>, quantity = 1): ScanEntry {
  return {
    card: {
      card_id: "sv02-025",
      name: "Pikachu",
      set: { id: "sv02", name: "Paldea Evolved", code: "PAL" },
      number: "025/193",
      rarity: "Common",
      image_url: null,
      confidence: 0.9,
      signals: {},
      ...card,
    },
    quantity,
    scannedAt: "2026-11-05T10:00:00.000Z",
  };
}

const reverse = [{ kind: "reverse_holo", present: true, confidence: 0.8 }];

it("writes TCGplayer's columns and printing vocabulary", () => {
  expect(toTcgplayerCsv([entry({ variants: reverse }, 2)])).toBe(
    "Quantity,Name,Set,Card Number,Language,Printing\n" +
      "2,Pikachu,Paldea Evolved,025/193,English,Reverse Holofoil\n",
  );
});

it("quotes CSV fields that contain commas or quotes", () => {
  const csv = toSpreadsheetCsv([
    entry({ name: 'Professor\'s "Research", Oak' }),
  ]);
  expect(csv.split("\n")[1]).toBe(
    '"Professor\'s ""Research"", Oak",Paldea Evolved,PAL,025/193,Common,Normal,1,sv02-025,2026-11-05',
  );
});

it("writes deck-list lines with the bare collector number, falling back to the set name", () => {
  const text = toDeckList([
    entry({}, 3),
    entry({
      name: "Charizard",
      set: { id: "base1", name: "Base Set", code: null },
      number: "4/102",
    }),
  ]);
  expect(text).toBe("3 Pikachu PAL 025\n1 Charizard Base Set 4\n");
});

it("names non-normal printings in the checklist only", () => {
  expect(toChecklist([entry({}, 2), entry({ variants: reverse })])).toBe(
    "2× Pikachu — Paldea Evolved 025/193\n1× Pikachu — Paldea Evolved 025/193 (Reverse Holofoil)\n",
  );
});

it("round-trips the whole collection through the JSON backup", () => {
  const entries = [entry({}, 2)];
  expect(JSON.parse(toJsonBackup(entries)).entries).toEqual(entries);
});
