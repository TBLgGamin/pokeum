<script setup lang="ts">
import { Check, CameraOff, CircleAlert, ImagePlus } from "@lucide/vue";
import { computed, onMounted, ref, watch } from "vue";

import MatchSheet from "@/components/scan/MatchSheet.vue";
import ScanFrame from "@/components/scan/ScanFrame.vue";
import ShutterButton from "@/components/scan/ShutterButton.vue";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useAutoCapture } from "@/composables/useAutoCapture";
import { useCamera } from "@/composables/useCamera";
import { useCollection } from "@/composables/useCollection";
import { useScanSession } from "@/composables/useScanSession";
import type { CandidateOut } from "@/lib/api";
import { haptic } from "@/lib/haptics";
import { loadAutoScan, saveAutoScan } from "@/lib/preferences";

/** How long "Saved …" stays in the status line. */
const SAVED_NOTICE_MS = 4000;

const video = ref<HTMLVideoElement | null>(null);
const frame = ref<InstanceType<typeof ScanFrame> | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const autoScan = ref(loadAutoScan());
const savedName = ref("");

let savedTimer: number | undefined;

const collection = useCollection();
const camera = useCamera(video);
const session = useScanSession({
  video,
  getFrameRect: () => frame.value?.getRect() ?? null,
  cameraReady: camera.isReady,
});
const { scanning, pending, failure } = session;

// Hands-free scanning: sample the guide crop and capture the moment the frame
// is worth sending, so the user only has to hold the card still.
const autoCapture = useAutoCapture({
  video,
  getFrameRect: () => frame.value?.getRect() ?? null,
  enabled: computed(() => autoScan.value && camera.isReady.value && !pending.value),
  busy: scanning,
  onStable: scan,
});

onMounted(() => {
  void camera.start();
});

watch(camera.state, (state) => {
  if (state === "ready") autoCapture.start();
  else autoCapture.stop();
});

// A failure describes the card that was just tried; once the frame changes (auto-capture
// re-arms) or a new scan starts it is stale.
watch(autoCapture.armed, (armed) => {
  if (armed) session.clearFailure();
});
watch(scanning, (busy) => {
  if (busy) savedName.value = "";
});

watch(pending, (scan) => {
  if (scan) haptic([14, 45, 22]);
});
watch(failure, (miss) => {
  if (miss) haptic(miss.kind === "error" ? [20, 60, 20] : [18, 50, 18]);
});

interface Status {
  text: string;
  /** `muted` is idle chatter, `default` asks for attention, `error` is a failed request. */
  tone: "muted" | "default" | "error";
  icon?: "saved" | "alert";
}

/** The one line that says what the scanner is doing, in priority order. */
const status = computed<Status>(() => {
  if (scanning.value) return { text: "Recognizing…", tone: "default" };
  if (camera.state.value === "starting") return { text: "Starting camera…", tone: "muted" };
  if (autoScan.value && camera.isReady.value && autoCapture.hint.value) {
    return { text: autoCapture.hint.value, tone: "default" };
  }
  if (savedName.value) return { text: `Saved ${savedName.value}`, tone: "default", icon: "saved" };
  if (failure.value) {
    return {
      text: failure.value.message,
      tone: failure.value.kind === "error" ? "error" : "default",
      icon: "alert",
    };
  }
  if (camera.state.value === "unavailable") return { text: "", tone: "muted" };
  return {
    text: autoScan.value ? "Hold a card in the frame" : "Frame a card, then tap Scan",
    tone: "muted",
  };
});

/** Any outcome means this card has been tried; auto-capture re-arms once the frame changes. */
async function scan(): Promise<void> {
  haptic(12);
  await session.scanCamera();
  autoCapture.disarm();
}

async function onUpload(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) {
    haptic(10);
    await session.scanFile(file);
    autoCapture.disarm();
  }
  input.value = "";
}

