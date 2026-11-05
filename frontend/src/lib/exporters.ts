/** Export scanned cards to formats collectors import elsewhere. */

import type { CandidateOut } from "./api";

export interface ScanEntry {
  card: CandidateOut;
  quantity: number;
  scannedAt: string;
}

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

/**
 * TCGplayer-style collection CSV (Quantity, Name, Set, Card Number, Language,
 * Printing) — the de-facto interchange format most collection trackers
 * (TCGplayer app, Collectr, Dragon Shield imports) can map directly.
 * Deliberately no Condition column: a scanner cannot judge condition, and a
 * made-up "Near Mint" would be worse than letting the importer ask.
 */
export function toTcgplayerCsv(entries: ScanEntry[]): string {
  const header = ["Quantity", "Name", "Set", "Card Number", "Language", "Printing"];
  const lines = [header.join(",")];
  for (const entry of entries) {
    const printing = entry.card.variants?.some((v) => v.kind === "reverse_holo" && v.present)
      ? "Reverse Holofoil"
      : "Normal";
    lines.push(
      [
        String(entry.quantity),
        csvEscape(entry.card.name),
        csvEscape(entry.card.set.name),
        csvEscape(entry.card.number),
        "English",
        printing,
      ].join(","),
    );
  }
  return lines.join("\n") + "\n";
}

/** Trigger a client-side file download. */
export function downloadFile(filename: string, content: string, mime = "text/csv"): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
