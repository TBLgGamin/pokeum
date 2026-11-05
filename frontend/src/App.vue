<script setup lang="ts">
import AppHeader from "@/components/AppHeader.vue";
import CollectionView from "@/components/collection/CollectionView.vue";
import ScanView from "@/components/scan/ScanView.vue";
import UploadView from "@/components/upload/UploadView.vue";
import { provideCollection } from "@/composables/useCollection";
import { useView } from "@/composables/useView";

const collection = provideCollection();
const view = useView();
</script>

<template>
  <!-- The scanner is a fixed-height viewfinder; upload and collection are ordinary scrolling pages. -->
  <div
    class="flex flex-col"
    :class="view === 'scan' ? 'h-dvh overflow-hidden' : 'min-h-dvh'"
  >
    <AppHeader :view="view" :count="collection.totalCards.value" />
    <ScanView v-if="view === 'scan'" />
    <UploadView v-else-if="view === 'upload'" />
    <CollectionView v-else />
  </div>
</template>
