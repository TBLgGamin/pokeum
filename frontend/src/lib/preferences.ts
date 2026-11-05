/** Device-local UI preferences; blocked storage (private mode) falls back to the defaults. */

/** Remembers the auto-capture toggle across launches. */
const AUTO_SCAN_KEY = "pokeum.autoScan";

/** Auto-capture preference: on unless it was explicitly turned off. */
export function loadAutoScan(): boolean {
  try {
    return localStorage.getItem(AUTO_SCAN_KEY) !== "0";
  } catch {
    return true;
  }
}

/** Persist the auto-capture preference; blocked storage just means it will not stick. */
export function saveAutoScan(enabled: boolean): void {
  try {
    localStorage.setItem(AUTO_SCAN_KEY, enabled ? "1" : "0");
  } catch {
    // Blocked storage: the toggle still works for this session.
  }
}
