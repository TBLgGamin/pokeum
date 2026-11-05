---
title: Scan frontend (Vue PWA)
sources: ["frontend/**", "docs/brand/**"]
read-when: "changing the Vue scan UI: the scan and collection screens, the match sheet, auto-capture, the theme tokens or shadcn-vue components, the on-device collection and CSV export, the PWA/service-worker setup, the brand/icon assets, or the frontend build/test tooling"
verified: 388a26a04ef7
---

# Scan frontend (Vue PWA)

The browser client: point a phone at a card, get the exact printing, keep the
collection on the device. It talks to the same endpoints the CLI drives (see
[service-and-cli](service-and-cli.md)) and shows nothing the recognizer does
not return.

A Vue 3 + TypeScript app on Tailwind v4 and shadcn-vue. **Two screens share one
header**: [`AppHeader.vue`](../frontend/src/components/AppHeader.vue) holds the
Lens P lockup and two tab links, `#scan` and `#collection` (the second shows the
card count), and [`App.vue`](../frontend/src/App.vue) swaps the view under it —
the scanner is a fixed-height viewfinder, the collection an ordinary scrolling
page. The screen lives in the URL hash ([`useView.ts`](../frontend/src/composables/useView.ts)),
so the platform Back button steps from the collection to the scanner instead of
closing an installed app.

```
src/
  App.vue                      shell: header + one view; owns the shared collection
  components/
    AppHeader.vue  BrandLogo.vue  CardArt.vue
    scan/        ScanView.vue  ScanFrame.vue  ShutterButton.vue  MatchSheet.vue
    collection/  CollectionView.vue
    ui/          shadcn-vue output: button switch label sheet alert-dialog item empty separator
  composables/   useScanSession  useCamera  useAutoCapture  useCollection  useView
  lib/           api quality image capture collection exporters copy preferences haptics
```

## Scan screen

[`ScanView.vue`](../frontend/src/components/scan/ScanView.vue) is the viewfinder
(the camera preview, a size container) above one control strip. **One status
line** (`role=status`) is the single place the scanner talks; in priority order
it shows *Recognizing…*, *Starting camera…*, the auto-capture coaching hint
("Hold still", "More light", "Reduce glare"), *Saved <name>* (4 s), the last
failure in plain words ([`copy.ts`](../frontend/src/lib/copy.ts): no card, no
match, recognizer busy on `503`, server unreachable), then the idle prompt. The
strip is Upload photo · shutter · Auto-scan switch. The shutter is a
lens-and-pupil ring in neutral tokens — the old red/white pokéball is gone on
purpose: the brand guidelines forbid Pokémon trade dress next to the mark.
[`ScanFrame.vue`](../frontend/src/components/scan/ScanFrame.vue) is the guide, a
light outline sized from the viewfinder with container units (72% of its width,
never taller than 78% of its height, card aspect 63:88) that sweeps a line while
a request is in flight (a static line under reduced motion). With the camera
denied or absent the viewfinder shows an empty state with *Try again*, and Upload
becomes the full-width primary action.

**Capture → identify is one seam.**
[`useScanSession.ts`](../frontend/src/composables/useScanSession.ts) owns it:
`scanCamera()` (guide crop via [`capture.ts`](../frontend/src/lib/capture.ts))
and `scanFile()` (library photo, downscaled) both end in `recognize()`, the only
caller of `identify()` and `identifyBurst()`. A camera scan whose first frame is
not `confident` captures `BURST_EXTRA_FRAMES` (2) more guide crops
`BURST_FRAME_GAP_MS` (80 ms) apart and re-asks `POST /api/identify/burst` with
all three, which merges them server-side; uploads never burst. It exposes `scanning`, `pending` (the verdict plus its
non-null match) and `failure` (`miss` for a recognizer "no", `error` for a failed
request); `accept()` returns the card to save, `dismiss()` drops the verdict.
`confident`, `uncertain` and `ambiguous` verdicts with a match are confirmable;
anything else is a failure. Camera lifecycle is
[`useCamera.ts`](../frontend/src/composables/useCamera.ts) (a retry, or leaving
the view during the permission prompt, cannot attach a stale stream).

**Match sheet.** [`MatchSheet.vue`](../frontend/src/components/scan/MatchSheet.vue)
is a shadcn `Sheet`: bottom sheet on phones, right-hand panel from 768 px. A
verdict shows the card's art, name, set, `code · number` and any variant traits,
with *Save card* (focused, so Enter confirms) and *Not this one*; `uncertain` adds
a *Low confidence* line. An `ambiguous` verdict settles the artwork but not the
printing (see [recognition-pipeline](recognition-pipeline.md)), so it opens
straight into the printing chooser — `[match, ...alternates]`, at most five rows
— and *Not this one* opens the same list. Picking a row saves that printing. The
close button, Esc and a tap outside dismiss without saving. No numeric confidence
is shown, by design: the fused score is not a calibrated probability, so only the
verdicts that change the decision (`uncertain`, `ambiguous`) surface, in words.

