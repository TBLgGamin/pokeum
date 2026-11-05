<script setup lang="ts">
import { CircleHelp } from "@lucide/vue";
import { useMediaQuery } from "@vueuse/core";
import { computed, ref, watch } from "vue";

import CardArt from "@/components/CardArt.vue";
import { Button } from "@/components/ui/button";
import { Item, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { PendingScan } from "@/composables/useScanSession";
import type { CandidateOut } from "@/lib/api";
import { cardCode, variantLabel } from "@/lib/copy";

const props = defineProps<{
  /** The verdict to confirm; null closes the sheet. */
  scan: PendingScan | null;
}>();

const emit = defineEmits<{
  /** Save the matched card, or the printing the user picked. */
  save: [card?: CandidateOut];
  /** Leave without saving (close button, Esc, tap outside). */
  dismiss: [];
}>();

// Wide screens have room beside the camera, so the sheet becomes a side panel there.
const isWide = useMediaQuery("(min-width: 768px)");

// The last verdict stays rendered while the sheet slides out; following `scan` straight
// to null would blank the content mid-animation.
const shown = ref<PendingScan | null>(props.scan);
const choosing = ref(false);
const saveButton = ref<InstanceType<typeof Button> | null>(null);

watch(
  () => props.scan,
  (scan) => {
    if (!scan) return;
    shown.value = scan;
    // Same artwork, printings the signals could not separate: the user is the
    // tie-breaker, so open straight into the chooser.
    choosing.value = scan.result.status === "ambiguous";
  },
  { immediate: true },
);

/** The printings on offer: the match first, then its ranked alternates. */
const options = computed<CandidateOut[]>(() =>
  shown.value ? [shown.value.match, ...shown.value.result.alternates].slice(0, 5) : [],
);

/** Land on "Save card" so a keyboard user confirms with Enter; the chooser keeps its default. */
function focusSave(event: Event): void {
  const button = saveButton.value?.$el as HTMLElement | undefined;
  if (!button) return;
  event.preventDefault();
  button.focus();
}
</script>

<template>
  <Sheet :open="scan !== null" @update:open="(open: boolean) => !open && emit('dismiss')">
    <SheetContent
      :side="isWide ? 'right' : 'bottom'"
      class="gap-0"
      :class="isWide ? '' : 'max-h-[85dvh] rounded-t-2xl'"
      @open-auto-focus="focusSave"
    >
      <template v-if="shown">
        <template v-if="choosing">
          <SheetHeader class="p-4 pr-16">
            <SheetTitle class="text-xl leading-tight">
              {{ shown.result.status === "ambiguous" ? "Which printing?" : "Pick the right card" }}
            </SheetTitle>
            <SheetDescription class="sr-only">
              Choose the card you scanned, or dismiss to scan again.
            </SheetDescription>
          </SheetHeader>
          <ul class="flex min-h-0 flex-col overflow-y-auto overscroll-contain pb-4">
            <li v-for="option in options" :key="option.card_id">
              <Item
                as="button"
                type="button"
                size="sm"
                class="w-full cursor-pointer flex-nowrap rounded-none px-4 text-left hover:bg-accent/60 active:bg-accent"
                @click="emit('save', option)"
              >
                <CardArt :card="option" class="w-10 shrink-0" />
                <ItemContent class="min-w-0">
                  <ItemTitle class="w-full min-w-0 text-base">
                    <span class="truncate">{{ option.name }}</span>
                  </ItemTitle>
                  <ItemDescription class="line-clamp-1">
                    {{ option.set.name }} · {{ cardCode(option) }}
                  </ItemDescription>
                </ItemContent>
              </Item>
            </li>
          </ul>
        </template>

        <template v-else>
          <div class="flex items-start gap-4 p-4 pr-16">
            <CardArt :card="shown.match" class="w-24 shrink-0" />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <p
                v-if="shown.result.status === 'uncertain'"
                class="flex items-center gap-1.5 text-sm text-muted-foreground"
              >
                <CircleHelp class="size-4 shrink-0" aria-hidden="true" />
                Low confidence
              </p>
              <SheetTitle class="text-xl leading-tight">{{ shown.match.name }}</SheetTitle>
              <SheetDescription class="text-base">{{ shown.match.set.name }}</SheetDescription>
              <p class="text-sm text-muted-foreground tabular-nums">{{ cardCode(shown.match) }}</p>
              <p v-if="variantLabel(shown.match)" class="pt-1 text-sm">
                {{ variantLabel(shown.match) }}
              </p>
            </div>
          </div>
          <SheetFooter class="grid grid-cols-2 gap-3 p-4 pt-2">
            <Button variant="outline" size="lg" @click="choosing = true">Not this one</Button>
            <Button ref="saveButton" size="lg" @click="emit('save')">Save card</Button>
          </SheetFooter>
        </template>
      </template>
    </SheetContent>
  </Sheet>
</template>
