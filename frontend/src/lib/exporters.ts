/** Export scanned cards to the formats collectors import elsewhere.

There is no shared standard: each tracker reads its own CSV. What every
importer can map is name + set + collector number + printing + quantity
(TCGplayer, Collectr-style importers, spreadsheets), so the CSV formats differ
mostly in column names. Condition is never written: a scanner cannot judge it,
and a made-up "Near Mint" is worse than letting the importer ask.
*/

import type { CandidateOut } from "./api";

export interface ScanEntry {
  card: CandidateOut;
  quantity: number;
  scannedAt: string;
}

/** One way to export the collection, as offered in the export menu. */
export interface ExportFormat {
  id: string;
  /** Menu label. */
  label: string;
  /** One line under the label: where the file goes. */
  hint: string;
  extension: string;
  mime: string;
  render: (entries: ScanEntry[]) => string;
}

function csvEscape(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function csv(header: string[], rows: string[][]): string {
  return (
    [header, ...rows].map((row) => row.map(csvEscape).join(",")).join("\n") +
    "\n"
  );
}

/** TCGplayer's printing vocabulary, which most importers also accept. */
function printing(entry: ScanEntry): string {
  const has = (kind: string) =>
    entry.card.variants?.some((v) => v.kind === kind && v.present) ?? false;
  if (has("first_edition")) return "1st Edition";
  if (has("reverse_holo")) return "Reverse Holofoil";
  return "Normal";
}

/**
 * TCGplayer-style collection CSV (Quantity, Name, Set, Card Number, Language,
 * Printing) — the de-facto interchange format: the TCGplayer app, Collectr-
 * style and Dragon Shield importers map it directly.
 */
export function toTcgplayerCsv(entries: ScanEntry[]): string {
  return csv(
    ["Quantity", "Name", "Set", "Card Number", "Language", "Printing"],
    entries.map((e) => [
      String(e.quantity),
      e.card.name,
      e.card.set.name,
      e.card.number,
      "English",
      printing(e),
    ]),
  );
}

/** Every field pokeum knows, one row per card: for spreadsheets and custom importers. */
export function toSpreadsheetCsv(entries: ScanEntry[]): string {
  return csv(
    [
      "Name",
      "Set",
      "Set Code",
      "Number",
      "Rarity",
      "Printing",
      "Quantity",
      "Card ID",
      "Added",
    ],
    entries.map((e) => [
      e.card.name,
      e.card.set.name,
      e.card.set.code ?? "",
      e.card.number,
      e.card.rarity ?? "",
      printing(e),
      String(e.quantity),
      e.card.card_id,
      e.scannedAt.slice(0, 10),
    ]),
  );
}

/**
 * Pokémon TCG Live / Limitless deck-list lines ("2 Pikachu PAL 25", the
 * number without its "/193" total): paste
 * into a deck builder. Cards without a printed set code use the set name.
 */
export function toDeckList(entries: ScanEntry[]): string {
  return (
    entries
      .map(
        (e) =>
          `${e.quantity} ${e.card.name} ${e.card.set.code ?? e.card.set.name} ${e.card.number.split("/")[0]}`,
      )
      .join("\n") + "\n"
  );
}

/** A readable checklist for chats, forums and trade posts. */
export function toChecklist(entries: ScanEntry[]): string {
  return (
    entries
      .map((e) => {
        const extra = printing(e) === "Normal" ? "" : ` (${printing(e)})`;
        return `${e.quantity}× ${e.card.name} — ${e.card.set.name} ${e.card.number}${extra}`;
      })
      .join("\n") + "\n"
  );
}

/** Lossless JSON backup of the whole collection, as stored on this device. */
export function toJsonBackup(entries: ScanEntry[]): string {
  return JSON.stringify({ app: "pokeum", version: 1, entries }, null, 2) + "\n";
}

export const EXPORT_FORMATS: ExportFormat[] = [
  {
    id: "tcgplayer",
    label: "TCGplayer CSV",
    hint: "TCGplayer, Collectr, Dragon Shield importers",
    extension: "csv",
    mime: "text/csv",
    render: toTcgplayerCsv,
  },
  {
    id: "spreadsheet",
    label: "Spreadsheet CSV",
    hint: "Excel, Google Sheets, every detail",
    extension: "csv",
    mime: "text/csv",
    render: toSpreadsheetCsv,
  },
  {
    id: "decklist",
    label: "Deck list",
    hint: "Pokémon TCG Live, Limitless",
    extension: "txt",
    mime: "text/plain",
    render: toDeckList,
  },
  {
    id: "checklist",
    label: "Checklist",
    hint: "Readable list for trades and chats",
    extension: "txt",
    mime: "text/plain",
    render: toChecklist,
  },
  {
    id: "json",
    label: "JSON backup",
    hint: "Everything, for safekeeping",
    extension: "json",
    mime: "application/json",
    render: toJsonBackup,
  },
];

/** Trigger a client-side file download. */
export function downloadFile(
  filename: string,
  content: string,
  mime = "text/csv",
): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
