"""Phase 3 — watermark removal, trim, and normalise to target sizes.

Runs over both input sets, because the mark is not only on the source PNGs:
btn-auto (a Phase 2 split crop) carries one too.

  3.1 watermark removal  (geometric + colour-density, OCR confirm)
  3.2 trim to content bbox, re-pad 8px
  3.3 background completion for residual wedges (btn-repeat)
  3.4 LANCZOS resize into the category box, centred, transparent padding
  3.5 per-category consistency check

Source files are never modified. Dropped variants are copied to
working/dropped/ rather than moved out of the source directory.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PIL import Image

from asset_manifest import (ASSETS, CATEGORY_SIZES, DROPPED, GENERATED, SOURCE,
                            NO_BG_FLOOD, SHAPE_MASK, WATERMARKED,
                            WATERMARK_CLEAN)
from assetlib import (apply_shape_mask, background_mask_for_image,
                      strip_flat_border_debris)
from watermark import Mark, detect_mark, remove_watermark

TRIM_PAD = 8
SPLIT_DIR = "working/split"
OUT_DIR = "working/normalized"
DROPPED_DIR = "working/dropped"


def load_asset(kind: str, ref: str):
    if kind == "split":
        return Image.open(os.path.join(SPLIT_DIR, ref))
    return Image.open(os.path.join(SOURCE, ref))


def trim_to_content(im: Image.Image, pad: int = TRIM_PAD) -> Image.Image:
    """Trim transparent border, then re-pad by `pad`."""
    a = np.asarray(im.getchannel("A"))
    if a.max() == 0:
        return im
    ys, xs = np.nonzero(a > 8)
    box = (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
           min(im.width, int(xs.max()) + 1 + pad), min(im.height, int(ys.max()) + 1 + pad))
    return im.crop(box)


def fill_foreign_pixels(im: Image.Image, bg_colour, min_frac: float = 0.004):
    """Fill near-opaque clusters that are a flat foreign colour.

    Catches the beige wedge left on btn-repeat: a large, flat, low-variance
    region touching the canvas edge that does not belong to the asset.
    """
    arr = np.asarray(im).astype(np.int16)
    a = arr[:, :, 3]
    ys, xs = np.nonzero(a > 200)
    if ys.size == 0:
        return im, 0
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    w, h = im.size
    edge = (x0 <= 1 or y0 <= 1 or x1 >= w - 1 or y1 >= h - 1)
    if not edge:
        return im, 0
    patch = arr[y0:y1, x0:x1]
    opaque = patch[patch[:, :, 3] > 200][:, :3]
    if opaque.size == 0:
        return im, 0
    if float((patch[:, :, 3] > 200).mean()) < min_frac:
        return im, 0
    std = float(opaque.std())
    if std > 18.0:
        return im, 0            # too varied to be a flat foreign fill
    fill = np.median(opaque, axis=0).astype(np.uint8)
    out = arr.copy()
    out[y0:y1, x0:x1, 0] = np.where(patch[:, :, 3] > 200, fill[0], out[y0:y1, x0:x1, 0])
    out[y0:y1, x0:x1, 1] = np.where(patch[:, :, 3] > 200, fill[1], out[y0:y1, x0:x1, 1])
    out[y0:y1, x0:x1, 2] = np.where(patch[:, :, 3] > 200, fill[2], out[y0:y1, x0:x1, 2])
    out[y0:y1, x0:x1, 3] = 0   # make it transparent: it was never artwork
    return Image.fromarray(out.astype(np.uint8), "RGBA"), int((patch[:, :, 3] > 200).sum())


def fit_to_box(im: Image.Image, size) -> Image.Image:
    """Scale to fit inside `size` preserving aspect, centre, pad transparent."""
    tw, th = size
    scale = min(tw / im.width, th / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    resized = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    canvas.paste(resized, ((tw - nw) // 2, (th - nh) // 2), resized)
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--keep-raw", action="store_true",
                    help="also write <id>-raw.png beside the clean file")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--report", default="docs/normalize-report.md")
    args = ap.parse_args()

    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(DROPPED_DIR, exist_ok=True)

    # ---- dropped variants: copy out of reach, never delete source ----
    dropped_rows = []
    for fname, reason in DROPPED.items():
        src = os.path.join(SOURCE, fname)
        dst = os.path.join(DROPPED_DIR, fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            dropped_rows.append((fname, reason, os.path.getsize(src)))

    rows = []
    by_cat = defaultdict(list)
    for aid, cat, kind, ref, note in ASSETS:
        try:
            im = load_asset(kind, ref)
        except Exception as exc:                       # noqa: BLE001
            rows.append({"id": aid, "cat": cat, "status": "FAIL",
                         "error": f"load: {type(exc).__name__}: {exc}"})
            continue

        src_w, src_h = im.size
        # Detection is scoped to assets whose watermark status was visually
        # verified. Running the heuristic on all 41 produced 4 false positives
        # on ornate panels and 2 false negatives; see asset_manifest.
        known_marked = aid in WATERMARKED
        pre = detect_mark(im) if known_marked else Mark(False, None, 0,
                                                        confidence="known-clean")
        raw_to = (os.path.join(args.out, f"{aid}-raw.png")
                  if args.keep_raw else None)
        clean, mark, method = remove_watermark(im, ocr=not args.no_ocr,
                                               keep_raw_to=raw_to)
        if not known_marked:
            mark = pre
            method = "skipped (verified clean)"

        # --- 3.2 background removal, for BOTH input sets -------------------
        # Phase 2 only stripped backgrounds from the 4 sheets. The 29
        # individuals still carried their baked-in near-white / dark / tan
        # backdrop, which is why panel-pot, panel-you, panel-round-room,
        # panel-balance, badge-hot, badge-you, banner-winner and btn-sound-on
        # all rendered with an opaque grey slab behind the artwork.
        # Order matters: the watermark is removed FIRST, while the backdrop is
        # still present to inpaint against, because the mark is composited onto
        # that backdrop and would otherwise survive as a content island.
        if aid in NO_BG_FLOOD:
            bg_note = "skipped (silhouette matches background tone)"
        elif kind == "individual":
            bg = background_mask_for_image(clean, tolerances=(8, 12, 15, 25),
                                          max_remove=0.92)
            if bg["ok"]:
                clean = bg["rgba"]
                bg_note = (f"flood tol={bg['tolerance']} "
                           f"removed {bg['removed_fraction']*100:.0f}%")
            else:
                bg_note = "REJECTED (tolerance would eat artwork)"
        else:
            bg_note = "done in phase 2"

        # --- 3.3 background completion + flat-debris strip -----------------
        # The beige wedge from the Phase 2 grid bands is fused to the artwork
        # (largest island 60-78% of canvas, next blobs <3%), so connectivity
        # cannot separate it. It is flat and border-touching, which can.
        a0 = np.asarray(clean.getchannel("A"))
        if (a0 > 0).any():
            fg2 = strip_flat_border_debris(a0 > 0, np.asarray(clean.convert("RGB")),
                                          flat_std=22.0, erode_px=2)
            a1 = a0.copy()
            a1[~fg2] = 0
            clean.putalpha(Image.fromarray(a1, "L"))

        # --- manual shape mask for the 4 assets the fill destroys ---------
        if aid in SHAPE_MASK:
            shape, inset = SHAPE_MASK[aid]
            clean = apply_shape_mask(clean, shape, inset)

        post = detect_mark(clean) if known_marked else Mark(False, None, 0)

        clean, filled = fill_foreign_pixels(clean, None)
        clean = trim_to_content(clean)
        tw, th = CATEGORY_SIZES[cat]
        out_im = fit_to_box(clean, (tw, th))
        out_path = os.path.join(args.out, f"{aid}.png")
        out_im.save(out_path, "PNG")

        by_cat[cat].append((aid, out_im.size))
        rows.append({
            "id": aid, "cat": cat, "kind": kind, "note": note,
            "src": f"{src_w}x{src_h}",
            "mark_px": mark.pixels, "ocr": mark.ocr_text,
            "conf": mark.confidence, "method": method,
            "post_px": post.pixels,
            "filled_px": filled, "bg": bg_note,
            "out": f"{tw}x{th}",
            "bytes": os.path.getsize(out_path),
            "status": ("OK" if post.pixels < 40 else "RESIDUAL") if known_marked else "OK",
            "expected_mark": known_marked,
        })

    # ---- consistency (3.5) ----
    consistency = []
    for cat, items in sorted(by_cat.items()):
        sizes = {s for _i, s in items}
        consistency.append((cat, len(items), sorted(sizes),
                            "OK" if len(sizes) == 1 else "MISMATCH"))

    # ---- report ----
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    resid = [r for r in rows if r.get("status") == "RESIDUAL"]
    fails = [r for r in rows if r.get("status") == "FAIL"]
    found = [r for r in rows if r.get("mark_px", 0) >= 40]

    L = ["# Normalize report (Phase 3)", "",
         "Watermark detection is geometric + colour-density in the bottom-right",
         "region (last 20% w x 12% h, S<15, 100<=V<=200, >=40px cluster); tesseract",
         "is confirmation only. Removal is a crop when the mark falls outside the",
         "content bbox, and a median inpaint when it overlaps the artwork.",
         "", f"- assets processed: **{len(rows)}**",
         f"- watermarks detected: **{len(found)}** / {len(rows)}",
         f"- residual after removal: **{len(resid)}**",
         f"- failures: **{len(fails)}**",
         f"- dropped variants quarantined to `working/dropped/`: {len(dropped_rows)}",
         f"- elapsed: {time.time() - t0:.1f}s", "",
         "## Per asset", "",
         "| asset | cat | src | mark px | method | post px | background | out | KB | status |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("status") == "FAIL":
            L.append(f"| `{r['id']}` | {r['cat']} | - | - | - | - | - | - | - | **FAIL** {r.get('error','')} |")
            continue
        L.append(
            f"| `{r['id']}` | {r['cat']} | {r['src']} | {r['mark_px']} | "
            f"{r['method']} | {r['post_px']} | {r['bg']} | "
            f"{r['out']} | {r['bytes']/1024:.0f} | "
            f"{'OK' if r['status']=='OK' else '**RESIDUAL**'} |")

    L += ["", "## Category consistency (3.5)", "",
          "| category | assets | distinct sizes | verdict |", "|---|---|---|---|"]
    for cat, n, sizes, verdict in consistency:
        L.append(f"| {cat} | {n} | {sizes} | {'OK' if verdict=='OK' else '**MISMATCH**'} |")

    L += ["", "## Dropped variants (quarantined, source untouched)", "",
          "| file | reason | bytes |", "|---|---|---|"]
    for f, r, b in dropped_rows:
        L.append(f"| `{f}` | {r} | {b:,} |")

    L += ["", "## Generated in Phase 6 (not from source)", ""]
    for gid, cat, why in GENERATED:
        L.append(f"- `{gid}.svg` ({cat}) — {why}")

    with open(args.report, "w") as f:
        f.write("\n".join(L) + "\n")

    print(f"[NORM] {len(rows)} assets -> {args.out}")
    print(f"       watermarks found {len(found)}, residual {len(resid)}, failures {len(fails)}")
    for cat, n, sizes, verdict in consistency:
        flag = "" if verdict == "OK" else "  <-- MISMATCH"
        print(f"       {cat:<14} {n:>2} assets {sizes}{flag}")
    for r in rows:
        if r.get("status") != "OK":
            print(f"       {r['id']}: {r.get('status')} post={r.get('post_px')} {r.get('error','')}")
    return 1 if (fails or resid) else 0


if __name__ == "__main__":
    raise SystemExit(main())