**Auto-capture:** the scanner fires by itself once the frame is worth sending,
so a stack can be worked through without tapping. The metrics are pure and
unit-tested in [`quality.ts`](../frontend/src/lib/quality.ts) (Rec. 601 luma,
Laplacian-variance sharpness, mean absolute frame difference for motion, mean
brightness; thresholds are tuned for the 160px downscaled sample); the sampling
loop and arm/disarm machine are in
[`useAutoCapture.ts`](../frontend/src/composables/useAutoCapture.ts). It samples
*the guide crop only* — the pixels that get uploaded — every 150 ms into one
shared offscreen canvas, reports the failing check through the status line, and
captures after `STABLE_SAMPLES_REQUIRED` good samples (~half a second of
stillness). Any result, camera or uploaded photo, disarms it; only
`CLEARED_SAMPLES_REQUIRED` moving/blurry samples re-arm it (otherwise a card left
in the guide would be re-scanned forever) and re-arming clears a stale failure.
The Auto-scan switch persists in `localStorage` as `pokeum.autoScan`
([`preferences.ts`](../frontend/src/lib/preferences.ts)).

Camera captures are **cropped to the guide frame**: `captureGuideCrop` maps the
guide's on-screen rect through the video's `object-cover` geometry to source
pixels (`guideCropSourceRect` in [`image.ts`](../frontend/src/lib/image.ts), pure
and testable), keeping a `GUIDE_CROP_MARGIN` of background so quad detection can
close the contour. The camera sends that margin as `guide_margin`: the server
uses a detected quad when there is one, otherwise strips the known margin and
recognizes the inner guide, accepting that fallback only when confident; an
uncertain result asks for a steadier angle, an `ambiguous` one is left alone (the
artwork *was* matched). Photo uploads keep the server's whole-frame fallback and
are downscaled client-side first (max side 1600px).

## Collection

**The collection persists on-device**: `entries` round-trips through
versioned-JSON localStorage ([`collection.ts`](../frontend/src/lib/collection.ts);
blocked storage never breaks scanning), restored when
[`useCollection.ts`](../frontend/src/composables/useCollection.ts) is created in
`App.vue` and provided to the header (count), scanner (add) and collection view.
Saves are debounced 400 ms and flushed on `visibilitychange: hidden` (mobile
PWAs die without unload events); the first non-empty save requests
`navigator.storage.persist()`. Persisted cards carry `image_url`, so a restored
collection renders art from the service-worker cache even offline.

[`CollectionView.vue`](../frontend/src/components/collection/CollectionView.vue)
is a list, newest scan first: art, name, `set · code · number` and a quantity
stepper (− removes the last copy, so a wrong save is fixable without clearing
everything). *Export CSV* downloads a TCGplayer-style file
([`exporters.ts`](../frontend/src/lib/exporters.ts)); *Clear collection* is a
quiet destructive link at the end that opens a confirmation naming the count. An
empty collection shows the mark, "No cards yet" and one *Scan a card* button.

## Design system

**Content sits on the page.** No cards, panels or shadows for grouping — spacing,
type and alignment do it. Only overlays that must float (the sheet, the confirm
dialog) get the raised `popover` surface plus a hairline border, never a shadow.
Icons are `@lucide/vue`; type is Space Grotesk Variable
(`@fontsource-variable/space-grotesk`, as in mtgfmax).

**Theme tokens are mtgfmax's** (`app/assets/css/tailwind.css` there): neutral
OKLCH greys plus one violet, the `--ring` hue 297.347, as CSS variables in
[`src/index.css`](../frontend/src/index.css), mapped to Tailwind colours with
`@theme inline` (Tailwind v4 via `@tailwindcss/vite`; no `tailwind.config`). Dark
mode follows the OS — `dark:` is Tailwind's default `prefers-color-scheme` variant
and the dark tokens override `:root` — so there is no toggle, stored preference or
flash. Components use semantic tokens only (`bg-primary` is near-black in light,
near-white in dark); the violet appears as the logo, focus rings and the scan
sweep, and red only for destructive actions and failed requests. Two project
tokens, `--guide` and `--guide-accent`, colour the guide drawn over the camera
feed and deliberately do not change with the theme.

**shadcn-vue conventions.** [`components.json`](../frontend/components.json)
(style `new-york`, `cn` from the `cn` package, Reka UI) means new components come
from `npx shadcn-vue@latest add <name>` into `src/components/ui/`. Those files are
vendored and **locally customised** — no shadows, 40/48 px button heights and a
press scale ([`button/index.ts`](../frontend/src/components/ui/button/index.ts)),
a 44×24 switch, the popover surface and half-strength scrim on the sheet and
alert dialog — so `add --overwrite` reintroduces the defaults; each customised
file opens with a "Customised from…" comment listing what to re-apply.

