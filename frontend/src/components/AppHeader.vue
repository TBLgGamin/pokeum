<script setup lang="ts">
import BrandLogo from "@/components/BrandLogo.vue";
import type { View } from "@/composables/useView";

defineProps<{
  /** The screen showing; its link is marked as the current page. */
  view: View;
  /** Cards in the collection, shown beside its link once there are any. */
  count: number;
}>();

/** Source repository, linked from the header's GitHub icon. */
const REPO_URL = "https://github.com/TBLgGamin/pokeum";

// Full-height tabs: the active one carries a bar on the header's bottom edge, so the
// current screen is shown by shape as well as colour. Narrow padding on phones keeps
// three tabs plus the GitHub link on one line at 320 px.
const tab =
  "relative inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 text-sm font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 sm:px-3 aria-[current=page]:text-foreground after:absolute after:inset-x-2 sm:after:inset-x-3 after:-bottom-px after:h-0.5 after:rounded-full after:bg-foreground after:opacity-0 aria-[current=page]:after:opacity-100";
</script>

<template>
  <header
    class="sticky top-0 z-20 shrink-0 border-b bg-background pt-[env(safe-area-inset-top)]"
  >
    <!-- Full width on purpose: the mark sits at the screen's left edge, the
         tabs at its right, while the content below keeps its centred column. -->
    <div class="flex h-14 w-full items-stretch justify-between px-4 md:px-6">
      <a
        href="#scan"
        aria-label="pokeum, go to scanner"
        class="my-auto shrink-0 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <BrandLogo variant="symbol" class="size-8" />
      </a>
      <nav aria-label="Main" class="-mr-2 flex items-stretch">
        <a
          href="#scan"
          :class="tab"
          :aria-current="view === 'scan' ? 'page' : undefined"
          >Scan</a
        >
        <a
          href="#upload"
          :class="tab"
          :aria-current="view === 'upload' ? 'page' : undefined"
        >
          Upload
        </a>
        <a
          href="#collection"
          :class="tab"
          :aria-current="view === 'collection' ? 'page' : undefined"
        >
          Collection
          <span v-if="count" class="tabular-nums">
            {{ count }}<span class="sr-only"> cards</span>
          </span>
        </a>
        <a
          :href="REPO_URL"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="pokeum on GitHub"
          :class="tab"
        >
          <!-- GitHub mark (lucide dropped brand icons). -->
          <svg
            viewBox="0 0 16 16"
            class="size-5"
            fill="currentColor"
            aria-hidden="true"
          >
            <path
              d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
            />
          </svg>
        </a>
      </nav>
    </div>
  </header>
</template>
