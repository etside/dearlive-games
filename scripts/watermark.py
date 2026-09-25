"""Watermark detection and removal (Phase 3.1).

Every source image carries a baked-in "Dreamina" mark in the bottom-right
region. Vectorising preserves it, so it would ship inside the SVG and be
visible in-game. Removal is non-negotiable.

Detection is geometric + colour-density, with OCR as confirmation only:

  primary   bottom-right region, last 20% of width x last 12% of height;
            a cluster of low-saturation grey pixels (S < 15 in HSV, 100 <= V <= 200)
            of at least MIN_CLUSTER px.
  confirm   tesseract on the region alone. Text found raises confidence but is
            NOT required -- OCR misses faint and partial marks, which is
            exactly the btn-auto case.

Removal is chosen by overlap, because a plain crop is only correct when the
mark sits outside the artwork:
  outside   tighten the crop so the region is excluded
  overlap   inpaint from the surrounding 30px median, feathered 8px

Never delete the source: output is a new file, and --keep-raw leaves a
reference copy beside the clean one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

# --- detection parameters (from the brief) ---
REGION_W_FRAC = 0.20      # last 20% of width
REGION_H_FRAC = 0.12      # last 12% of height
SAT_MAX = 15              # S < 15 in HSV (0-255 scale)
V_MIN, V_MAX = 100, 200   # 100 <= V <= 200
MIN_CLUSTER = 40          # pixels
# A text watermark is thin glyph strokes inside its own bounding box, so its
# pixels cover only a small fraction of that box. Artwork does the opposite:
# panel-wide assets (panel-pot, badge-hot) have a large low-saturation area in
# the bottom-right that fills its own box almost completely.
#
# The first attempt compared the box against the SEARCH REGION, which is wrong:
# on a 2048x2048 asset the real "Dreamina" mark is 373x215 inside a 409x245
# region, i.e. 80% of it, and the test rejected a genuine watermark.
MAX_SELF_FILL = 0.45      # cluster pixels / cluster box area
MAX_REGION_FILL = 0.60    # cluster pixels / region pixels
# Saturation and density are not sufficient: panel-pot and panel-balance still
# false-positive on the ornate gold frame in their bottom-right corner. The
# reliable discriminator is morphological. A watermark is THIN glyph strokes,
# so eroding the candidate mask 2px destroys it; a slab of artwork survives.
MAX_ERODE_SURVIVAL = 0.55  # eroded pixels / original pixels


def _erode2(mask: np.ndarray) -> np.ndarray:
    """Binary erosion by 2px, 4-neighbour, using shifted ANDs (no scipy)."""
    m = mask.copy()
    for _ in range(2):
        p = np.pad(m, 1, mode="constant", constant_values=False)
        m = (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] &
             p[1:-1, :-2] & p[1:-1, 2:])
    return m

# --- inpainting parameters ---
INPAINT_MEDIAN_PX = 30
FEATHER_PX = 8


@dataclass
class Mark:
    found: bool
    box: Optional[Tuple[int, int, int, int]]   # x0,y0,x1,y1 of the mark
    pixels: int
    ocr_text: str = ""
    confidence: str = "none"      # geometric | geometric+ocr

    @property
    def label(self) -> str:
        return (f"{self.pixels}px @ {self.box} "
                f"[{self.confidence}{' ' + self.ocr_text if self.ocr_text else ''}]")


def _rgb_to_hsv(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """HSV on 0-255 scales, so SAT_MAX/V_MIN/V_MAX read as written."""
    a = arr.astype(np.float32) / 255.0
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    diff = mx - mn
    s = np.zeros_like(mx)
    nz = diff > 1e-6
    s[nz] = diff[nz] / mx[nz]
    v = mx
    return s, v, diff


def detect_mark(im: Image.Image) -> Mark:
    """Locate the watermark in the bottom-right region."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    x0 = int(w * (1.0 - REGION_W_FRAC))
    y0 = int(h * (1.0 - REGION_H_FRAC))
    if x1_ok := (x0 >= w - 4 or y0 >= h - 4):
        # region too small to be meaningful
        pass
    region = np.asarray(rgb.crop((x0, y0, w, h)))
    if region.size == 0:
        return Mark(False, None, 0)

    s, v, _ = _rgb_to_hsv(region)
    cand = (s * 255.0 < SAT_MAX) & (v * 255.0 >= V_MIN) & (v * 255.0 <= V_MAX)
    n = int(cand.sum())
    if n < MIN_CLUSTER:
        return Mark(False, None, n)

    ys, xs = np.nonzero(cand)
    bx0, bx1 = int(xs.min()) + x0, int(xs.max()) + 1 + x0
    by0, by1 = int(ys.min()) + y0, int(ys.max()) + 1 + y0

    # A watermark is sparse text strokes, not a slab of artwork.
    region_px = float(region.shape[0] * region.shape[1])
    box_px = float(max(1, (bx1 - bx0) * (by1 - by0)))
    self_fill = n / box_px
    if self_fill > MAX_SELF_FILL:
        return Mark(False, None, n,
                    confidence=f"rejected:self-fill {self_fill:.2f}")
    if n / region_px > MAX_REGION_FILL:
        return Mark(False, None, n, confidence="rejected:fills-region")

    er = _erode2(cand)
    survival = int(er.sum()) / float(n)
    if survival > MAX_ERODE_SURVIVAL:
        return Mark(False, None, n,
                    confidence=f"rejected:solid (erode survival {survival:.2f})")

    # Pad slightly so anti-aliased glyph fringes go too.
    pad = 4
    box = (max(0, bx0 - pad), max(0, by0 - pad),
           min(w, bx1 + pad), min(h, by1 + pad))
    return Mark(True, box, n)


