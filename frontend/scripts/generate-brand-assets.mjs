/** Build every brand artefact the app ships from the logo set in `docs/brand/`.
 *
 * `docs/brand/` is the source of truth (SVG masters plus the set's ready-made
 * web exports); nothing in `frontend/public/`, `frontend/src/assets/brand/` or
 * `docs/brand/png/` is edited by hand. One idempotent run does everything:
 *
 *   1. copies the four in-app SVG masters to `src/assets/brand/`,
 *   2. copies the set's ready-made web exports into `public/` under the names
 *      the manifest and `index.html` use (the set's art is never re-drawn),
 *   3. renders only what the set lacks (152/167px apple icons, 64px PWA icon,
 *      the og images, the docs PNG kit) from its SVGs,
 *   4. reads every output back and throws if a size, format or opacity
 *      requirement is violated, then prints the asset table.
 *
 * Run with `npm run generate-brand-assets` from `frontend/`.
 */

import { copyFile, mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../..");
const brandDir = resolve(repoRoot, "docs/brand");
const publicDir = resolve(here, "../public");
const appBrandDir = resolve(here, "../src/assets/brand");
const kitDir = resolve(brandDir, "png");

/** Palette: mtgfmax theme tokens (docs/brand/guidelines.md §2). */
const VIOLET = { r: 0x6f, g: 0x49, b: 0xb1 };
const WHITE = { r: 255, g: 255, b: 255, alpha: 1 };

/** Android clips a maskable icon to a circle of at most this fraction of its side. */
const MASKABLE_SAFE_RADIUS = 0.4;

const rel = (path) => relative(repoRoot, path).replaceAll("\\", "/");
const svgDir = (name) => resolve(brandDir, "svg", name);
const webDir = (name) => resolve(brandDir, "web", name);

/** @type {{path: string, width: number|null, height: number|null, format: string, opaque?: boolean, copyOf?: string}[]} */
const expected = [];

/**
 * Register an output for the final self-check.
 *
 * @param {string} path absolute output path
 * @param {number|null} width required pixel width, null for vector files
 * @param {number|null} height required pixel height, null for vector files
 * @param {string} format sharp format name the file must decode as
 * @param {{opaque?: boolean, copyOf?: string}} [extra] opaque: no pixel may carry
 *   alpha < 255; copyOf: path whose bytes this file must equal
 */
function track(path, width, height, format, extra = {}) {
  expected.push({ path, width, height, format, ...extra });
}

/** Copy a file verbatim (creating the target folder) and register it for the self-check. */
async function copy(source, target, [width, height, format, extra = {}]) {
  await mkdir(dirname(target), { recursive: true });
  await copyFile(source, target);
  track(target, width, height, format, { copyOf: source, ...extra });
}

/**
 * Rasterize an SVG so one user unit maps to a whole pixel multiple of the
 * target, then resize to the exact width. Rendering at the target density
 * keeps edges sharp; a plain resize of the 72 dpi default would blur.
 *
 * @param {string|Buffer} svg path of an SVG file, or its markup
 * @param {number} width output width in pixels (height follows the aspect ratio)
 * @returns {Promise<sharp.Sharp>} pipeline positioned at the exact size, alpha kept
 */
async function rasterize(svg, width) {
  const markup = Buffer.isBuffer(svg) ? svg : await readFile(svg);
  const viewBox = /viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"/.exec(markup.toString());
  if (!viewBox) throw new Error(`SVG without a viewBox: ${Buffer.isBuffer(svg) ? "<buffer>" : svg}`);
  const density = (72 * width) / Number(viewBox[1]);
  return sharp(markup, { density }).resize({ width });
}

/** Write a PNG; `background` flattens onto an opaque backdrop, null keeps alpha. */
async function writePng(pipeline, target, background = null) {
  await mkdir(dirname(target), { recursive: true });
  const out = background ? pipeline.flatten({ background }) : pipeline;
  await out.png({ compressionLevel: 9 }).toFile(target);
}

/**
 * Describe an icon that should be full-bleed violet with a white symbol.
 *
 * @param {Buffer} png encoded image
 * @returns {Promise<{width:number,height:number,opaque:boolean,cornersViolet:boolean,
 *   maxRadius:number,bbox:number[]}>} `maxRadius` is the distance from the centre to the
 *   farthest pixel that is not background violet; `bbox` is [x0, y0, x1, y1] of those pixels
 */
async function inspectVioletIcon(png) {
  const { data, info } = await sharp(png).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  const { width, height } = info;
  const cx = width / 2;
  const cy = height / 2;
  let opaque = true;
  let maxRadius = 0;
  let [x0, y0, x1, y1] = [width, height, -1, -1];
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      if (data[i + 3] !== 255) opaque = false;
      const delta =
        Math.abs(data[i] - VIOLET.r) + Math.abs(data[i + 1] - VIOLET.g) + Math.abs(data[i + 2] - VIOLET.b);
      if (delta > 8) {
        maxRadius = Math.max(maxRadius, Math.hypot(x + 0.5 - cx, y + 0.5 - cy));
        [x0, y0, x1, y1] = [Math.min(x0, x), Math.min(y0, y), Math.max(x1, x), Math.max(y1, y)];
      }
    }
  }
  const corner = (x, y) => {
    const i = (y * width + x) * 4;
    return data[i] === VIOLET.r && data[i + 1] === VIOLET.g && data[i + 2] === VIOLET.b && data[i + 3] === 255;
  };
  const cornersViolet =
    corner(0, 0) && corner(width - 1, 0) && corner(0, height - 1) && corner(width - 1, height - 1);
  return { width, height, opaque, cornersViolet, maxRadius, bbox: [x0, y0, x1, y1] };
}

