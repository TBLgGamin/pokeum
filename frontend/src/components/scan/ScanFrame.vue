<script setup lang="ts">
import { ref } from "vue";

defineProps<{
  /** A request is in flight: sweep a line down the frame. */
  scanning?: boolean;
}>();

const root = ref<HTMLDivElement | null>(null);

/** On-screen rect of the guide frame; the capture is cropped to it. */
function getRect(): DOMRect | null {
  return root.value?.getBoundingClientRect() ?? null;
}

defineExpose({ getRect });
</script>

<template>
  <!--
    Sized from the viewfinder (a size container): 72% of its width, but never taller than
    78% of its height (55.8cqh = 78cqh × 63/88, the card's aspect). The light stroke carries
    a thin dark outline so it stays visible on a white table too.
  -->
  <div
    ref="root"
    aria-hidden="true"
    class="relative aspect-[63/88] w-[min(72cqw,55.8cqh)] rounded-xl border-2 border-guide/90 outline outline-1 outline-black/25"
  >
    <div
      class="absolute inset-x-3 top-[6%] h-0.5 rounded-full bg-guide-accent opacity-0"
      :class="
        scanning
          ? 'animate-scan-sweep motion-reduce:top-1/2 motion-reduce:animate-none motion-reduce:opacity-100'
          : ''
      "
    />
  </div>
</template>