def ocr_region(im: Image.Image, box: Tuple[int, int, int, int]) -> str:
    """Confirmation only. Returns recognised text, or '' if unavailable."""
    x0, y0, x1, y1 = box
    if (x1 - x0) < 24 or (y1 - y0) < 10:
        return ""
    crop = im.convert("L").crop(box)
    # Upscale: tesseract is unreliable on small watermark glyphs.
    scale = 4
    crop = crop.resize(((x1 - x0) * scale, (y1 - y0) * scale), Image.LANCZOS)
    tmp = "/tmp/_wm_ocr.png"
    try:
        crop.save(tmp)
        out = subprocess.run(
            ["tesseract", tmp, "stdout", "--psm", "7"],
            capture_output=True, text=True, timeout=30)
        text = " ".join(out.stdout.split())
        return text if len(text) >= 4 else ""
    except Exception:
        return ""
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _content_bbox(rgba: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    a = np.asarray(rgba.getchannel("A"))
    if a.max() == 0:
        return None
    ys, xs = np.nonzero(a > 8)
    if ys.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def _feather(mask: np.ndarray, px: int) -> np.ndarray:
    """Soften a hard mask edge with a linear ramp, no external deps."""
    if px <= 0 or mask.size == 0:
        return mask.astype(np.float32)
    m = mask.astype(np.float32)
    h, w = m.shape
    ramp = np.ones((h, w), dtype=np.float32)
    for i in range(min(px, h)):
        a = (i + 1) / (px + 1.0)
        ramp[i, :] = np.minimum(ramp[i, :], a)
        ramp[h - 1 - i, :] = np.minimum(ramp[h - 1 - i, :], a)
    for i in range(min(px, w)):
        a = (i + 1) / (px + 1.0)
        ramp[:, i] = np.minimum(ramp[:, i], a)
        ramp[:, w - 1 - i] = np.minimum(ramp[:, w - 1 - i], a)
    return m * ramp


def inpaint(im: Image.Image, box: Tuple[int, int, int, int]) -> Image.Image:
    """Fill box with the median colour of a surrounding ring, feathered.

    Used when the mark overlaps the artwork (btn-auto): a crop would delete
    part of the pill, so the mark is painted out using the pill's own colour.
    """
    out = im.convert("RGBA").copy()
    arr = np.asarray(out).astype(np.float32)
    x0, y0, x1, y1 = box
    m = INPAINT_MEDIAN_PX
    rx0, ry0 = max(0, x0 - m), max(0, y0 - m)
    rx1, ry1 = min(arr.shape[1], x1 + m), min(arr.shape[0], y1 + m)
    ring_mask = np.ones((ry1 - ry0, rx1 - rx0), dtype=bool)
    ring_mask[y0 - ry0:y1 - ry0, x0 - rx0:x1 - rx0] = False
    ring = arr[ry0:ry1, rx0:rx1][ring_mask]
    if ring.size == 0:
        return out
    fill = np.median(ring, axis=0)

    sub = arr[y0:y1, x0:x1]
    f = _feather(np.ones(sub.shape[:2], dtype=bool), FEATHER_PX)[:, :, None]
    arr[y0:y1, x0:x1] = sub * (1.0 - f) + fill[None, None, :] * f
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def remove_watermark(im: Image.Image, ocr: bool = True,
                    keep_raw_to: Optional[str] = None) -> Tuple[Image.Image, Mark, str]:
    """Return (clean_image, mark, method). Never mutates the input."""
    im = im.convert("RGBA")
    mark = detect_mark(im)
    if not mark.found:
        return im, mark, "none"

    if ocr:
        mark.ocr_text = ocr_region(im, mark.box)
    mark.confidence = "geometric+ocr" if mark.ocr_text else "geometric"

    content = _content_bbox(im)
    overlaps = False
    if content:
        cx0, cy0, cx1, cy1 = content
        bx0, by0, bx1, by1 = mark.box
        overlaps = not (bx1 <= cx0 or bx0 >= cx1 or by1 <= cy0 or by0 >= cy1)

    if keep_raw_to and overlaps:
        os.makedirs(os.path.dirname(keep_raw_to), exist_ok=True)
        im.save(keep_raw_to)

    if overlaps:
        clean = inpaint(im, mark.box)
        method = f"inpaint(median{INPAINT_MEDIAN_PX}px,feather{FEATHER_PX}px)"
    else:
        clean = im
        method = "crop(excluded by content bbox)"

    # Verify: the mark must be gone (or reduced below the cluster floor).
    after = detect_mark(clean)
    if overlaps and after.found and after.pixels >= MIN_CLUSTER:
        # Inpaint did not clear it; fall back to a hard fill of the box.
        arr = np.asarray(clean).copy()
        bx0, by0, bx1, by1 = mark.box
        sub = arr[by0:by1, bx0:bx1].astype(np.float32)
        ring = arr[max(0, by0 - 30):by1 + 30, max(0, bx0 - 30):bx1 + 30]
        if ring.size:
            arr[by0:by1, bx0:bx1] = np.median(ring.reshape(-1, 4), axis=0).astype(np.uint8)
        clean = Image.fromarray(arr, "RGBA")
        method += "+hardfill"
    return clean, mark, method
