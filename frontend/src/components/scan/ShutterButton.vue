<script setup lang="ts">
import { Button } from "@/components/ui/button";

defineProps<{
  /** A scan is running: the button is inert and its centre pulses. */
  busy?: boolean;
  /** Nothing to scan with yet (camera still starting): inert and dimmed. */
  disabled?: boolean;
}>();
</script>

<template>
  <!-- A lens with a pupil, the logo's own idea, in neutral tokens (never the red/white split circle). -->
  <Button
    variant="ghost"
    aria-label="Scan card"
    :aria-busy="busy"
    :disabled="busy || disabled"
    class="group relative size-[4.5rem] rounded-full p-0 hover:bg-transparent dark:hover:bg-transparent"
    :class="busy ? 'disabled:opacity-100' : 'disabled:opacity-40'"
  >
    <span
      aria-hidden="true"
      class="absolute inset-0 rounded-full border-[3px] border-foreground"
    />
    <span
      aria-hidden="true"
      class="size-[3.25rem] rounded-full bg-foreground transition-transform duration-100 group-active:scale-90 motion-reduce:transition-none"
      :class="busy ? 'scale-75 animate-pulse' : ''"
    />
  </Button>
</template>
