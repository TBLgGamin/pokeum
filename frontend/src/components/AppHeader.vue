<script setup lang="ts">
import BrandLogo from "@/components/BrandLogo.vue";
import type { View } from "@/composables/useView";

defineProps<{
  /** The screen showing; its link is marked as the current page. */
  view: View;
  /** Cards in the collection, shown beside its link once there are any. */
  count: number;
}>();

// Full-height tabs: the active one carries a bar on the header's bottom edge, so the
// current screen is shown by shape as well as colour.
const tab =
  "relative inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-3 text-sm font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 aria-[current=page]:text-foreground after:absolute after:inset-x-3 after:-bottom-px after:h-0.5 after:rounded-full after:bg-foreground after:opacity-0 aria-[current=page]:after:opacity-100";
</script>

<template>
  <header class="sticky top-0 z-20 shrink-0 border-b bg-background pt-[env(safe-area-inset-top)]">
    <div class="mx-auto flex h-14 w-full max-w-2xl items-stretch justify-between px-4 md:px-6">
      <BrandLogo class="my-auto h-6 w-auto shrink-0" />
      <nav aria-label="Main" class="-mr-3 flex items-stretch">
        <a href="#scan" :class="tab" :aria-current="view === 'scan' ? 'page' : undefined">Scan</a>
        <a href="#collection" :class="tab" :aria-current="view === 'collection' ? 'page' : undefined">
          Collection
          <span v-if="count" class="tabular-nums">
            {{ count }}<span class="sr-only"> cards</span>
          </span>
        </a>
      </nav>
    </div>
  </header>
</template>
