<script setup lang="ts">
import { Minus, Plus } from "@lucide/vue";
import { computed, ref } from "vue";

import BrandLogo from "@/components/BrandLogo.vue";
import CardArt from "@/components/CardArt.vue";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemTitle,
} from "@/components/ui/item";
import { useCollection } from "@/composables/useCollection";
import { cardCode, cardCount } from "@/lib/copy";
import {
  downloadFile,
  EXPORT_FORMATS,
  type ExportFormat,
} from "@/lib/exporters";
import { haptic } from "@/lib/haptics";

const collection = useCollection();
const { entries, totalCards } = collection;
const query = ref("");

/** Entries matching every word of the search, against name, set, code and number. */
const shown = computed(() => {
  const words = query.value.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return entries.value;
  return entries.value.filter((entry) => {
    const card = entry.card;
    const haystack =
      `${card.name} ${card.set.name} ${card.set.code ?? ""} ${card.number}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });
});

function exportAs(format: ExportFormat): void {
  const stamp = new Date().toISOString().slice(0, 10);
  downloadFile(
    `pokeum-collection-${format.id}-${stamp}.${format.extension}`,
    format.render(entries.value),
    format.mime,
  );
  haptic(12);
}
</script>

<template>
  <main
    class="mx-auto flex w-full max-w-2xl flex-1 flex-col px-4 pb-12 md:px-6"
  >
    <template v-if="entries.length">
      <div class="flex items-end justify-between gap-4 pt-6 pb-3">
        <div>
          <h1 class="text-xl leading-tight font-semibold tracking-tight">
            Collection
          </h1>
          <p class="text-sm text-muted-foreground tabular-nums">
            {{ cardCount(totalCards) }}
          </p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger as-child>
            <Button>Export</Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" class="w-64">
            <DropdownMenuLabel>Export as</DropdownMenuLabel>
            <DropdownMenuItem
              v-for="format in EXPORT_FORMATS"
              :key="format.id"
              class="flex-col items-start gap-0"
              @select="exportAs(format)"
            >
              <span class="font-medium">{{ format.label }}</span>
              <span class="text-xs text-muted-foreground">{{
                format.hint
              }}</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <p
        role="note"
        class="mb-4 rounded-lg border bg-muted/50 px-3 py-2 text-sm text-muted-foreground"
      >
        <span class="font-medium text-foreground"
          >Only saved in this browser.</span
        >
        There is no account or cloud copy: clearing site data, a private window
        or another device starts empty. Export your cards before you leave.
      </p>

      <Input
        v-model="query"
        type="search"
        placeholder="Search name, set or number"
        aria-label="Search collection"
        class="mb-2"
      />
      <p
        v-if="!shown.length"
        class="py-8 text-center text-sm text-muted-foreground"
      >
        No cards match “{{ query }}”.
      </p>

      <ItemGroup>
        <Item
          v-for="entry in shown"
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
            <span
              class="w-6 text-center text-base tabular-nums"
              aria-live="polite"
            >
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
