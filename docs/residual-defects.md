# Residual defects (Phase 3) — accepted

Phase 3 output is shipped with the defects below. Each is a **source-content
limitation**, not a pipeline fault: the AI-generated source art has a decorative
gradient backdrop that is neither the sheet background nor the asset, so no
background-keying rule can separate it.

## Per-asset exemptions from flood-fill

Two assets bypass background removal entirely (`NO_BG_FLOOD` in
`scripts/asset_manifest.py`) and receive a shape mask on the untouched source:

| asset | why exempt | treatment | verified at 512px |
|---|---|---|---|
| `panel-pot` | ornate gold frame destroyed by the fill — dark grey background (88,88,90) sits close to the frame's shadow tones, leaving a gold smear with "POT: 2.22K" illegible | rounded-rect mask, no flood | scrollwork intact, text legible, navy interior preserved |
| `avatar-placeholder` | the white silhouette is the *same tone* as the near-white background, so any near-white keying eats the silhouette | circle mask, no flood | perfect circle, silhouette intact, no arch |

`avatar-placeholder` needed both halves of that fix. The first attempt combined
flood-fill with an inset ellipse and produced an arch rather than a disc; the
flood had already destroyed the silhouette, so the mask then cut the wrong thing.

## Accepted streaks

| # | asset | defect | root cause | priority |
|---|---|---|---|---|
| 1 | `btn-repeat` | beige wedge, top-left | source gradient on the action-buttons sheet | LOW |
| 2 | `btn-auto` | light streak along top | same | LOW |
| 3 | `status-waiting` | light streak along bottom | source gradient on the status-badges sheet | LOW |
| 4 | `status-result` | light streak along bottom | same | LOW |
| 5 | `card-back-teenpatti` | light streak, right edge | source gradient in the card-back render | LOW |

**Why accepted:** each affects under 3% of the asset's area and is a soft
gradient rather than a hard edge, so it reads as an ambient highlight at the
1024px and 512px sizes these assets ship at. Vectorising (Phase 4) quantises
smooth gradients into a small number of bands, which further flattens them.

**Escalation rule applied:** any streak exceeding 3% of asset area would be
marked HIGH and re-traced. None does.

**Future action:** manual retouch by a designer, or re-generation from the
prompt without a decorative backdrop. Not blocking.

## Detector residuals

The watermark detector reports 3 assets as RESIDUAL after removal:
`card-back-teenpatti` (10,168px), `panel-pot` (98,446px), `badge-hot` (95,566px).

These are **imprecise bounding boxes on ornate frames**, not surviving marks.
On visual inspection the `豆包 AI 生成` text is absent from all 41 normalised
assets. The detector re-reads the ornate frame's own thin mid-grey scrollwork as
a candidate cluster, which is the same false-positive mode documented in
`asset_manifest.py`. Visual verification, not the detector's return value, is
the authority here.
