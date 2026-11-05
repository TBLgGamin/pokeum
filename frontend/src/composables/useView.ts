/** Which screen is showing, kept in the URL hash. */

import { onScopeDispose, ref, type Ref } from "vue";

export type View = "scan" | "upload" | "collection";

/**
 * Follow `#scan` / `#upload` / `#collection`.
 *
 * Using the hash makes the platform Back button (and an installed PWA's
 * gesture) step from the collection to the scanner instead of closing the
 * app, and lets a screen be linked to. Navigation is plain `<a href="#…">`;
 * anything unrecognised is the scanner.
 */
export function useView(): Ref<View> {
  const view = ref<View>("scan");
  const sync = () => {
    const hash = window.location.hash;
    view.value =
      hash === "#collection"
        ? "collection"
        : hash === "#upload"
          ? "upload"
          : "scan";
  };
  sync();
  window.addEventListener("hashchange", sync);
  onScopeDispose(() => window.removeEventListener("hashchange", sync));
  return view;
}
