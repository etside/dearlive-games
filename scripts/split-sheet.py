"""Phase 2 — split unified sheets into individual assets.

Background removal is corner-seeded flood fill, not alpha detection: 37 of the
38 source PNGs are colour type 2 (RGB) with no alpha channel at all.

The four known sheets are declared in SHEETS below with their expected yield
and, where auto-detection is unreliable, an explicit background seed. Expect-
ation mismatches are reported rather than silently accepted.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from PIL import Image

from assetlib import (connected_islands, corner_seeds, pad_box, remove_background,
                      restore_eroded_edges, save_rgba_crop)

SOURCE = "/storage/emulated/0/11labd/Assets-all"

# filename -> (slug, expected_count, asset_ids, seed_hint, tolerance_cap)
#
# `boxes` is an explicit manual manifest, used where island detection cannot
# frame the asset correctly. status-badges needs it: each of the 5 badges
# resolves to TWO islands (a ~250k core plus a ~60k glow halo the flood fill
# detached from it), and the 5 badges are not evenly spaced down the canvas, so
# equal grid bands each clip a fragment of the neighbouring pill. These boxes
# are the union of each badge's core and its halo.
SHEETS = {
    "1790365359113.png": dict(
        slug="status-badges", expected=5,
        ids=["status-waiting", "status-betting-open", "status-betting-closed",
             "status-dealing", "status-result"],
        seed=(207, 214, 224), cap=15,
        boxes=[[61, 406, 1124, 819], [57, 1012, 1124, 1446],
               [54, 1632, 1124, 2068], [58, 2254, 1124, 2678],
               [59, 2855, 1122, 3287]]),
    "1790365874766.png": dict(
        slug="action-buttons", expected=3,
        ids=["btn-repeat", "btn-history", "btn-auto"],
        seed=(76, 77, 95), cap=20),
    "1790365820434.png": dict(
        slug="sound-buttons", expected=2,
        ids=["btn-sound-on", "btn-sound-off"],
        seed=(249, 230, 190), cap=20),
    "1790365433004.png": dict(
        slug="frames", expected=2,
        ids=["frame-ring-gold-sm", "suit-spade-gold"],
        seed=(226, 229, 234), cap=20),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--output", default="working/split")
    ap.add_argument("--min-area", type=int, default=1000)
    ap.add_argument("--restore-tol", type=float, default=0.0,
                    help="colour distance at which an eaten pixel is reclaimed")
    ap.add_argument("--min-area-frac", type=float, default=0.008,
                    help="min island area as a fraction of canvas (noise filter)")
    ap.add_argument("--padding", type=int, default=8)
    ap.add_argument("--report", default="docs/split-report.md")
    ap.add_argument("--only", default=None, help="split a single sheet by filename")
    ap.add_argument("--grid", action="store_true",
                    help="use known grid slots instead of island detection. The "
                         "sheets are axis-aligned grids, so this frames assets far "
                         "more reliably than flood fill, which erodes the soft glow "
                         "around each pill.")
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    # Clear stale output. A previous run with a wrong island threshold leaves
    # orphan crops behind (28 of them on the first attempt), which then get
    # picked up by Phase 3 and registered as real assets. Only ever touches
    # --output; the source directory is never written to.
    for entry in os.listdir(args.output):
        target = os.path.join(args.output, entry)
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    report_rows = []
    t0 = time.time()

    targets = {args.only: SHEETS[args.only]} if args.only else SHEETS

    for fname, spec in targets.items():
        path = os.path.join(args.source, fname)
        if not os.path.exists(path):
            report_rows.append({"sheet": fname, "status": "FAIL", "error": "source missing"})
            continue

        res = remove_background(
            path, tolerances=(8, 12, 15, spec["cap"]),
            seed_hint=spec.get("seed"),
            # A sheet's art is the point of the image; a 40% cap would reject
            # every sheet, so the cap is raised and verified by island count.
            max_remove=0.92)
        if not res["ok"]:
            report_rows.append({"sheet": fname, "slug": spec["slug"],
                                "status": "FAIL", "attempts": res["attempts"]})
            continue

        rgba = res["rgba"]
        fg = ~res["bg_mask"]
        w, h = rgba.size
        canvas = w * h

        if spec.get("boxes"):
            # Manual manifest (Decision 1 fallback).
            boxes = [pad_box({"x0": b[0], "y0": b[1], "x1": b[2], "y1": b[3]},
                             w, h, args.padding) for b in spec["boxes"]]
            islands_meta = {"mode": "manifest", "islands_found": len(boxes),
                            "noise_islands": 0, "discarded_fragments": 0}
        elif args.grid:
            # Known grid: divide the canvas into `expected` equal bands along
            # the dominant axis. Framing is exact, so no island threshold and
            # no fragment-discard heuristic is involved.
            vertical = spec.get("grid_axis", "auto")
            if vertical == "auto":
                vertical = h >= w
            n = spec["expected"]
            boxes = []
            for i in range(n):
                if vertical:
                    y0 = round(i * h / n); y1 = round((i + 1) * h / n)
                    boxes.append({"x0": 0, "y0": y0, "x1": w, "y1": y1})
                else:
                    x0 = round(i * w / n); x1 = round((i + 1) * w / n)
                    boxes.append({"x0": x0, "y0": 0, "x1": x1, "y1": h})
            # Trim each band to its own foreground so bands do not ship a
            # canvas of empty background.
            trimmed = []
            for bx in boxes:
                sub_fg = fg[bx["y0"]:bx["y1"], bx["x0"]:bx["x1"]]
                ys = np.flatnonzero(sub_fg.any(axis=1))
                xs = np.flatnonzero(sub_fg.any(axis=0))
                if ys.size == 0 or xs.size == 0:
                    trimmed.append(bx)
                    continue
                t = {"x0": bx["x0"] + int(xs[0]), "x1": bx["x0"] + int(xs[-1]) + 1,
                     "y0": bx["y0"] + int(ys[0]), "y1": bx["y0"] + int(ys[-1]) + 1}
                trimmed.append(pad_box(t, w, h, args.padding))
            boxes = trimmed
            islands_meta = {"mode": "grid", "islands_found": n, "noise_islands": 0,
                            "discarded_fragments": 0}
        else:
            # Background gradients leave speckle that even tolerance 8 cannot
            # reach, producing thousands of 1-2px islands. A flat --min-area of
            # 1000 keeps all of them. Scale the threshold to the canvas
            # instead: real assets occupy >5%, noise <0.05%.
            min_area = max(args.min_area, int(args.min_area_frac * canvas))
            all_islands = connected_islands(fg)
            keep = [i for i in all_islands if i["area"] >= min_area]
            keep.sort(key=lambda d: -d["area"])
            discarded = 0
            if len(keep) > spec["expected"]:
                discarded = len(keep) - spec["expected"]
                keep = keep[:spec["expected"]]
            keep.sort(key=lambda d: (d["y0"], d["x0"]))

            if args.restore_tol > 0:
                seeds = corner_seeds(res["rgb"])
                if spec.get("seed") is not None:
                    seeds = seeds + [np.asarray(spec["seed"], dtype=np.float64)]
                fg = restore_eroded_edges(res["rgb"], fg, seeds, keep,
                                          restore_tol=args.restore_tol)
            # Rebuild the mask from the kept islands so interior speckle
            # becomes transparent instead of shipping as dots.
            clean = np.zeros_like(fg)
            for i in keep:
                clean[i["y0"]:i["y1"], i["x0"]:i["x1"]] |= \
                    fg[i["y0"]:i["y1"], i["x0"]:i["x1"]]
            rgba = rgba.copy()
            a = np.asarray(rgba.getchannel("A"), dtype=np.uint8).copy()
            a[~clean] = 0
            rgba.putalpha(Image.fromarray(a, "L"))
            boxes = [pad_box(i, w, h, args.padding) for i in keep]
            islands_meta = {"mode": "islands", "islands_found": len(keep),
                            "noise_islands": len(all_islands),
                            "discarded_fragments": discarded}

        out_dir = os.path.join(args.output, spec["slug"])
        written = []
        for idx, box in enumerate(boxes):
            aid = spec["ids"][idx] if idx < len(spec["ids"]) else \
                f"{spec['slug']}-{idx + 1}"
            out_path = os.path.join(out_dir, f"{aid}.png")
            save_rgba_crop(rgba, box, out_path)
            written.append({"asset_id": aid, "path": out_path,
                            "box": box, "px": (box["x1"] - box["x0"], box["y1"] - box["y0"])})

        ok = len(written) == spec["expected"]
        report_rows.append({
            "sheet": fname, "slug": spec["slug"], "status": "OK" if ok else "MISMATCH",
            "size": f"{w}x{h}", "seed": res["seed"], "tolerance": res["tolerance"],
            "removed_fraction": res["removed_fraction"],
            "mode": islands_meta["mode"],
            "noise_islands": islands_meta["noise_islands"],
            "islands_found": islands_meta["islands_found"],
            "discarded_fragments": islands_meta["discarded_fragments"],
            "expected": spec["expected"], "written": written,
        })

    # ---- report ----
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    total = sum(len(r.get("written", [])) for r in report_rows)
    lines = [
        "# Split report (Phase 2)", "",
        "Background removed by corner-seeded flood fill, not alpha: 37 of 38",
        "source PNGs are colour type 2 (RGB) with no alpha channel.", "",
        f"- min-island-area: {args.min_area} or {args.min_area_frac:.1%} of canvas, whichever is larger"
        f"  padding: {args.padding}px",
        f"- sheets processed: {len(report_rows)}  assets written: {total}",
        f"- elapsed: {time.time() - t0:.1f}s", "",
        "| sheet | size | mode | seed RGB | tol | bg removed | islands kept | frag dropped | expected | status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report_rows:
        if r.get("status") == "FAIL" and "written" not in r:
            lines.append(f"| `{r['sheet']}` | - | - | - | - | - | - | **FAIL** {r.get('error','')} |")
            continue
        lines.append(
            f"| `{r['sheet']}` | {r['size']} | {r.get('mode','-')} | {tuple(r['seed'])} | {r['tolerance']} | "
            f"{r['removed_fraction']*100:.1f}% | {r['islands_found']} | "
            f"{r['discarded_fragments']} | {r['expected']} | "
            f"{'OK' if r['status']=='OK' else '**MISMATCH**'} |")

    lines += ["", "## Output", ""]
    for r in report_rows:
        for w_ in r.get("written", []):
            lines.append(f"- `{w_['path']}` — {w_['px'][0]}x{w_['px'][1]} "
                         f"(box {w_['box']['x0']},{w_['box']['y0']} "
                         f"{w_['box']['x1']},{w_['box']['y1']})")
    with open(args.report, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[SPLIT] {len(report_rows)} sheets -> {total} assets")
    for r in report_rows:
        print(f"  {r.get('slug', r.get('sheet'))}: {r['status']} "
              f"islands={r.get('islands_found')}/{r.get('expected')} "
              f"noise={r.get('noise_islands')} tol={r.get('tolerance')} "
              f"tol={r.get('tolerance')} bg={r.get('removed_fraction')}")
    bad = [r for r in report_rows if r.get("status") != "OK"]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
