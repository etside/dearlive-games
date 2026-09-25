"""Phase 3 asset manifest: every asset, its category, and its target size.

Category drives the target box (from the brief):
    icon/chip/button-round/avatar  512x512
    card-face/card-back           500x700  (5:7)
    badge-pill/button-pill/panel-wide  1024x256 (4:1)
    frame-ring/chair              1024x1024

Resolved duplicates are already excluded here; see docs/duplicate-decisions.md.
`kind` is "individual" (straight from the source PNG) or "split" (Phase 2 crop).
"""

SOURCE = "/storage/emulated/0/11labd/Assets-all"
GAME = "teen-patti-pro"

# (asset_id, category, kind, source_ref, note)
ASSETS = [
    # ---- chairs / seats -------------------------------------------------
    ("seat-red",             "chair",        "individual", "1790363227097.png", ""),
    ("seat-blue",            "chair",        "individual", "1790363286305.png", ""),
    ("seat-green",           "chair",        "individual", "1790363487752.png", ""),

    # ---- cards ----------------------------------------------------------
    ("card-back-teenpatti",  "card-back",    "individual", "1790363545846.png",
     "1760x2464 = 5:7 exactly"),
    ("card-face-template",   "card-face",    "individual", "1790364032350.png",
     "Phase 7 input: blank face with corner rank boxes"),
    ("suit-spade",           "icon",         "split",      "frames/suit-spade-gold.png",
     "kept over flat individual; see duplicate-decisions Pair 2"),
    ("suit-heart",           "icon",         "individual", "1790364081341.png", ""),
    ("suit-diamond",         "icon",         "individual", "1790364091888.png", ""),
    ("suit-club",            "icon",         "individual",
     "file_00000000991882088c05e573d77d512d.png", "only true-RGBA source file"),

    # ---- chips ----------------------------------------------------------
    ("chip-20",              "chip",         "individual", "1790363644734.png", ""),
    ("chip-100",             "chip",         "individual", "1790363648337.png", ""),
    ("chip-500",             "chip",         "individual", "1790363653670.png", ""),
    ("chip-1k",              "chip",         "individual", "1790363660266.png", ""),

    # ---- panels ---------------------------------------------------------
    ("panel-pot",            "panel-wide",   "individual", "1790363906438.png", ""),
    ("panel-you",            "panel-wide",   "individual", "1790363913031.png", ""),
    ("panel-round-room",     "panel-wide",   "individual", "1790363899018.png", ""),
    ("panel-balance",        "panel-wide",   "individual", "1790366066962.png", ""),

    # ---- avatars --------------------------------------------------------
    ("avatar-placeholder",   "avatar",       "individual", "1790364768421.png", ""),
    ("avatar-frame-navy",    "frame-ring",   "individual", "1790364763457.png",
     "from ring trio; navy pairs with navy UI"),
    ("status-online",        "badge-pill",   "individual", "1790363835245.png", ""),
    ("status-offline",       "badge-pill",   "individual", "1790363893478.png",
     "kept for contrast 71.29; see duplicate-decisions Pair 1"),

    # ---- ui -------------------------------------------------------------
    ("btn-back",             "button-round", "individual", "1790363940210.png", ""),
    ("btn-help",             "button-round", "individual", "1790363943631.png", ""),
    ("btn-settings",         "button-round", "individual", "1790364685478.png", ""),
    ("badge-hot",            "badge-pill",   "individual", "1790363889846.png", ""),
    ("badge-you",            "badge-pill",   "individual", "1790364839407.png", ""),
    ("badge-crown",          "icon",         "individual", "1790364239319.png", ""),
    ("banner-winner",        "panel-wide",   "individual", "1790363792241.png",
     "dark grey background (88,88,90)"),
    ("timer-ring",           "frame-ring",   "individual", "1790364565360.png",
     "hollow dark centre for numerals"),
    ("decorative-ring",      "frame-ring",   "individual", "1790364718733.png", ""),

    # ---- split sheets ---------------------------------------------------
    ("btn-repeat",           "button-pill",  "split", "action-buttons/btn-repeat.png",
     "beige wedge top-left, fixed in Phase 3.3"),
    ("btn-history",          "button-pill",  "split", "action-buttons/btn-history.png", ""),
    ("btn-auto",             "button-pill",  "split", "action-buttons/btn-auto.png",
     "watermark overlaps the pill"),
    ("btn-sound-on",         "button-round", "split", "sound-buttons/btn-sound-on.png", ""),
    ("btn-sound-off",        "button-round", "split", "sound-buttons/btn-sound-off.png", ""),
    ("frame-ring-gold-sm",   "frame-ring",   "split", "frames/frame-ring-gold-sm.png", ""),
    ("status-waiting",       "badge-pill",   "split", "status-badges/status-waiting.png",
     "mild edge erosion, accepted"),
    ("status-betting-open",  "badge-pill",   "split", "status-badges/status-betting-open.png", ""),
    ("status-betting-closed","badge-pill",   "split", "status-badges/status-betting-closed.png", ""),
    ("status-dealing",       "badge-pill",   "split", "status-badges/status-dealing.png", ""),
    ("status-result",        "badge-pill",   "split", "status-badges/status-result.png", ""),
]

