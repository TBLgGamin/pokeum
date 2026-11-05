# Pokeum — Logo Guidelines

## 1. The logo
- **Idea**: the counter of the P is a camera lens with a pupil — Pokeum's first letter is the eye that recognises your card. The `o` in the wordmark carries the same pupil.
- **Versions**: horizontal (primary) · stacked · symbol · wordmark. Each in light, dark, violet, black and white.
- **Construction** (256-unit symbol grid): bowl ring r76 / counter r32 / pupil r13, centre (128,108); stem 44 wide, round foot r22. Dark/white versions are thinned ~1 unit to offset irradiation. `-small` cuts open the counter (r34, pupil r12) for ≤ 24 px.
- **Wordmark**: Nunito ExtraBold (800), outlined, tracking −6/1000 em, custom pupil in the `o`. Nunito is SIL OFL — free for logo use.

## 2. Colour — mtgfmax tokens
The palette is mtgfmax's theme (`app/assets/css/tailwind.css`): neutral greys plus the violet `--ring` hue 297.347.

| Role | Light mode | Dark mode | mtgfmax token |
|---|---|---|---|
| Symbol / accent | `#6F49B1` · oklch(0.5 0.16 297.347) | `#D6C9F9` · oklch(0.862 0.067 297.347) | `--ring` |
| Wordmark | `#0A0A0A` · oklch(0.145 0 0) | `#FAFAFA` · oklch(0.985 0 0) | `--foreground` |
| Background | `#FFFFFF` · oklch(1 0 0) | `#0A0A0A` · oklch(0.145 0 0) | `--background` |
| Tile / card (dark) | — | `#171717` · oklch(0.205 0 0) | `--card` |

| Colour | RGB | CMYK (approx.) |
|---|---|---|
| Violet `#6F49B1` | 111 73 177 | 37 59 0 31 |
| Lavender `#D6C9F9` | 214 201 249 | 14 19 0 2 |
| Ink `#0A0A0A` | 10 10 10 | 0 0 0 96 |

CMYK values are naive conversions — proof before print; match a Pantone spot on a physical bridge guide if needed.

Contrast: ink on white 19.8:1 · paper on black 19.0:1 · white on violet 6.4:1 · lavender on `#0A0A0A` 12.8:1.

**Approved pairs**: `pokeum-*.svg` on white · `pokeum-*-dark.svg` on `#0A0A0A`/`#171717` · white symbol on violet tile (app icon) · lavender symbol on `#171717` tile (dark app icon) · black or white one-colour.
**Never** use the light violet symbol on dark backgrounds (3:1, dull) — switch to the `-dark` file.

## 3. Clear space
Keep **1 stem width** clear on all sides (stem = 23 % of the symbol height). It scales with the logo.

## 4. Minimum size
| Version | Screen | Print |
|---|---|---|
| Horizontal | 120 px wide | 30 mm wide |
| Stacked | 64 px wide | 18 mm wide |
| Symbol | 24 px (≤ 24 px: `-small` files / `web/favicon.*`) | 6 mm |

## 5. In the app (mtgfmax / Nuxt)
- Copy `web/*` to `public/` and use `web/head-snippet.html` in `<head>` (adds light/dark `theme-color`).
- `web/favicon.svg` switches violet → lavender with `prefers-color-scheme: dark`.
- Header: swap `svg/pokeum-horizontal.svg` ↔ `svg/pokeum-horizontal-dark.svg` on the `.dark` class.

## 6. Don'ts
Don't stretch, rotate or recolour outside the table above · no shadows, outlines or gradients · don't move the pupil or change its size · don't retype the wordmark · don't use Pokémon trade dress (red/white split circle, Pikachu yellow) next to the mark.

## 7. Files
| Folder | Contents |
|---|---|
| `svg/` | Masters: symbol, small, horizontal, stacked, wordmark × light/dark/violet/black/white |
| `png/` | Transparent PNG renders of the masters |
| `web/` | favicon.ico/svg, 16/32/48 px, apple-touch, 192/512, maskable, webmanifest, head snippet; `web/dark/` dark app icon |
| `slides/`, `presentation.html` | Presentation board with mockups |
| `preview.html` | Test sheet (sizes, 16 px, one-colour, reversed, shelf) |

Trademark clearance not done — run a professional search before launch.