/** Encode PNG frames into a PNG-in-ICO container (Vista+ and every browser). */
function encodeIco(frames) {
  const header = Buffer.alloc(6 + 16 * frames.length);
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(frames.length, 4);
  let offset = header.length;
  frames.forEach(({ size, png }, index) => {
    const entry = 6 + 16 * index;
    header[entry] = size; // 0 would mean 256; every frame here is smaller
    header[entry + 1] = size;
    header.writeUInt16LE(1, entry + 4); // colour planes
    header.writeUInt16LE(32, entry + 6); // bits per pixel
    header.writeUInt32LE(png.length, entry + 8);
    header.writeUInt32LE(offset, entry + 12);
    offset += png.length;
  });
  return Buffer.concat([header, ...frames.map(({ png }) => png)]);
}

/** Sizes of the frames inside an ICO file, in directory order. */
function icoSizes(ico) {
  const count = ico.readUInt16LE(4);
  return Array.from({ length: count }, (_, i) => ico[6 + 16 * i] || 256);
}

// ---------------------------------------------------------------------------
// 1. In-app SVG masters (imported by BrandLogo.vue with `?no-inline`; Vite hashes them).
// ---------------------------------------------------------------------------
for (const name of ["pokeum-horizontal", "pokeum-horizontal-dark", "pokeum-symbol", "pokeum-symbol-dark"]) {
  await copy(svgDir(`${name}.svg`), resolve(appBrandDir, `${name}.svg`), [null, null, "svg"]);
}

// ---------------------------------------------------------------------------
// 2. Ready-made web exports from the set.
// ---------------------------------------------------------------------------
await copy(webDir("favicon.svg"), resolve(publicDir, "favicon.svg"), [null, null, "svg"]);
await copy(webDir("pokeum-symbol-app-icon.svg"), resolve(publicDir, "logo.svg"), [null, null, "svg"]);
await copy(webDir("favicon-16.png"), resolve(publicDir, "favicon-16x16.png"), [16, 16, "png"]);
await copy(webDir("favicon-32.png"), resolve(publicDir, "favicon-32x32.png"), [32, 32, "png"]);
await copy(webDir("icon-192.png"), resolve(publicDir, "pwa-192x192.png"), [192, 192, "png", { opaque: true }]);
await copy(webDir("icon-512.png"), resolve(publicDir, "pwa-512x512.png"), [512, 512, "png", { opaque: true }]);

// The .ico must carry 16/32/48 frames so every tab/bookmark size is a crisp
// native rendition; if a future set ships fewer, rebuild it from the favicon art.
const icoTarget = resolve(publicDir, "favicon.ico");
const setIco = await readFile(webDir("favicon.ico"));
if (icoSizes(setIco).join() === "16,32,48") {
  await copyFile(webDir("favicon.ico"), icoTarget);
} else {
  const frames = await Promise.all(
    [16, 32, 48].map(async (size) => ({
      size,
      png: await (await rasterize(webDir("pokeum-symbol-favicon.svg"), size)).png({ compressionLevel: 9 }).toBuffer(),
    })),
  );
  await writeFile(icoTarget, encodeIco(frames));
}
track(icoTarget, null, null, "ico");

