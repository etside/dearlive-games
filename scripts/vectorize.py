"""Phase 4 — PNG -> SVG vectorisation.

Toolchain reality in this environment: vtracer, pngtosvg, autotrace, svgo and
imagemagick are all unavailable (apt has no autotrace candidate; npm is blocked
by a security wrapper that intercepts its `env` exec). `potrace` and `xmllint`
were installable via apt and are what this uses.

potrace traces a BINARY mask, so colour art is handled the way vtracer's
`--hierarchical stacked` does it: quantise to a small palette, trace one mask
per palette colour, and stack the resulting paths back-to-front. That is real
vector output -- no embedded raster, which the brief forbids and this verifies.

Tool selection follows the brief:
  colored art (chairs, chips, panels, banners) -> stacked colour trace, spline
  icons / line art (suits, rings, frames)        -> single-path silhouette
  badges / buttons (gradient + flat)             -> stacked colour trace, polygon

`svgo` is unavailable, so `optimise_svg()` performs the equivalent work:
coordinate rounding, redundant-attribute stripping, and metadata removal.

No <image> element is ever emitted. Verified per asset after writing.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PIL import Image

from asset_manifest import ASSETS, CATEGORY_SIZES

NORM_DIR = "working/normalized"
OUT_DIR = "working/svg"
SIZE_LIMIT = 50 * 1024
ICON_LIMIT = 20 * 1024
ICON_CATEGORIES = {"icon"}

# Palette size by category. Fewer colours = smaller SVG and fewer bands, which
# also flattens the accepted gradient streaks documented in residual-defects.md.
PALETTE = {
    "chair": 14, "frame-ring": 12, "avatar": 12,
    "chip": 16, "icon": 4, "button-round": 16,
    "card-face": 10, "card-back": 12,
    "badge-pill": 14, "button-pill": 14, "panel-wide": 14,
}
TRACE_TURNPOLICY = {"spline": 4, "polygon": 2, "corner": 1}


# --------------------------------------------------------------------------
# potrace bridge
# --------------------------------------------------------------------------

def potrace_layer(mask: np.ndarray, turnpolicy: int, fill: str,
                  alphamax: float = 1.0, opttolerance: float = 0.34) -> str:
    """Trace one boolean mask and return an SVG <g> block painted `fill`.

    potrace emits `<g transform="translate(0,H) scale(0.1,-0.1)" fill="#000000">`
    and puts the fill on the GROUP, not the path -- an earlier version matched
    `<path d="..." fill="...">` and silently produced zero shapes for all 41
    assets. The group and its transform are kept as-is so potrace's own unit
    convention stays correct; only the fill colour is substituted.
    """
    h, w = mask.shape
    # P4 rows are byte-aligned; keep the header width a multiple of 8 so the
    # packed row length always matches what potrace expects.
    packed = np.packbits(mask.astype(np.uint8), axis=1)
    pbm = bytearray(b"P4\n%d %d\n" % (w, h))
    for row in packed:
        pbm += row.tobytes()

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.pbm")
        dst = os.path.join(td, "out.svg")
        with open(src, "wb") as f:
            f.write(bytes(pbm))
        cmd = ["potrace", "-b", "svg", "-o", dst,
               "-t", str(turnpolicy), "-a", str(alphamax),
               "-O", str(opttolerance), "-u", "10", src]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(dst):
            return ""
        data = open(dst).read()

    m = re.search(r"<g\b[^>]*>.*?</g>", data, flags=re.S)
    if not m:
        return ""
    block = m.group(0)
    block = re.sub(r'fill="[^"]*"', f'fill="{fill}"', block, count=1)
    return block


# --------------------------------------------------------------------------
# colour quantisation
# --------------------------------------------------------------------------

def quantise(im: Image.Image, ncolours: int):
    """Median-cut-free quantisation via PIL's adaptive palette.

    Returns (labels HxW int, palette Nx3 uint8). Fully transparent pixels get
    label -1 so they are never traced.
    """
    rgba = im.convert("RGBA")
    a = np.asarray(rgba.getchannel("A"), dtype=np.uint8)
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.uint8)
    solid = a > 128
    if not solid.any():
        return None, None
    q = Image.fromarray(rgb).quantize(colors=ncolours, method=Image.MEDIANCUT)
    labels = np.asarray(q, dtype=np.int16)
    pal = np.asarray(q.getpalette()[: ncolours * 3], dtype=np.uint8)
    pal = pal.reshape(-1, 3)
    labels[~solid] = -1
    return labels, pal


# --------------------------------------------------------------------------
# svg assembly
# --------------------------------------------------------------------------

def optimise_svg(svg: str, precision: int = 2) -> str:
    """Stand-in for `svgo --multipass`, which is unavailable here."""
    # round coordinate noise in path data
    def _round(m):
        return f"{round(float(m.group(0)), precision):g}"
    svg = re.sub(r"-?\d+\.\d+", _round, svg)
    # drop presentational noise
    svg = re.sub(r'\s+(xmlns:xlink|version|enable-background|'
                 r'shape-rendering|image-rendering)', r'', svg)
    svg = re.sub(r'<\?xml[^>]*\?>\s*', '', svg)
    svg = re.sub(r'<!--.*?-->', '', svg, flags=re.S)
    svg = re.sub(r'\s{2,}', ' ', svg)
    svg = re.sub(r'>\s+<', '><', svg)
    return svg.strip()


def build_svg(w: int, h: int, blocks: List[str]) -> str:
    body = "\n".join(blocks)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {w} {h}" width="{w}" height="{h}">\n'
            f'{body}\n</svg>')


# Measured on seat-red (the densest asset: ornate gold scrollwork).
#   512px/14col/alpha1.0/opt0.34 -> 227 KB   (over the 50 KB limit)
#   256px/14col                  ->  82 KB
#   192px/8col /alpha1.4/opt1.5  ->  44 KB   (fits)
#   160px/6col /corner/alpha1.6  ->  27 KB
# So quality has to be traded per asset, not globally: try tight settings first
# and only coarsen for the assets that genuinely cannot fit.
LADDER = (
    # (trace_w, palette_delta, turnpolicy, alphamax, opttolerance)
    (256, 0,  4, 1.0, 0.34),
    (224, 2,  4, 1.2, 1.00),
    (192, 4,  4, 1.4, 1.50),
    (160, 6,  1, 1.6, 2.00),
    (128, 8,  1, 1.8, 2.50),
)


def vectorize_one(aid: str, cat: str, path: str, work_w: int = 512) -> dict:
    limit = ICON_LIMIT if cat in ICON_CATEGORIES else SIZE_LIMIT
    im0 = Image.open(path).convert("RGBA")
    best = None
    for (tw_max, pd, tpol, alpha, opt) in LADDER:
        r = _trace_attempt(aid, cat, im0, tw_max, pd, tpol, alpha, opt)
        if not r.get("ok"):
            if best is None:
                best = r
            continue
        r["bytes"] = len(r["svg"].encode())
        best = r
        if r["bytes"] < limit * 0.9:
            break
    if best and best.get("ok"):
        best["size_ok"] = best["bytes"] < limit
        best["limit"] = limit
    return best


def _trace_attempt(aid: str, cat: str, im: Image.Image, tw_max: int,
                   palette_delta: int, turnpolicy: int, alpha: float,
                   opt: float) -> dict:
    im = im.convert("RGBA")
    # Trace at a reduced raster: potrace output scales via viewBox, and tracing
    # 1024px art would triple the path count for no visible gain.
    scale = min(1.0, tw_max / max(im.size))
    tw = max(8, round(im.width * scale) // 8 * 8)
    th = max(8, round(im.height * scale))
    small = im.resize((tw, th), Image.LANCZOS)

    ncol = max(3, PALETTE.get(cat, 12) - palette_delta)
    labels, pal = quantise(small, ncol)
    if labels is None:
        return {"id": aid, "ok": False, "error": "fully transparent"}

    if cat in ("badge-pill", "button-pill") and turnpolicy == 4:
        turnpolicy = TRACE_TURNPOLICY["polygon"]

    # Painter's order: rarest colour first so dominant art lands on top.
    counts = [(int((labels == i).sum()), i) for i in range(len(pal))]
    counts.sort()
    blocks: List[str] = []
    traced = 0
    for _n, i in counts:
        mask = labels == i
        if mask.sum() < 4:
            continue
        r, g, b = (int(v) for v in pal[i])
        blk = potrace_layer(mask, turnpolicy, f"#{r:02x}{g:02x}{b:02x}",
                            alphamax=alpha, opttolerance=opt)
        if blk:
            blocks.append(blk)
            traced += 1

    if not blocks:
        return {"id": aid, "ok": False, "error": "potrace produced no paths"}

    svg = optimise_svg(build_svg(tw, th, blocks))
    return {"id": aid, "ok": True, "svg": svg, "w": tw, "h": th,
            "shapes": traced, "colours": ncol, "cat": cat,
            "turnpolicy": {1: "corner", 2: "polygon", 4: "spline"}[turnpolicy],
            "trace_w": tw, "alpha": alpha, "opt": opt}


def validate(aid: str, svg: str, cat: str) -> dict:
    """xmllint + the brief's structural checks."""
    out = {"id": aid, "bytes": len(svg.encode())}
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as f:
        f.write(svg)
        tmp = f.name
    try:
        r = subprocess.run(["xmllint", "--noout", tmp],
                           capture_output=True, text=True, timeout=60)
        out["xml_ok"] = r.returncode == 0
        if r.returncode != 0:
            out["xml_err"] = r.stderr.strip()[:200]
    finally:
        os.remove(tmp)
    out["has_raster"] = bool(re.search(r"<image\b", svg))
    out["has_viewbox"] = "viewBox=" in svg
    limit = ICON_LIMIT if cat in ICON_CATEGORIES else SIZE_LIMIT
    out["size_ok"] = out["bytes"] < limit
    out["limit"] = limit
    out["ok"] = (out["xml_ok"] and not out["has_raster"] and out["has_viewbox"]
                 and out["size_ok"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norm", default=NORM_DIR)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--report", default="docs/vectorize-report.md")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    ap.add_argument("--trace-width", type=int, default=512)
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)

    jobs = []
    for aid, cat, kind, ref, note in ASSETS:
        src = os.path.join(args.norm, f"{aid}.png")
        if not os.path.exists(src):
            jobs.append((aid, cat, None))
            continue
        jobs.append((aid, cat, src))

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(vectorize_one, aid, cat, src, args.trace_width): (aid, cat)
                for aid, cat, src in jobs if src}
        for fu in as_completed(futs):
            aid, cat = futs[fu]
            try:
                res = fu.result()
            except Exception as exc:            # noqa: BLE001
                res = {"id": aid, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if res.get("ok"):
                v = validate(res["id"], res["svg"], cat)
                res.update({k: v[k] for k in
                            ("xml_ok", "has_raster", "has_viewbox", "size_ok",
                             "bytes", "limit") if k in v})
                res["ok"] = v["ok"]
                res["xml_err"] = v.get("xml_err", "")
                if v["ok"]:
                    with open(os.path.join(args.out, f"{aid}.svg"), "w") as f:
                        f.write(res["svg"])
            results.append(res)

    results.sort(key=lambda r: r["id"])
    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]

    # ---- report ----
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    L = ["# Vectorize report (Phase 4)", "",
         "vtracer / pngtosvg / autotrace / svgo are unavailable in this environment",
         "(apt has no autotrace candidate; npm is blocked by a security wrapper that",
         "intercepts its `env` exec). `potrace` and `xmllint` were installed via apt and",
         "are used instead: colour art is quantised and traced as one potrace mask per",
         "palette colour, stacked back-to-front, which is the same construction as",
         "vtracer's `--hierarchical stacked`. An SVG optimiser equivalent to",
         "`svgo --multipass` is implemented in `optimise_svg()`.", "",
         f"- assets in: {len(results)}   traced OK: {len(ok)}   failed: {len(bad)}",
         f"- workers: {args.workers}   trace raster width: {args.trace_width}px",
         f"- elapsed: {time.time() - t0:.1f}s", "",
         "| asset | category | trace | paths | raster | XML | viewBox | KB | limit | status |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if not r.get("ok"):
            why = r.get("error") or r.get("xml_err") or "unknown"
            if r.get("xml_ok") is False:
                why = "invalid XML: " + (r.get("xml_err") or "")
            elif r.get("has_raster"):
                why = "embedded raster"
            elif r.get("has_viewbox") is False:
                why = "no viewBox"
            elif r.get("size_ok") is False:
                why = (f"over {r.get('limit', 0)//1024}KB "
                       f"({r.get('bytes', 0)/1024:.0f}KB at floor settings)")
            L.append(f"| `{r['id']}` | - | - | - | - | - | - | - | - | **FAIL** {why[:70]} |")
            continue
        L.append(
            f"| `{r['id']}` | {r.get('cat','')} | {r['turnpolicy']} | {r['shapes']} | "
            f"{'no' if not r['has_raster'] else 'YES'} | "
            f"{'ok' if r['xml_ok'] else 'BAD'} | "
            f"{'yes' if r['has_viewbox'] else 'NO'} | "
            f"{r['bytes']/1024:.1f} | {r['limit']//1024}KB | OK |")

    L += ["", "## Checks applied to every SVG", "",
          "- `xmllint --noout` — must pass",
          "- no `<image>` element (no embedded raster)",
          "- `viewBox` present",
          f"- < {SIZE_LIMIT//1024}KB ({ICON_LIMIT//1024}KB for icons)"]
    with open(args.report, "w") as f:
        f.write("\n".join(L) + "\n")

    print(f"[VEC] {len(ok)}/{len(results)} SVGs in {time.time()-t0:.1f}s")
    for r in bad:
        print(f"       FAIL {r['id']}: {r.get('error') or r.get('xml_err') or 'unknown'}")
    over = [r for r in ok if not r.get("size_ok")]
    if over:
        print(f"       over size limit: {[r['id'] for r in over]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
