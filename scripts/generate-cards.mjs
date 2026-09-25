#!/usr/bin/env node
/**
 * Phase 7 — generate 52 card faces from the Phase 4 vector output.
 *
 * Inputs:
 *   assets/games/teen-patti-pro/cards/card-face-template.svg  (blank face)
 *   assets/games/teen-patti-pro/cards/suit-{spade,heart,diamond,club}.svg
 *
 * The template is a traced blank face with corner rank boxes and an empty
 * centre, so each card is the template plus:
 *   - rank text top-left, suit glyph beneath it
 *   - the same pair rotated 180 degrees at bottom-right
 *   - a large suit glyph at 40% scale in the centre
 *
 * Suit paths are inlined from the Phase 4 SVGs and re-scaled into a 24x24
 * user-unit box, so a card file stays self-contained with no external refs and
 * no embedded raster.
 *
 * Usage: node scripts/generate-cards.mjs --out <dir> [--face <svg>] [--suits <dir>]
 */
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];
const SUITS = ["spade", "heart", "diamond", "club"];
const RED = new Set(["heart", "diamond"]);

const CARD_W = 500;
const CARD_H = 700;
const GLYPH = 74;        // corner glyph box
const CENTRE = 200;      // centre suit box

function parseArgs(argv) {
  const a = { out: "assets/games/teen-patti-pro/cards", face: null, suits: null };
  for (let i = 2; i < argv.length; i += 2) {
    const k = argv[i].replace(/^--/, "");
    const v = argv[i + 1];
    if (k in a) a[k] = v;
  }
  return a;
}

/** Pull the path data out of a Phase 4 SVG, normalised to a 0..1 box. */
function loadGlyph(svgPath) {
  const src = fs.readFileSync(svgPath, "utf8");
  const vb = src.match(/viewBox="([^"]+)"/);
  const [, , vw, vh] = vb ? vb[1].split(/\s+/).map(Number) : [0, 0, 1, 1];
  const d = [...src.matchAll(/<path[^>]*\sd="([^"]+)"/g)].map((m) => m[1]);
  if (!d.length) throw new Error(`no <path> in ${svgPath}`);
  // strip potrace's own unit transform; we re-scale into our own box
  const scale = vw ? 1 / (vw * 10) : 1;
  const paths = d
    .join(" ")
    .replace(/-?\d+(\.\d+)?/g, (n) => (Number(n) * scale).toFixed(3));
  return { d: paths, aspect: vw && vh ? vw / vh : 1 };
}

function glyphTransform(g, x, y, size) {
  // fit the glyph into a size x size box, centred
  const s = size / Math.max(g.aspect, 1);
  const w = g.aspect * s;
  return `translate(${x + (size - w) / 2} ${y + (size - s) / 2}) scale(${s})`;
}

function build({ rank, suit, glyph, colour }) {
  const parts = [];
  // The suit is referenced three times (corner, mirrored corner, centre), so
  // it is defined once in <defs> and drawn with <use>. Inlining it three times
  // is what pushed the first pass to 114 KB per card; this is ~8-14 KB.
  parts.push(
    `<defs><path id="s" d="${glyph.d}"/></defs>`,
    `<g id="face">${faceBody}</g>`,
  );
  const use = (x, y, size, extra = "") =>
    `<g transform="${glyphTransform(glyph, x, y, size)}" fill="${colour}"${extra}>` +
    `<use href="#s"/></g>`;
  // top-left rank + suit
  parts.push(
    `<text x="34" y="76" font-family="Georgia,'Times New Roman',serif" ` +
    `font-size="64" font-weight="700" fill="${colour}">${rank}</text>`,
    use(36, 86, GLYPH),
  );
  // bottom-right, rotated 180
  parts.push(
    `<g transform="rotate(180 ${CARD_W / 2} ${CARD_H / 2})">` +
    `<text x="34" y="76" font-family="Georgia,'Times New Roman',serif" ` +
    `font-size="64" font-weight="700" fill="${colour}">${rank}</text>` +
    use(36, 86, GLYPH) +
    `</g>`,
  );
  // centre suit
  const cy = (CARD_H - CENTRE * glyph.aspect) / 2 - 10;
  parts.push(use((CARD_W - CENTRE) / 2, cy, CENTRE, ' opacity="0.92"'));
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CARD_W} ${CARD_H}" ` +
    `width="${CARD_W}" height="${CARD_H}" role="img" ` +
    `aria-label="${rank} of ${suit}">\n${parts.join("\n")}\n</svg>\n`
  );
}

let faceBody = "";

const args = parseArgs(process.argv);
const facePath = args.face || path.join(args.out, "card-face-template.svg");
const suitDir = args.suits || args.out;

faceBody = fs
  .readFileSync(facePath, "utf8")
  .replace(/^[\s\S]*?<svg[^>]*>/, "")
  .replace(/<\/svg>\s*$/, "")
  .trim();

fs.mkdirSync(args.out, { recursive: true });

let written = 0;
const failures = [];
for (const suit of SUITS) {
  let glyph;
  try {
    glyph = loadGlyph(path.join(suitDir, `suit-${suit}.svg`));
  } catch (e) {
    failures.push(`suit-${suit}: ${e.message}`);
    continue;
  }
  const colour = RED.has(suit) ? "#c8102e" : "#1a1a1f";
  for (const rank of RANKS) {
    const name = `card-${rank}-${suit}.svg`;
    fs.writeFileSync(path.join(args.out, name), build({ rank, suit, glyph, colour }));
    written += 1;
  }
}

console.log(`[CARDS] ${written} card faces -> ${args.out}`);
if (failures.length) {
  for (const f of failures) console.error(`  FAIL ${f}`);
  process.exit(1);
}