// ---------------------------------------------------------------------------
// 3. Sizes the set lacks, rendered from its app-icon SVG.
// ---------------------------------------------------------------------------
// The set's `icon-*.png` and apple icon are full-bleed squares (iOS and Android
// apply their own mask), while the app-icon SVG has rounded corners. Dropping
// `rx` gives the same full-bleed artwork for the sizes the set does not ship.
const appIconSvg = await readFile(webDir("pokeum-symbol-app-icon.svg"), "utf8");
const fullBleedSvg = appIconSvg.replace(/(<rect [^>]*?) rx="[\d.]+"/, "$1");
if (fullBleedSvg === appIconSvg) throw new Error("app-icon SVG has no rounded <rect>; update the full-bleed derivation");
const renderFullBleed = async (size) => (await rasterize(Buffer.from(fullBleedSvg), size)).png({ compressionLevel: 9 });

await (await renderFullBleed(64)).toFile(resolve(publicDir, "pwa-64x64.png"));
track(resolve(publicDir, "pwa-64x64.png"), 64, 64, "png", { opaque: true });

// iOS composites transparency onto black and applies its own corner mask, so
// the apple icons must be opaque full-bleed. Use the set's 180px export when it
// qualifies; otherwise regenerate all three sizes.
const appleTarget = (size) => resolve(publicDir, `apple-touch-icon-${size}x${size}.png`);
const setApple = await readFile(webDir("apple-touch-icon.png"));
const appleProbe = await inspectVioletIcon(setApple);
const appleOk =
  appleProbe.width === 180 && appleProbe.height === 180 && appleProbe.opaque && appleProbe.cornersViolet;
if (appleOk) {
  await copyFile(webDir("apple-touch-icon.png"), appleTarget(180));
} else {
  await (await renderFullBleed(180)).toFile(appleTarget(180));
}
for (const size of [152, 167]) await (await renderFullBleed(size)).toFile(appleTarget(size));
for (const size of [152, 167, 180]) track(appleTarget(size), size, size, "png", { opaque: true });

// Maskable: full-bleed violet, symbol wholly inside the central circle that
// every Android mask shape keeps. Verified by pixel; regenerated if it fails.
const maskableTarget = resolve(publicDir, "maskable-icon-512x512.png");
const setMaskable = await readFile(webDir("maskable-512.png"));
const maskableOk = (probe) =>
  probe.width === 512 &&
  probe.height === 512 &&
  probe.opaque &&
  probe.cornersViolet &&
  probe.maxRadius <= MASKABLE_SAFE_RADIUS * 512;
if (maskableOk(await inspectVioletIcon(setMaskable))) {
  await copyFile(webDir("maskable-512.png"), maskableTarget);
} else {
  // Shrink the app-icon geometry about the centre until it fits the safe circle.
  const maskableSvg = fullBleedSvg
    .replace(/(<rect [^>]*\/>)/, '$1<g transform="translate(128 128) scale(0.8) translate(-128 -128)">')
    .replace("</svg>", "</g></svg>");
  await (await rasterize(Buffer.from(maskableSvg), 512)).png({ compressionLevel: 9 }).toFile(maskableTarget);
  const redo = await inspectVioletIcon(await readFile(maskableTarget));
  if (!maskableOk(redo)) throw new Error(`regenerated maskable icon still unsafe: ${JSON.stringify(redo)}`);
}
track(maskableTarget, 512, 512, "png", { opaque: true });

// ---------------------------------------------------------------------------
// 4. Link previews: stacked lockup centred on white (the approved light pair).
// No tagline: the renderer has no fonts, and the wordmark is outlined art.
// ---------------------------------------------------------------------------
/**
 * @param {string} target output path
 * @param {number} width canvas width
 * @param {number} height canvas height
 * @param {number} lockupWidth lockup width; its height follows the 3639x2661 viewBox
 */
