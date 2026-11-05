/** Rear-camera preview lifecycle: ask for the stream, attach it, release it. */

import { computed, onScopeDispose, ref, type Ref } from "vue";

/** `starting` covers the permission prompt too; `unavailable` is denied, absent or insecure. */
export type CameraState = "starting" | "ready" | "unavailable";

/**
 * Drive a `<video>` element from the device camera.
 *
 * The stream is released when the owning scope is disposed, so leaving the
 * scan view always turns the camera light off.
 *
 * @param video the preview element the stream is attached to
 */
export function useCamera(video: Ref<HTMLVideoElement | null>) {
  const state = ref<CameraState>("starting");
  const isReady = computed(() => state.value === "ready");

  let stream: MediaStream | null = null;
  // A retry (or leaving the view) while the permission prompt is open must not
  // let the stale request attach its stream later.
  let attempt = 0;

  async function start(): Promise<void> {
    stop();
    const mine = ++attempt;
    state.value = "starting";

    if (!navigator.mediaDevices?.getUserMedia) {
      state.value = "unavailable";
      return;
    }

    try {
      const opened = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
        audio: false,
      });
      if (mine !== attempt || !video.value) {
        opened.getTracks().forEach((track) => track.stop());
        if (mine === attempt) state.value = "unavailable";
        return;
      }
      stream = opened;
      video.value.srcObject = opened;
      await video.value.play();
      if (mine === attempt) state.value = "ready";
    } catch {
      if (mine !== attempt) return;
      stop();
      state.value = "unavailable";
    }
  }

  function stop(): void {
    attempt += 1;
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    if (video.value) video.value.srcObject = null;
  }

  onScopeDispose(stop);

  return { state, isReady, start, stop };
}
