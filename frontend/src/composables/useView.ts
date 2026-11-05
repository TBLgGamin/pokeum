/** Which of the two screens is showing, kept in the URL hash. */

import { onScopeDispose, ref, type Ref } from "vue";

export type View = "scan" | "collection";

/**
 * Follow `#scan` / `#collection`.
 *
 * Using the hash makes the platform Back button (and an installed PWA's
 * gesture) step from the collection to the scanner instead of closing the
 * app, and lets a screen be linked to. Navigation is plain `<a href="#…">`;
 * anything that is not `#collection` is the scanner.
 */
export function useView(): Ref<View> {
  const view = ref<View>("scan");
  const sync = () => {
    view.value = window.location.hash === "#collection" ? "collection" : "scan";
  };
  sync();
  window.addEventListener("hashchange", sync);
  onScopeDispose(() => window.removeEventListener("hashchange", sync));
  return view;
}
