<script setup lang="ts">
import type { DialogContentEmits, DialogContentProps } from "reka-ui";
import type { HTMLAttributes } from "vue";
import { X } from "@lucide/vue";
import { reactiveOmit } from "@vueuse/core";
import { cn } from "cn";
import {
  DialogClose,
  DialogContent,
  DialogPortal,
  useForwardPropsEmits,
} from "reka-ui";
import SheetOverlay from "./SheetOverlay.vue";

interface SheetContentProps extends DialogContentProps {
  class?: HTMLAttributes["class"];
  side?: "top" | "right" | "bottom" | "left";
}

defineOptions({
  inheritAttrs: false,
});

const props = withDefaults(defineProps<SheetContentProps>(), {
  side: "right",
});
const emits = defineEmits<DialogContentEmits>();

const delegatedProps = reactiveOmit(props, "class", "side");

const forwarded = useForwardPropsEmits(delegatedProps, emits);
</script>

<!--
  Customised from the shadcn-vue new-york output: no shadow — the popover surface (one step
  lighter than the page in dark mode) plus a hairline border separates the sheet from the
  dimmed page — 200/300 ms motion instead of 300/500, a 40 px close target, and bottom
  sheets clear the home indicator.
-->
<template>
  <DialogPortal>
    <SheetOverlay />
    <DialogContent
      data-slot="sheet-content"
      :class="
        cn(
          'bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out fixed z-50 flex flex-col gap-4 transition ease-out data-[state=closed]:duration-200 data-[state=open]:duration-300',
          side === 'right' &&
            'data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right inset-y-0 right-0 h-full w-3/4 border-l sm:max-w-sm',
          side === 'left' &&
            'data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left inset-y-0 left-0 h-full w-3/4 border-r sm:max-w-sm',
          side === 'top' &&
            'data-[state=closed]:slide-out-to-top data-[state=open]:slide-in-from-top inset-x-0 top-0 h-auto border-b',
          side === 'bottom' &&
            'data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom inset-x-0 bottom-0 h-auto border-t pb-[env(safe-area-inset-bottom)]',
          props.class,
        )
      "
      v-bind="{ ...$attrs, ...forwarded }"
    >
      <slot />

      <DialogClose
        class="text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:ring-ring/50 absolute top-3 right-3 grid size-10 place-items-center rounded-md transition-colors outline-none focus-visible:ring-3 disabled:pointer-events-none"
      >
        <X class="size-5" />
        <span class="sr-only">Dismiss</span>
      </DialogClose>
    </DialogContent>
  </DialogPortal>
</template>