function setAutoScan(enabled: boolean): void {
  autoScan.value = enabled;
  saveAutoScan(enabled);
  haptic(8);
  // Turning it back on is an explicit "scan for me": re-arm now instead of
  // waiting for the frame to change.
  if (enabled) autoCapture.start();
}

function save(chosen?: CandidateOut): void {
  const card = session.accept(chosen);
  if (!card) return;
  collection.add(card);
  haptic([12, 35, 18]);
  savedName.value = card.name;
  window.clearTimeout(savedTimer);
  savedTimer = window.setTimeout(() => {
    savedName.value = "";
  }, SAVED_NOTICE_MS);
}
</script>

<template>
  <main class="mx-auto flex min-h-0 w-full max-w-2xl flex-1 flex-col md:px-6">
    <h1 class="sr-only">Scan a card</h1>
    <!-- A size container: the guide frame is sized from this box (see ScanFrame). -->
    <section
      aria-label="Camera"
      class="relative min-h-0 flex-1 overflow-hidden bg-muted [container-type:size] md:rounded-2xl"
    >
      <video
        ref="video"
        aria-label="Camera preview"
        autoplay
        playsinline
        muted
        class="absolute inset-0 size-full object-cover transition-opacity duration-300 motion-reduce:transition-none"
        :class="camera.isReady.value ? 'opacity-100' : 'opacity-0'"
      />
      <div
        v-if="camera.isReady.value"
        class="pointer-events-none absolute inset-0 grid place-items-center"
      >
        <ScanFrame ref="frame" :scanning="scanning" />
      </div>
      <Empty v-if="camera.state.value === 'unavailable'" class="absolute inset-0">
        <EmptyHeader>
          <EmptyMedia>
            <CameraOff class="size-8 text-muted-foreground" aria-hidden="true" />
          </EmptyMedia>
          <EmptyTitle>Camera unavailable</EmptyTitle>
          <!-- Foreground at 70%, not muted: muted text on the muted viewfinder is 4.4:1 in light mode. -->
          <EmptyDescription class="text-foreground/70">
            Allow camera access, or upload a photo.
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button variant="secondary" @click="camera.start()">Try again</Button>
        </EmptyContent>
      </Empty>
    </section>

    <div class="shrink-0 px-4 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))] md:px-0 md:pb-6">
      <p
        role="status"
        class="mb-3 flex min-h-6 items-center justify-center gap-2 text-center text-sm"
        :class="{
          'text-muted-foreground': status.tone === 'muted',
          'text-foreground': status.tone === 'default',
          'text-destructive': status.tone === 'error',
        }"
      >
        <Check v-if="status.icon === 'saved'" class="size-4 shrink-0" aria-hidden="true" />
        <CircleAlert v-else-if="status.icon === 'alert'" class="size-4 shrink-0" aria-hidden="true" />
        <span>{{ status.text }}</span>
      </p>

      <div
        v-if="camera.state.value !== 'unavailable'"
        class="grid grid-cols-[1fr_auto_1fr] items-center gap-2"
      >
        <Button
          variant="ghost"
          aria-label="Upload photo"
          class="justify-self-start"
          :disabled="scanning"
          @click="fileInput?.click()"
        >
          <ImagePlus aria-hidden="true" />
          Upload
        </Button>
        <ShutterButton :busy="scanning" :disabled="!camera.isReady.value" @click="scan" />
        <Label class="h-10 cursor-pointer justify-self-end gap-2 pl-2 font-medium">
          Auto-scan
          <Switch :model-value="autoScan" @update:model-value="setAutoScan" />
        </Label>
      </div>
      <Button v-else size="lg" class="w-full" :disabled="scanning" @click="fileInput?.click()">
        <ImagePlus aria-hidden="true" />
        Upload photo
      </Button>
    </div>

    <MatchSheet :scan="pending" @save="save" @dismiss="session.dismiss" />

    <input ref="fileInput" type="file" accept="image/*" hidden @change="onUpload" />
  </main>
</template>
