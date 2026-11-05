/** The on-device collection: one shared list the header, scanner and collection view all read. */

import {
  computed,
  inject,
  onScopeDispose,
  provide,
  ref,
  watch,
  type ComputedRef,
  type InjectionKey,
  type Ref,
} from "vue";

import type { CandidateOut } from "@/lib/api";
import {
  loadCollection,
  requestPersistentStorage,
  saveCollection,
} from "@/lib/collection";
import type { ScanEntry } from "@/lib/exporters";

/** Quiet period before a burst of edits is written, so one tap-and-hold costs one write. */
const SAVE_DELAY_MS = 400;

/** The collection as every view sees it. */
export interface Collection {
  /** One entry per distinct card with its quantity, newest scan first. */
  entries: Ref<ScanEntry[]>;
  /** Copies across all entries. */
  totalCards: ComputedRef<number>;
  /** Add one copy of a card; a new card goes to the top, a known one keeps its place. */
  add: (card: CandidateOut) => void;
  /** Change a card's quantity by `delta`; reaching zero removes it. */
  adjust: (cardId: string, delta: number) => void;
}

/**
 * Build the collection state, restored from localStorage.
 *
 * Writes are debounced and flushed when the page is hidden: mobile PWAs are
 * killed without unload events, so the hidden transition is the last reliable
 * moment to persist. The first non-empty save also asks the browser to protect
 * the origin's storage from eviction. Disposing the owning scope flushes too.
 */
export function createCollection(): Collection {
  const entries = ref<ScanEntry[]>(loadCollection());
  const totalCards = computed(() =>
    entries.value.reduce((sum, entry) => sum + entry.quantity, 0),
  );

  let saveTimer: number | undefined;
  let persistenceRequested = false;

  function persist(): void {
    window.clearTimeout(saveTimer);
    saveTimer = undefined;
    saveCollection(entries.value);
  }

  function flush(): void {
    if (document.visibilityState === "hidden" || saveTimer !== undefined)
      persist();
  }

  watch(
    entries,
    () => {
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(persist, SAVE_DELAY_MS);
      if (!persistenceRequested && entries.value.length > 0) {
        persistenceRequested = true;
        void requestPersistentStorage();
      }
    },
    { deep: true },
  );

  document.addEventListener("visibilitychange", flush);
  onScopeDispose(() => {
    document.removeEventListener("visibilitychange", flush);
    flush();
  });

  function add(card: CandidateOut): void {
    const existing = entries.value.find(
      (entry) => entry.card.card_id === card.card_id,
    );
    if (existing) {
      existing.quantity += 1;
    } else {
      entries.value.unshift({
        card,
        quantity: 1,
        scannedAt: new Date().toISOString(),
      });
    }
  }

  function adjust(cardId: string, delta: number): void {
    const index = entries.value.findIndex(
      (entry) => entry.card.card_id === cardId,
    );
    if (index < 0) return;
    const quantity = entries.value[index].quantity + delta;
    if (quantity <= 0) {
      entries.value.splice(index, 1);
    } else {
      entries.value[index].quantity = quantity;
    }
  }

  return { entries, totalCards, add, adjust };
}

const collectionKey: InjectionKey<Collection> = Symbol("collection");

/** Create the collection and make it available to every component below the caller. */
export function provideCollection(): Collection {
  const collection = createCollection();
  provide(collectionKey, collection);
  return collection;
}

/** The collection provided by an ancestor (`App.vue`). */
export function useCollection(): Collection {
  const collection = inject(collectionKey);
  if (!collection)
    throw new Error("useCollection() needs provideCollection() in an ancestor");
  return collection;
}