## Installable PWA

`vite-plugin-pwa` (config in [`vite.config.ts`](../frontend/vite.config.ts))
generates the manifest and a Workbox service worker; `main.ts` registers it
(`autoUpdate` — new deploys activate on next launch). The app shell is
precached; card art is runtime `CacheFirst` (the TCGdex CDN plus the API
thumbnail fallback, capped at 1500/500 entries), `/api/*` is never answered by
the navigation fallback, and POSTs are never cached. Installing requires HTTPS
(as does the camera). The manifest is "Pokeum" with `theme_color` and
`background_color` `#FFFFFF` (the brand set's values; a manifest holds one
colour); `index.html` adds media-scoped `theme-color` metas, `#FFFFFF` light and
`#0A0A0A` dark, so the status bar follows the OS.

## Brand and icon assets

The Lens P set (a P whose counter is a camera lens with a pupil) is mirrored in
[`docs/brand/`](../docs/brand/README.md): `svg/` masters, `web/` ready-made
exports, `guidelines.md`, a small generated PNG kit. **Everything shipped is
copied or generated from there, never hand-edited**, by one script, `npm run
generate-brand-assets`
([`scripts/generate-brand-assets.mjs`](../frontend/scripts/generate-brand-assets.mjs),
`sharp` only, idempotent): it copies the four in-app SVGs to `src/assets/brand/`
and the set's exports to `frontend/public/`, renders the sizes the set lacks and
the link previews, then reads every output back and fails on a wrong size,
format, transparency or maskable safe zone.

| In `frontend/public/` | Size | Used by |
|---|---|---|
| `favicon.ico` | 16/32/48 | legacy tabs |
| `favicon.svg` | vector, violet → lavender in dark | modern tabs |
| `favicon-16x16.png`, `favicon-32x32.png` | 16, 32 | crisp tab renditions |
| `apple-touch-icon-{152x152,167x167,180x180}.png` | opaque full-bleed | iPad / iPhone home screen |
| `pwa-{64x64,192x192,512x512}.png` | white symbol on violet | manifest icons (`any`) |
| `maskable-icon-512x512.png` | symbol inside the central 40% radius | Android adaptive icon |
| `logo.svg` | vector | app-icon artwork |
| `og-image.png`, `og-image-square.png` | 1200×630, 1024×1024 | link previews (stacked lockup on white) |

[`BrandLogo.vue`](../frontend/src/components/BrandLogo.vue) renders the
horizontal lockup (header, ≥120 px wide) or the symbol (empty state) in a
`<picture>`, swapping to the `-dark` files under `prefers-color-scheme: dark`
(the violet mark is never reversed onto dark). The SVGs are imported with
`?no-inline`: Vite would otherwise inline the tiny symbols as data URIs whose
commas break `srcset`. Which files ship is spelled out twice — `includeAssets`
and the manifest `icons` in `vite.config.ts`, and the icon / `og:` tags in
[`index.html`](../frontend/index.html) — so a new size or format means editing
both lists, not just dropping a file in `public/`.

## Dev and checks

`npm run dev` in `frontend/` (port 5173) proxies `/api/*` to the local service,
so start the API first; `allowedHosts` covers cloudflared/ngrok tunnels, and
`VITE_API_BASE` points the client at a cross-origin API when the proxy isn't in
play. Checks: `npm test` (vitest: the pure frame-quality, copy and preference
helpers, the `useAutoCapture`, `useScanSession` and `useCollection` composables,
and a jsdom smoke test that mounts `App.vue` through upload → verdict → save →
collection) and `npm run build` (vue-tsc + vite) — neither is part of the Python
gate; see [workflow](workflow.md). CI runs `npm ci`, so keep `package-lock.json`
in step with `package.json`.

## Changing this area

- Keep measurement logic pure: anything expressible as pixels-in/numbers-out
  belongs in `src/lib` with a `*.test.ts` beside it, not in a component.
- Anything that sends an image to the recognizer goes through
  `useScanSession.recognize`; never call `identify()` from a component.
- The client mirrors the backend `IdentifyResponse` by hand
  ([`api.ts`](../frontend/src/lib/api.ts)): the `status` union plus the optional
  `ocr.confidence` and title-OCR `name`. A new status or field must be added
  there, and a status that carries a match must also be made confirmable in
  `recognize`, or the type-check passes while the UI treats it as a miss.
- Style with the semantic tokens; a raw colour in a component is a bug unless it
  is drawn over the camera feed. Keep new surfaces on the page and give every
  state (empty, busy, error) a design.
- The built app is served by the API itself when `FRONTEND_DIST_DIR` exists, so
  relative `/api` paths must keep working from the root origin.
