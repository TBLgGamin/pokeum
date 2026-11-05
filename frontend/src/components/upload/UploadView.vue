<script setup lang="ts">
import { Check, CircleAlert, ImagePlus, LoaderCircle } from "@lucide/vue";
import { ref } from "vue";

import MatchSheet from "@/components/scan/MatchSheet.vue";
import { useCollection } from "@/composables/useCollection";
import { useScanSession } from "@/composables/useScanSession";
import type { CandidateOut } from "@/lib/api";
import { haptic } from "@/lib/haptics";

const collection = useCollection();
// No camera on this screen: the session only ever sees uploaded photos.
const session = useScanSession({
  video: ref(null),
  getFrameRect: () => null,
  cameraReady: ref(false),
});
const { scanning, pending, failure } = session;

const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const savedName = ref("");

async function identify(file: File | undefined): Promise<void> {
  if (!file || scanning.value) return;
  savedName.value = "";
  haptic(10);
  await session.scanFile(file);
}

function onPick(event: Event): void {
  const input = event.target as HTMLInputElement;
  void identify(input.files?.[0]);
  input.value = "";
}

function onDrop(event: DragEvent): void {
  dragging.value = false;
  void identify(event.dataTransfer?.files[0]);
}

function save(chosen?: CandidateOut): void {
  const card = session.accept(chosen);
  if (!card) return;
  collection.add(card);
  haptic([12, 35, 18]);
  savedName.value = card.name;
}
</script>

<template>
  <main
    class="mx-auto flex w-full max-w-2xl flex-1 flex-col px-4 pt-6 pb-12 md:px-6"
  >
    <h1 class="text-xl leading-tight font-semibold tracking-tight">Upload</h1>
    <p class="mb-4 text-sm text-muted-foreground">
      Identify a card from a photo; it is saved to your collection like a scan.
    </p>

    <button
      type="button"
      class="flex min-h-56 flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-6 text-center outline-none transition-colors hover:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-wait disabled:opacity-70"
      :class="dragging ? 'border-ring bg-muted/50' : 'border-border'"
      :disabled="scanning"
      aria-label="Upload photo"
      @click="fileInput?.click()"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop.prevent="onDrop"
    >
      <LoaderCircle
        v-if="scanning"
        class="size-8 animate-spin text-muted-foreground"
        aria-hidden="true"
      />
      <ImagePlus
        v-else
        class="size-8 text-muted-foreground"
        aria-hidden="true"
      />
      <span class="font-medium">{{
        scanning ? "Recognizing…" : "Choose a photo"
      }}</span>
      <span class="text-sm text-muted-foreground"
        >or drop one here · one card per photo</span
      >
    </button>

    <p
      role="status"
      class="mt-4 flex min-h-6 items-center justify-center gap-2 text-sm"
    >
      <template v-if="savedName">
        <Check class="size-4 shrink-0" aria-hidden="true" />
        <span>Saved {{ savedName }}</span>
      </template>
      <template v-else-if="failure">
        <CircleAlert class="size-4 shrink-0" aria-hidden="true" />
        <span :class="failure.kind === 'error' ? 'text-destructive' : ''">{{
          failure.message
        }}</span>
      </template>
    </p>

    <MatchSheet :scan="pending" @save="save" @dismiss="session.dismiss" />

    <input
      ref="fileInput"
      type="file"
      accept="image/*"
      hidden
      @change="onPick"
    />
  </main>
</template>
