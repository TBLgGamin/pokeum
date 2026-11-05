<script setup lang="ts">
import { cn } from "cn";
import { ref, watch, type HTMLAttributes } from "vue";

import { cardArtUrl, type CandidateOut } from "@/lib/api";

const props = defineProps<{
  card: Pick<CandidateOut, "card_id" | "image_url">;
  /** Defer loading until the art nears the viewport (long lists). */
  lazy?: boolean;
  class?: HTMLAttributes["class"];
}>();

// Offline with art that was never cached: keep the muted placeholder, not a broken-image icon.
const failed = ref(false);
watch(
  () => props.card.card_id,
  () => {
    failed.value = false;
  },
);
</script>

<template>
  <!-- Decorative: the card's name and set are always printed next to its art. -->
  <span
    :class="
      cn(
        'block aspect-[63/88] overflow-hidden rounded-[4.5%/3.2%] bg-muted',
        props.class,
      )
    "
  >
    <img
      v-if="!failed"
      :src="cardArtUrl(card)"
      alt=""
      :loading="lazy ? 'lazy' : undefined"
      decoding="async"
      class="size-full object-cover"
      @error="failed = true"
    />
  </span>
</template>
