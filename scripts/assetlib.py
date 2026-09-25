"""Shared image ops for the asset pipeline.

Two facts about this source set drive the design:

1. 37 of 38 source PNGs are colour type 2 (RGB) with NO alpha channel and 0%
   transparent pixels. Only file_...512d.png (the club suit) is real RGBA.
   So "find transparent regions" does not work; background has to be removed
   by flood fill from the image border.

2. The background is NOT uniformly white. Corner samples range from
   (252,253,255) near-white through (207,214,224) light blue-grey, (88,88,90)
   dark grey, (76,77,95) navy, to (185,153,114) tan. The seed colour and the
   tolerance must therefore be per-file, never hardcoded.

No scipy/cv2/skimage in this environment, so connected components are done with
a run-length union-find over the foreground mask, which is exact for axis-aligned
islands (what sheets contain) and linear in the number of runs.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

# Tolerance ladder from the brief. Seeded at 8 (tight) so gold-framed assets
# are not eaten, widening only when a tight pass finds too little.
TOLERANCE_LADDER: Tuple[int, ...] = (8, 15, 25)
# Never treat more than this fraction of the canvas as background: past this we
# are probably about to delete the artwork itself.
MAX_REMOVE_FRACTION = 0.40
# ...unless the file is a known sheet whose art genuinely covers the canvas.
DEFAULT_MIN_ISLAND_AREA = 1000


# --------------------------------------------------------------------------
# seeds
# --------------------------------------------------------------------------

def corner_seed(rgb: np.ndarray) -> np.ndarray:
    """Average the 4 corner pixels, each sampled as a small patch mean.

    Kept for reporting. Use corner_seeds() for the actual flood: several of
    these sheets have HETEROGENEOUS borders (1790365820434.png has corners
    (250,230,193), (84,84,92), (111,112,117), (104,104,114)), so a single
    averaged seed only ever opens one corner and leaves the rest as noise.
    """
    return np.mean(np.stack(corner_seeds(rgb)), axis=0)


def corner_seeds(rgb: np.ndarray) -> List[np.ndarray]:
    """One seed colour per corner, each a small patch mean.

    A 1px sample is too noisy on AI art with soft shadows or a vignette.
    """
    h, w = rgb.shape[:2]
    k = max(1, min(h, w) // 200)
    patches = [
        rgb[0:k, 0:k], rgb[0:k, w - k:w],
        rgb[h - k:h, 0:k], rgb[h - k:h, w - k:w],
    ]
    return [p.reshape(-1, 3).mean(axis=0).astype(np.float64) for p in patches]


# --------------------------------------------------------------------------
# background removal
# --------------------------------------------------------------------------

def flood_background(rgb: np.ndarray, seeds: Sequence[np.ndarray], tolerance: int
                     ) -> np.ndarray:
    """Return a boolean mask of BACKGROUND pixels reachable from the border.

    PIL's C floodfill is seeded on every border pixel that matches ANY seed
    colour, so an interior region that happens to share the background colour
    (a white specular highlight inside a chip, a pale gap inside a wordmark) is
    NOT removed -- only the border-connected region is.
    """
    h, w = rgb.shape[:2]
    thresh = tolerance * tolerance * 3  # PIL compares squared euclidean distance
    work = Image.fromarray(rgb.astype(np.uint8), "RGB")
    px = work.load()
    border: List[Tuple[int, int]] = []
    for x in range(w):
        border.append((x, 0))
        border.append((x, h - 1))
    for y in range(h):
        border.append((0, y))
        border.append((w - 1, y))

    marker = (255, 0, 255)
    for (x, y) in border:
        r, g, b = px[x, y]
        for s in seeds:
            d2 = (r - s[0]) ** 2 + (g - s[1]) ** 2 + (b - s[2]) ** 2
            if d2 <= thresh:
                ImageDraw.floodfill(work, (x, y), marker, thresh=thresh)
                break

    arr = np.asarray(work, dtype=np.int16)
    return (arr[:, :, 0] == marker[0]) & (arr[:, :, 1] == marker[1]) & \
           (arr[:, :, 2] == marker[2])


def background_mask_for_image(im, tolerances: Sequence[int] = TOLERANCE_LADDER,
                              seed_hint: Optional[Sequence[int]] = None,
                              max_remove: float = MAX_REMOVE_FRACTION,
                              allow_large: bool = False) -> dict:
    """Same ladder as remove_background(), but takes an open Image.

    Phase 3 needs this because it works on already-modified in-memory images
    (watermark removed) rather than re-reading the source file.
    """
    im = im.convert("RGBA")
    rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    alpha_in = np.asarray(im.getchannel("A"), dtype=np.uint8)
    had_alpha = bool(alpha_in.min() < 255)

    seeds = corner_seeds(rgb)
    if seed_hint is not None:
        seeds = seeds + [np.asarray(seed_hint, dtype=np.float64)]
    seed = np.mean(np.stack(seeds), axis=0)

    attempts = []
    best = None
    for tol in tolerances:
        bg = flood_background(rgb, seeds, tol)
        frac = float(bg.mean())
        attempts.append({"tolerance": int(tol), "removed_fraction": round(frac, 4)})
        if frac <= 0.0005:
            continue
        if frac > max_remove and not allow_large:
            continue
        best = (tol, bg, frac)
        break

    if best is None:
        return {"ok": False, "seed": [round(float(v), 1) for v in seed],
                "attempts": attempts, "reason": "no safe tolerance found"}

    tol, bg, frac = best
    out = im.copy()
    a = np.asarray(out.getchannel("A"), dtype=np.uint8).copy()
    a[bg] = 0
    out.putalpha(Image.fromarray(a, "L"))
    return {"ok": True, "tolerance": int(tol), "removed_fraction": round(frac, 4),
            "had_alpha": had_alpha, "seed": [round(float(v), 1) for v in seed],
            "attempts": attempts, "rgba": out, "rgb": rgb, "bg_mask": bg}


def remove_background(path: str, tolerances: Sequence[int] = TOLERANCE_LADDER,
                      seed_hint: Optional[Sequence[int]] = None,
                      max_remove: float = MAX_REMOVE_FRACTION,
                      allow_large: bool = False) -> dict:
    """Load a PNG, strip its border-connected background, return RGBA + log.

    Tries the tolerance ladder in order and keeps the FIRST result that
    removes something meaningful but not destructive. Returns a log describing
    what happened so docs/split-report.md and docs/normalize-report.md can be
    generated from real numbers rather than guesses.
    """
    im = Image.open(path)
    im = im.convert("RGBA")
    rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    alpha_in = np.asarray(im.getchannel("A"), dtype=np.uint8)
    had_alpha = bool(alpha_in.min() < 255)

    # One seed per corner, plus any manual hint (used as extra seeds, never as
    # a replacement -- a single averaged colour misses heterogeneous borders).
    seeds = corner_seeds(rgb)
    if seed_hint is not None:
        seeds = seeds + [np.asarray(seed_hint, dtype=np.float64)]
    seed = np.mean(np.stack(seeds), axis=0)

    attempts = []
    best = None
    for tol in tolerances:
        bg = flood_background(rgb, seeds, tol)
        frac = float(bg.mean())
        attempts.append({"tolerance": int(tol), "removed_fraction": round(frac, 4)})
        if frac <= 0.0005:
            continue
        if frac > max_remove and not allow_large:
            # destructive: keep looking at tighter tolerances
            continue
        best = (tol, bg, frac)
        break

    if best is None:
        return {"ok": False, "path": path, "seed": [round(float(v), 1) for v in seed],
                "attempts": attempts, "reason": "no safe tolerance found"}

    tol, bg, frac = best
    out = im.copy()
    a = np.asarray(out.getchannel("A"), dtype=np.uint8).copy()
    a[bg] = 0
    out.putalpha(Image.fromarray(a, "L"))
    return {"ok": True, "path": path, "tolerance": int(tol),
            "removed_fraction": round(frac, 4), "had_alpha": had_alpha,
            "seed": [round(float(v), 1) for v in seed], "attempts": attempts,
            "rgba": out, "rgb": rgb, "bg_mask": bg}


# --------------------------------------------------------------------------
# connected components (run-length union-find)
# --------------------------------------------------------------------------

def _runs(row: np.ndarray) -> List[Tuple[int, int]]:
    """Contiguous True spans in a boolean row as half-open (start, stop)."""
    if not row.any():
        return []
    idx = np.flatnonzero(np.diff(np.concatenate(([0], row.view(np.int8), [0]))))
    return [(int(idx[i]), int(idx[i + 1])) for i in range(0, len(idx), 2)]


def connected_islands(mask: np.ndarray) -> List[dict]:
    """4-connected components of a boolean mask, as bounding boxes.

    Two-pass run-length labelling with union-find. Exact, and linear in the
    number of runs rather than the number of pixels.
    """
    h, w = mask.shape
    parent: List[int] = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    prev_spans: List[Tuple[int, int]] = []
    prev_labels: List[int] = []
    runs: List[Tuple[int, int, int, int]] = []  # (y0, x0, x1, label)

    for y in range(h):
        spans = _runs(mask[y])
        if not spans:
            prev_spans, prev_labels = [], []
            continue
        cur_labels: List[int] = []
        for (x0, x1) in spans:
            lbl = -1
            for i, (px0, px1) in enumerate(prev_spans):
                if px0 < x1 and x0 < px1:      # spans overlap -> same island
                    if lbl == -1:
                        lbl = prev_labels[i]
                    else:
                        union(lbl, prev_labels[i])
            if lbl == -1:
                lbl = len(parent)
                parent.append(lbl)
            cur_labels.append(lbl)
            runs.append((y, x0, x1, lbl))
        prev_spans = spans
        prev_labels = cur_labels

    # [y0, y1, x0, x1, area]
    boxes: Dict[int, List[int]] = defaultdict(lambda: [10 ** 9, -1, 10 ** 9, -1, 0])
    for (y, x0, x1, lbl) in runs:
        r = find(lbl)
        b = boxes[r]
        b[0] = min(b[0], y)        # top
        b[1] = max(b[1], y + 1)    # bottom (runs are half-open in x, so y+1)
        b[2] = min(b[2], x0)       # left
        b[3] = max(b[3], x1)       # right
        b[4] += x1 - x0

    out = []
    for lbl, (y0, y1, x0, x1, area) in boxes.items():
        out.append({"x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1),
                    "area": int(area)})
    out.sort(key=lambda d: (d["y0"], d["x0"]))
    return out


def pad_box(box: dict, w: int, h: int, padding: int) -> dict:
    return {"x0": max(0, box["x0"] - padding),
            "y0": max(0, box["y0"] - padding),
            "x1": min(w, box["x1"] + padding),
            "y1": min(h, box["y1"] + padding)}


def restore_eroded_edges(rgb: np.ndarray, bg: np.ndarray, seeds: Sequence[np.ndarray],
                         islands: Sequence[dict], restore_tol: float = 26.0,
                         pad: int = 12) -> np.ndarray:
    """Reclaim foreground pixels the flood fill ate.

    A sheet's assets have soft glows and anti-aliased edges that blend toward
    the background, so a border-connected fill creeps inward and leaves ragged
    holes in the asset silhouette (clearly visible on all five status badges).
    The ladder bottoms out at tolerance 8; going tighter removes less
    background but also cannot recover what is already lost.

    So: inside each kept island's padded bounding box, put back any pixel that
    was called background but whose colour is clearly NOT background. A real
    asset edge is saturated or much lighter/darker than a flat backdrop; flat
    backdrop stays removed.

    `restore_tol` is a colour distance, not a flood tolerance.
    """
    h, w = bg.shape
    fg = ~bg
    seeds_arr = np.stack(seeds)
    for isl in islands:
        x0 = max(0, isl["x0"] - pad); y0 = max(0, isl["y0"] - pad)
        x1 = min(w, isl["x1"] + pad); y1 = min(h, isl["y1"] + pad)
        if x1 <= x0 or y1 <= y0:
            continue
        patch = rgb[y0:y1, x0:x1].astype(np.float32)
        # distance to the NEAREST seed colour
        d = np.sqrt(((patch[:, :, None, :] - seeds_arr[None, None, :, :]) ** 2)
                    .sum(axis=3)).min(axis=2)
        reclaim = bg[y0:y1, x0:x1] & (d > restore_tol)
        fg[y0:y1, x0:x1] |= reclaim
    return fg


def strip_flat_border_debris(fg: np.ndarray, rgb: np.ndarray,
                            flat_std: float = 9.0, erode_px: int = 3) -> np.ndarray:
    """Remove flat, border-touching regions that are not artwork.

    The beige wedge carried from the Phase 2 grid bands is FUSED to the asset --
    the largest connected island is 60-78% of the canvas and the next blobs are
    under 3% -- so a connected-component filter cannot remove it. What does
    separate it is that it is (a) flat and (b) touching the canvas edge, while
    the button body has a gradient and interior detail.

    Method: erode the foreground, then flood the eroded-away margin from the
    border through pixels whose local colour variance is below `flat_std`.
    A real gradient or a glyph never satisfies that, so only backdrop survives.
    """
    h, w = fg.shape
    if not fg.any():
        return fg
    g = rgb.astype(np.float32).mean(axis=2)
    # local variance in a 5x5 window, via a box sum (no scipy)
    k = 2
    acc = np.zeros_like(g)
    acc2 = np.zeros_like(g)
    p = np.pad(g, k, mode="edge")
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            s = p[k + dy:k + dy + h, k + dx:k + dx + w]
            acc += s
            acc2 += s * s
    n = (2 * k + 1) ** 2
    var = np.maximum(acc2 / n - (acc / n) ** 2, 0.0)
    flat = var <= (flat_std ** 2)

    margin = _erode_np(fg, erode_px)
    border = np.zeros_like(fg)
    border[0, :] = border[-1, :] = True
    border[:, 0] = border[:, -1] = True
    removable = (~margin) & flat & border
    # grow the removal through connected flat regions
    out = removable.copy()
    for _ in range(6):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        grown &= flat & (~margin)
        if grown.sum() == out.sum():
            break
        out = grown
    return fg & ~out


def _erode_np(mask: np.ndarray, px: int) -> np.ndarray:
    m = mask.copy()
    for _ in range(max(0, px)):
        p = np.pad(m, 1, mode="constant", constant_values=False)
        m = (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] &
             p[1:-1, :-2] & p[1:-1, 2:])
    return m


def apply_shape_mask(im: Image.Image, shape: str, inset: float = 0.0) -> Image.Image:
    """Force the alpha channel to a shape, for assets a flood fill cannot save.

    Four assets share a failure mode: a thin light ornate element (gold frame,
    navy oval) on a backdrop of similar tone, which the fill eats. Re-processing
    them with a manual shape mask keeps the element because nothing is inferred.
    """
    w, h = im.size
    ix, iy = int(w * inset), int(h * inset)
    x0, y0, x1, y1 = ix, iy, w - ix, h - iy
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    if shape == "ellipse":
        d.ellipse([x0, y0, x1, y1], fill=255)
    elif shape == "rounded-rect":
        r = int(min(x1 - x0, y1 - y0) * 0.28)
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=255)
    else:
        raise ValueError(f"unknown mask shape {shape!r}")
    out = im.convert("RGBA")
    a = out.getchannel("A")
    out.putalpha(Image.fromarray(np.minimum(np.asarray(a), np.asarray(mask))))
    return out


def save_rgba_crop(im: Image.Image, box: dict, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    im.crop((box["x0"], box["y0"], box["x1"], box["y1"])).save(out_path, "PNG")
