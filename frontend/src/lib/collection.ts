/** Device-local persistence of the scanned collection.

The collection (`ScanEntry[]`) lives in localStorage as versioned JSON so it
survives reloads and PWA restarts. Entries are small (~300 bytes of card
metadata; images stay in the service-worker cache keyed by URL), so even large
collections sit far below storage quotas. Storage failures (private mode,
quota) are swallowed — scanning keeps working, persistence just pauses, the
and nothing crashes.

Bump `COLLECTION_VERSION` when the persisted shape changes incompatibly; old
data is then discarded rather than half-parsed.
*/

import type { ScanEntry } from "@/lib/exporters";

/** Bump when the persisted entry shape changes incompatibly. */
export const COLLECTION_VERSION = 1;

const STORAGE_KEY = "pokeum.collection";

function isValidEntry(value: unknown): value is ScanEntry {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Partial<ScanEntry>;
  return (
    typeof entry.card === "object" &&
    entry.card !== null &&
    typeof entry.card.card_id === "string" &&
    typeof entry.quantity === "number" &&
    entry.quantity > 0
  );
}

/** Load the persisted collection; wrong version, parse errors, or blocked storage yield []. */
export function loadCollection(): ScanEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as { version?: number; entries?: unknown[] };
    if (parsed.version !== COLLECTION_VERSION || !Array.isArray(parsed.entries))
      return [];
    return parsed.entries.filter(isValidEntry);
  } catch {
    return [];
  }
}

/** Persist the collection; storage failures are ignored (scanning still works). */
export function saveCollection(entries: ScanEntry[]): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: COLLECTION_VERSION, entries }),
    );
  } catch {
    // Quota or private mode: keep the in-memory collection, retry on next save.
  }
}

/**
 * Ask the browser to protect this origin's storage from automatic eviction —
 * matters for an installed PWA where the collection is the user's data.
 * Best-effort: unsupported browsers and denials resolve to false.
 */
export async function requestPersistentStorage(): Promise<boolean> {
  try {
    return (await navigator.storage?.persist?.()) ?? false;
  } catch {
    return false;
  }
}
