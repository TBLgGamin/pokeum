<script setup lang="ts">
import { Download, Minus, Plus } from "@lucide/vue";

import BrandLogo from "@/components/BrandLogo.vue";
import CardArt from "@/components/CardArt.vue";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemTitle } from "@/components/ui/item";
import { useCollection } from "@/composables/useCollection";
import { cardCode, cardCount } from "@/lib/copy";
import { downloadFile, toTcgplayerCsv } from "@/lib/exporters";
import { haptic } from "@/lib/haptics";

const collection = useCollection();
const { entries, totalCards } = collection;

function exportCsv(): void {
  const stamp = new Date().toISOString().slice(0, 10);
  downloadFile(`pokeum-collection-${stamp}.csv`, toTcgplayerCsv(entries.value));
  haptic(12);
}

function clearAll(): void {
  collection.clear();
  haptic(12);
}
</script>

<template>
  <main class="mx-auto flex w-full max-w-2xl flex-1 flex-col px-4 pb-12 md:px-6">
    <template v-if="entries.length">
      <div class="flex items-end justify-between gap-4 pt-6 pb-2">
        <div>
          <h1 class="text-xl leading-tight font-semibold tracking-tight">Collection</h1>
          <p class="text-sm text-muted-foreground tabular-nums">{{ cardCount(totalCards) }}</p>
        </div>
        <Button @click="exportCsv">
          <Download aria-hidden="true" />
          Export CSV
        </Button>
      </div>

      <ItemGroup>
        <Item
          v-for="entry in entries"
          :key="entry.card.card_id"
          role="listitem"
          size="sm"
          class="flex-nowrap px-0"
        >
          <CardArt :card="entry.card" lazy class="w-10 shrink-0" />
          <ItemContent class="min-w-0">
            <ItemTitle class="w-full min-w-0 text-base">
              <span class="truncate">{{ entry.card.name }}</span>
            </ItemTitle>
            <ItemDescription class="line-clamp-1">
              {{ entry.card.set.name }} · {{ cardCode(entry.card) }}
            </ItemDescription>
          </ItemContent>
          <ItemActions class="shrink-0 gap-0">
            <Button
              variant="ghost"
              size="icon"
              :aria-label="`Remove one ${entry.card.name}`"
              @click="collection.adjust(entry.card.card_id, -1)"
            >
              <Minus aria-hidden="true" />
            </Button>
            <span class="w-6 text-center text-base tabular-nums" aria-live="polite">
              {{ entry.quantity }}
            </span>
            <Button
              variant="ghost"
              size="icon"
              :aria-label="`Add one ${entry.card.name}`"
              @click="collection.adjust(entry.card.card_id, 1)"
            >
              <Plus aria-hidden="true" />
            </Button>
          </ItemActions>
        </Item>
      </ItemGroup>

      <div class="flex justify-center pt-8">
        <AlertDialog>
          <AlertDialogTrigger as-child>
            <Button variant="ghost" class="text-destructive hover:text-destructive">
              Clear collection
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Clear {{ cardCount(totalCards) }}?</AlertDialogTitle>
              <AlertDialogDescription>
                This removes the collection from this device. It can't be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction :class="buttonVariants({ variant: 'destructive' })" @click="clearAll">
                Clear collection
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    </template>

    <template v-else>
      <h1 class="sr-only">Collection</h1>
      <Empty class="min-h-[60dvh]">
        <EmptyHeader>
          <EmptyMedia>
            <BrandLogo variant="symbol" class="size-16" />
          </EmptyMedia>
          <EmptyTitle>No cards yet</EmptyTitle>
          <EmptyDescription>Saved cards appear here.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button as-child>
            <a href="#scan">Scan a card</a>
          </Button>
        </EmptyContent>
      </Empty>
    </template>
  </main>
</template>