# --- assets Phase 6 generates from badge-you (not from source) -------------
GENERATED = [
    ("badge-player", "badge-pill", "recolour badge-you gold -> blue, text set at runtime"),
    ("badge-new",    "badge-pill", "pill from badge-you, green gradient, text NEW"),
]

# TODO (deferred per brief): shared/nav/* Home, Live, Party, Games, Wallet,
# Messages, Profile — arrive with host-app integration, not needed for the
# Teen Patti game screen. No placeholder directories are created.

CATEGORY_SIZES = {
    "icon":         (512, 512),
    "chip":         (512, 512),
    "button-round": (512, 512),
    "avatar":       (512, 512),
    "card-face":    (500, 700),
    "card-back":    (500, 700),
    "badge-pill":   (1024, 256),
    "button-pill":  (1024, 256),
    "panel-wide":   (1024, 256),
    "frame-ring":   (1024, 1024),
    "chair":        (1024, 1024),
}

# Explicitly dropped, with reason. Copied to working/dropped/ by Phase 3, never
# deleted from the source directory.
DROPPED = {
    "1790364882531.png": "low contrast (28.95 vs 71.29) - Pair 1",
    "1790364036324.png": "flat greyscale spade, sat 1.5 - Pair 2",
    "1790364140236.png": "square 2048x2048, low contrast, unbranded - Pair 4",
}


def asset_ids():
    return [a[0] for a in ASSETS]


# ---------------------------------------------------------------------------
# Watermark ground truth (Phase 3.1)
# ---------------------------------------------------------------------------
# The mark is NOT "Dreamina" -- it is the Chinese text "豆包 AI 生成"
# (ByteDance Doubao). Two consequences, both measured rather than assumed:
#
#  * OCR cannot detect it. tesseract here has only `eng` and `osd`; there is no
#    chi_sim and the mark is Chinese. OCR returned garbage on real marks and
#    nothing at all on three others, so the brief's "OCR as confirmation" is
#    simply unavailable in this environment.
#  * The mark is a LIGHT EMBOSSED text (near-white fill, grey outline), so on a
#    near-white background the detectable signature is the outline, not the fill.
#
# The geometric detector alone is not reliable enough to ship. Measured against
# this ground truth it produced 4 false positives (panel-pot, panel-balance,
# panel-you, banner-winner -- ornate frames whose bottom-right is legitimately
# thin mid-grey) and, once an erosion test was added to fight those, 2 false
# negatives (card-back, btn-repeat). So the split below is VISUALLY VERIFIED
# from contact sheets rather than inferred, and the detector is only used to
# locate the mark inside assets already known to carry one.
WATERMARKED = {
    "seat-red", "seat-blue", "seat-green",
    "card-back-teenpatti", "card-face-template",
    "suit-spade", "suit-heart", "suit-diamond",
    "chip-20", "chip-100", "chip-500", "chip-1k",
    "panel-pot", "panel-you", "panel-round-room", "panel-balance",
    "avatar-placeholder", "avatar-frame-navy",
    "status-online", "status-offline",
    "btn-back", "btn-help", "btn-settings",
    "badge-hot", "badge-you", "badge-crown",
    "banner-winner", "timer-ring", "decorative-ring",
    "btn-auto", "btn-sound-off",
}
# Verified CLEAN at a 0.58w x 0.70h crop, i.e. wider than the search region.
# Phase 2's grid/manifest bands fell outside the overlay on these.
WATERMARK_CLEAN = {
    "suit-club", "btn-repeat", "btn-history", "btn-sound-on",
    "frame-ring-gold-sm", "status-waiting", "status-betting-open",
    "status-betting-closed", "status-dealing", "status-result",
}
assert WATERMARKED | WATERMARK_CLEAN == set(asset_ids()), \
    "watermark ground truth does not cover the manifest"
assert not (WATERMARKED & WATERMARK_CLEAN)
