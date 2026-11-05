# Pokeum brand assets

The mark is the **Lens P**: the counter of the letter P is a camera lens with a
pupil, so the first letter of the name is the eye that recognises your card. The
`o` in the wordmark (Nunito ExtraBold, outlined) carries the same pupil. Built
from one circle family (bowl r76 / counter r32 / pupil r13 on a 256-unit grid)
and a 44-unit stem. Full construction and rules: [`guidelines.md`](guidelines.md).

## Source of truth

Every PNG and every file in `frontend/public/` or `frontend/src/assets/brand/`
is generated or copied from this folder. Edit the SVG, never the PNG.

| Path | What it is |
| --- | --- |
| `svg/` | The 21 masters: symbol (+ `-small` for ≤ 24 px), horizontal, stacked and wordmark, each in light, `-dark`, `-violet`, `-black`, `-white` |
| `web/` | The set's ready-made web exports (favicon `.ico`/`.svg`/16/32/48, apple-touch, 192/512, maskable) and their `pokeum-symbol-*.svg` sources; `web/dark/` is the dark app icon |
| `png/` | Small transparent PNG kit for READMEs, slides and stores (generated, ≈ 120 KB) |
| `guidelines.md` | The set's guidelines, verbatim. Its §5 and §7 describe the Nuxt app and folders (`slides/`, `preview.html`) that are not mirrored here |

`png/` holds the symbol in violet/dark/white/black at 512 px, the horizontal
lockup (light, dark) at 1200 px wide and the stacked lockup (light, dark) at 800 px wide.

## Palette

mtgfmax theme tokens: neutral greys plus the violet `--ring` hue 297.347.

| Role | Light mode | Dark mode | Token |
| --- | --- | --- | --- |
| Symbol / accent | `#6F49B1` | `#D6C9F9` | `--ring` |
| Wordmark | `#0A0A0A` | `#FAFAFA` | `--foreground` |
| Background | `#FFFFFF` | `#0A0A0A` | `--background` |
| Tile / card (dark) | — | `#171717` | `--card` |

Contrast: ink on white 19.8:1 · paper on black 19.0:1 · white on violet 6.4:1 ·
lavender on `#0A0A0A` 12.8:1.

## Usage rules

- Light backgrounds take `pokeum-*.svg`; dark ones (`#0A0A0A`, `#171717`) take
  `pokeum-*-dark.svg`. Never put the violet symbol on dark (3:1, dull).
- App icons are the white symbol on a violet tile; the dark app icon is the
  lavender symbol on `#171717`.
- Keep one stem width (23 % of the symbol height) clear on every side.
- Minimum widths: horizontal 120 px, stacked 64 px, symbol 24 px. At ≤ 24 px use
  the `-small` files or `web/favicon.*`.
- Do not stretch, rotate, recolour, add shadows/outlines/gradients, move or
  resize the pupil, or retype the wordmark.
- Keep Pokémon trade dress (red/white split circle, Pikachu yellow) away from the mark.
- One-colour use: `-black` or `-white` files.

## Where each file is used

In-app SVGs (`frontend/src/assets/brand/`, copied verbatim from `svg/`), used by
`BrandLogo.vue`: `pokeum-horizontal.svg` / `pokeum-horizontal-dark.svg` for the header and
`pokeum-symbol.svg` / `pokeum-symbol-dark.svg` for compact marks. The app has no theme
toggle, so the component swaps them with a `<picture>` `prefers-color-scheme` source.

`frontend/public/` (shipped and precached by the service worker):

| File | Size | Source | Used by |
| --- | --- | --- | --- |
| `favicon.ico` | 16/32/48 | `web/favicon.ico` | Legacy tabs and bookmarks |
| `favicon.svg` | vector | `web/favicon.svg` | Modern tabs; violet → lavender under `prefers-color-scheme: dark` |
| `favicon-16x16.png`, `favicon-32x32.png` | 16, 32 | `web/favicon-16/32.png` | Crisp small tab renditions |
| `apple-touch-icon-180x180.png` | 180 | `web/apple-touch-icon.png` | iPhone home screen |
| `apple-touch-icon-167x167.png`, `apple-touch-icon-152x152.png` | 167, 152 | rendered | iPad home screen |
| `pwa-64x64.png` | 64 | rendered | Manifest icon |
| `pwa-192x192.png`, `pwa-512x512.png` | 192, 512 | `web/icon-192/512.png` | Manifest icons (`purpose: any`) |
| `maskable-icon-512x512.png` | 512 | `web/maskable-512.png` | Android adaptive icon; symbol verified inside the central 40 % radius |
| `logo.svg` | vector | `web/pokeum-symbol-app-icon.svg` | App icon artwork |
| `og-image.jpg` | 1200×630 | cropped from `docs/media/pokeum-hero.png` | Open Graph / Twitter large card (the README hero) |
| `og-image-square.png` | 1024×1024 | rendered | Square fallback for clients that crop to 1:1 |

The apple and `pwa-*` icons are opaque full-bleed squares: iOS composites
transparency onto black and both platforms apply their own mask.

## Regenerating

```bash
cd frontend
npm run generate-brand-assets
```

The script (`frontend/scripts/generate-brand-assets.mjs`, `sharp` only) is
idempotent. It copies the in-app SVGs and the set's web exports, renders the
sizes the set lacks, writes `png/`, then reads every output back and fails on a
wrong size, format or transparency, printing a path/size/bytes table. To adopt a
new revision of the set, replace `svg/`, `web/` and `guidelines.md` and rerun it.

## Trademark

Trademark clearance has not been done. Run a professional search before launch.