async function renderPreview(target, width, height, lockupWidth) {
  const lockup = await (await rasterize(svgDir("pokeum-stacked.svg"), lockupWidth)).png().toBuffer();
  await writePng(
    sharp({ create: { width, height, channels: 4, background: WHITE } }).composite([{ input: lockup, gravity: "centre" }]),
    target,
    WHITE,
  );
  track(target, width, height, "png", { opaque: true });
}
// Lockup ≈ 60% of the shorter canvas side: far more than the 1-stem clear space,
// and the 1200x630 card survives a centre crop to 1:1 or 4:3 unclipped.
await renderPreview(resolve(publicDir, "og-image.png"), 1200, 630, 517);
await renderPreview(resolve(publicDir, "og-image-square.png"), 1024, 1024, 614);

// ---------------------------------------------------------------------------
// 5. Docs PNG kit (transparent; small on purpose, the SVGs stay the masters).
// ---------------------------------------------------------------------------
const kit = [
  ...["pokeum-symbol", "pokeum-symbol-dark", "pokeum-symbol-white", "pokeum-symbol-black"].map((n) => [n, 512]),
  ...["pokeum-horizontal", "pokeum-horizontal-dark"].map((n) => [n, 1200]),
  ...["pokeum-stacked", "pokeum-stacked-dark"].map((n) => [n, 800]),
];
await mkdir(kitDir, { recursive: true });
for (const stale of await readdir(kitDir)) {
  if (!kit.some(([name]) => `${name}.png` === stale)) await rm(resolve(kitDir, stale));
}
for (const [name, width] of kit) {
  const target = resolve(kitDir, `${name}.png`);
  const pipeline = await rasterize(svgDir(`${name}.svg`), width);
  await writePng(pipeline, target);
  const { height } = await sharp(target).metadata();
  track(target, width, height, "png");
}

// ---------------------------------------------------------------------------
// 6. Self-check: read every output back; throw on any violation.
// ---------------------------------------------------------------------------
const rows = [];
const problems = [];
for (const item of expected) {
  const bytes = await readFile(item.path);
  let dims = "vector";
  if (item.format === "ico") {
    const sizes = icoSizes(bytes);
    dims = sizes.map((s) => `${s}x${s}`).join("+");
    if (sizes.join() !== "16,32,48") problems.push(`${rel(item.path)}: ICO frames ${sizes} != 16,32,48`);
    for (const [index, size] of sizes.entries()) {
      const entry = 6 + 16 * index;
      const start = bytes.readUInt32LE(entry + 12);
      const frame = await sharp(bytes.subarray(start, start + bytes.readUInt32LE(entry + 8))).metadata();
      if (frame.format !== "png" || frame.width !== size || frame.height !== size) {
        problems.push(`${rel(item.path)}: ICO frame ${size} is ${frame.format} ${frame.width}x${frame.height}`);
      }
    }
  } else if (item.format === "svg") {
    if (!bytes.toString("utf8").trimStart().startsWith("<svg")) problems.push(`${rel(item.path)}: not an SVG`);
  } else {
    const meta = await sharp(bytes).metadata();
    dims = `${meta.width}x${meta.height}`;
    if (meta.format !== item.format) problems.push(`${rel(item.path)}: format ${meta.format} != ${item.format}`);
    if (meta.width !== item.width || meta.height !== item.height) {
      problems.push(`${rel(item.path)}: ${dims} != ${item.width}x${item.height}`);
    }
    if (item.opaque) {
      const { data } = await sharp(bytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
      for (let i = 3; i < data.length; i += 4) {
        if (data[i] !== 255) {
          problems.push(`${rel(item.path)}: has transparent pixels`);
          break;
        }
      }
    }
  }
  if (item.copyOf && !bytes.equals(await readFile(item.copyOf))) {
    problems.push(`${rel(item.path)}: differs from ${rel(item.copyOf)}`);
  }
  rows.push([rel(item.path), dims, String(bytes.length)]);
}
if (problems.length) throw new Error(`brand asset self-check failed:\n  ${problems.join("\n  ")}`);

const widths = [0, 1, 2].map((col) => Math.max(...rows.map((row) => row[col].length)));
for (const [path, dims, bytes] of rows) {
  console.log(`${path.padEnd(widths[0])}  ${dims.padEnd(widths[1])}  ${bytes.padStart(widths[2])}`);
}
const kitBytes = rows.filter(([path]) => path.startsWith("docs/brand/png/")).reduce((sum, row) => sum + Number(row[2]), 0);
console.log(`\n${rows.length} files ok; docs PNG kit ${kitBytes} bytes`);
