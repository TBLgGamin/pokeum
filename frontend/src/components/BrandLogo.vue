<script setup lang="ts">
import type { HTMLAttributes } from "vue";

// `?no-inline` keeps the SVGs as files: Vite would otherwise inline the tiny symbols as
// data URIs, whose commas break `srcset` parsing in the <picture> below.
import horizontalDark from "@/assets/brand/pokeum-horizontal-dark.svg?no-inline";
import horizontal from "@/assets/brand/pokeum-horizontal.svg?no-inline";
import symbolDark from "@/assets/brand/pokeum-symbol-dark.svg?no-inline";
import symbol from "@/assets/brand/pokeum-symbol.svg?no-inline";

const props = withDefaults(
  defineProps<{
    /** `horizontal` is the header lockup (min 120 px wide); `symbol` the mark alone. */
    variant?: "horizontal" | "symbol";
    class?: HTMLAttributes["class"];
  }>(),
  { variant: "horizontal" },
);

const files = {
  horizontal: { light: horizontal, dark: horizontalDark },
  symbol: { light: symbol, dark: symbolDark },
};
</script>

<template>
  <!--
    The dark files are the brand's dark-mode versions (lavender on near-black), never the
    violet mark reversed. `contents` lets the <img> itself be the flex/grid item.
  -->
  <picture class="contents">
    <source media="(prefers-color-scheme: dark)" :srcset="files[props.variant].dark" />
    <img :src="files[props.variant].light" alt="Pokeum" :class="props.class" />
  </picture>
</template>
