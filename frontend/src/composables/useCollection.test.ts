import { effectScope, nextTick } from "vue";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { createCollection, type Collection } from "@/composables/useCollection";
import type { CandidateOut } from "@/lib/api";
import { COLLECTION_VERSION } from "@/lib/collection";

const STORAGE_KEY = "pokeum.collection";

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

function stored(): { version: number; entries: { card: CandidateOut; quantity: number }[] } {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
}

/** Build a collection inside a scope the test can dispose, like a mounted component's. */
function setup(): { collection: Collection; dispose: () => void } {
  const scope = effectScope();
  const collection = scope.run(() => createCollection())!;
  return { collection, dispose: () => scope.stop() };
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

it("adds a new card on top and counts repeats on the existing entry", () => {
  const { collection, dispose } = setup();

  collection.add(card("1"));
  collection.add(card("2"));
  collection.add(card("1"));

  expect(collection.entries.value.map((entry) => [entry.card.card_id, entry.quantity])).toEqual([
    ["2", 1],
    ["1", 2],
  ]);
  expect(collection.totalCards.value).toBe(3);
  dispose();
});

it("adjusts quantities and removes a card when it reaches zero", () => {
  const { collection, dispose } = setup();
  collection.add(card("1"));
  collection.add(card("1"));
  collection.add(card("2"));

  collection.adjust("1", -1);
  expect(collection.entries.value.find((entry) => entry.card.card_id === "1")?.quantity).toBe(1);

  collection.adjust("1", -1);
  expect(collection.entries.value.map((entry) => entry.card.card_id)).toEqual(["2"]);

  collection.adjust("2", 2);
  expect(collection.totalCards.value).toBe(3);

  collection.adjust("missing", -1);
  expect(collection.entries.value).toHaveLength(1);
  dispose();
});

it("writes once after a burst of edits, and a restored session starts from it", async () => {
  const first = setup();
  first.collection.add(card("1"));
  first.collection.add(card("1"));
  await nextTick();

  expect(stored()).toBeNull();
  vi.advanceTimersByTime(400);
  expect(stored()).toMatchObject({ version: COLLECTION_VERSION, entries: [{ quantity: 2 }] });

  const second = setup();
  expect(second.collection.totalCards.value).toBe(2);
  first.dispose();
  second.dispose();
});

it("flushes a pending write when the owning scope is disposed", async () => {
  const { collection, dispose } = setup();
  collection.add(card("1"));
  await nextTick();

  dispose();

  expect(stored().entries).toHaveLength(1);
});

it("clears the list and the stored copy", async () => {
  const { collection, dispose } = setup();
  collection.add(card("1"));
  await nextTick();
  vi.advanceTimersByTime(400);

  collection.clear();

  expect(collection.entries.value).toEqual([]);
  expect(collection.totalCards.value).toBe(0);
  expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  dispose();
});
