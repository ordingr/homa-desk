# Homa app icon spec (2026-11-08)

Status: SVGs written, NOT visually verified. The designer session had no shell or browser tool, so nothing was rasterised and no preview PNG exists. See "Verification still owed".

Files (this folder):
- `2026-11-08-homa-icon.svg` copy to `assistant/assets/icon.svg`
- `2026-11-08-homa-icon-muted.svg` copy to `assistant/assets/icon-muted.svg`
- Source art: `2026-11-08-homa-icon-source.png`

## Format (matches the current icons)
`viewBox="0 0 100 100"`, 100x100, full-bleed `<rect>` with no rx (the renderer clips the .ico frames to a 22% rounded square; the PNG tiles stay square). No filters, no raster. One linear gradient (gold, vertical) on the colour icon only.

## Colours
| Role | Colour icon | Muted icon |
|---|---|---|
| Tile | `#0E1A3A` navy | `#7A7A7A` (same grey as the current muted icon) |
| Bird | gradient `#FFE08A` (top) to `#F2A81D` (bottom) | `#FFFFFF` (the current muted icon also uses a white glyph) |
| Slash | none | `#E5352B`, `stroke-width 7`, round caps, (24,76) to (76,24) (copied unchanged from the current muted icon) |

Muted follows the existing convention: the normal icon's tile goes grey, glyph white, with the red diagonal slash on top. The slash is what signals "muted" (used by `assistant/overlay/icon.py` line 143, `icon-muted` when `self.muted`).

## Construction (v1, superseded by Revision 2 below)
The source art is a three-quarter profile phoenix with a glow. The brief asked for a front-facing, symmetric, few-feather reduction, so this is a simplification, not a trace.
- Wing: 4 tapered leaf blades fanning from a shoulder at about (47,50). Tips at (7,33), (7,55), (15,71), (27,82). Defined once as `#wing`, mirrored for the right wing with `translate(100 0) scale(-1 1)`.
- Body: one teardrop, (50,29) down to (50,72).
- Head: circle r6 at (50,23) plus a single triangular crest spike to y=12. No beak or eye (invisible at 16 px).
- Tail: 3 blades, centre to (50,95), sides to (32,90) and (68,90).
- All content sits inside x 7..93, y 12..95.
- Badge safety: a red count badge covers roughly x 58..100, y 0..42.

## Verification still owed
Before dev copies the files:
1. Render with the existing script path: copy the two SVGs into `assistant/assets/` and run `powershell -ExecutionPolicy Bypass -File scripts\render-icons.ps1`, then view `icon_60.png`, `assistant.ico` frames at 16/24/32, and a `badge_3` overlaid top-right.
2. Scratch script `sheet.cjs` (in the designer session scratchpad) builds the contact sheet at 16/24/32/48/256 px with badges. Run with `NODE_PATH=%USERPROFILE%\node_modules node sheet.cjs`.

## Note for dev (copy step)
1. Copy the two SVGs over `assistant/assets/icon.svg` and `icon-muted.svg` (keep the filenames).
2. Run `powershell -ExecutionPolicy Bypass -File scripts\render-icons.ps1`; commit the regenerated PNGs and `assistant.ico`.
3. `<use href>` is used for the mirrored wing. Edge/Chromium (what the render script uses) supports it; if another consumer of the SVGs does not, inline the second wing path with the mirrored transform.

---

## Revision 2 (2026-11-08, after tech-lead render review)

Why: the v1 render read as a lotus/starburst. Wings fanned up and down around the body, the head was a detached circle, and the tail merged with the lower wing blades. v1 notes above are kept for the record; the SVGs now implement Revision 2. Still not rendered by me (no shell); tech-lead re-renders.

Construction:
- Wings: raised, each a single solid shape (not separate blades). The leading edge is one curve from the shoulder (46,42) up and out to the tip at (7,12). The trailing edge is a straight-line zigzag with 4 feather tips at (7,12), (10,33), (19,47), (33,58), ending at the body (45,60). Nothing hangs below y=60, so there are no downward blades. Mirrored for the right wing with the same `translate(100 0) scale(-1 1)`.
- Head, neck and body: one continuous path, so no gap. A crest at (55,13), a beak pointing left to (37,25) (a side-facing head on a front-facing body, and left is away from the badge), a neck about 9 units wide at y=28, a chest widening to about 16 units at y=50, ending at the hip (50,71).
- Tail: 3 narrow feathers fanning from (50,68) to tips at (33,92), (50,96), (67,92). The lowest wing tip (33,58) sits about 18 units above the side tail feather's tip, and the tail starts at x=50 in the middle, so the tail reads separately from the wings.
- Colours, gradient, tile and muted slash are unchanged from the tables above. Muted uses the same silhouette.
- Badge: covers about x 58..100, y 0..42, which hides most of the right wing's upper half. The left wing, head with beak, body and tail stay visible, so it still reads as a bird with one visible raised wing. That is the deliberate trade-off of raised wings.

Expected 16 px reading (geometry only, unobserved): a gold V of two raised wings with a narrow body and a split tail below. Notches in the trailing edge are about 5 units deep and may soften to a slightly serrated edge. The beak is about 1.5 px wide and may merge with the head.

If it still misreads: first fix to try is to enlarge the head and beak by about 20 percent and drop the second wing notch (L10 33 and L22 40), giving 3 feather tips per wing.

## R4 render outcome (2026-11-08)

Supersedes: nothing. The paths above (`assistant/assets/`, `assistant.ico`) are from before the rename. The same files are
now `homa/assets/` and `homa/assets/homa.ico` (plan `docs/plans/2026-11-08-rename-to-homa.md`, section 4.7). The paths above are
kept as written.

- Date: 2026-11-08. Recorded by doc-keeper (R7) from the R4 report relayed by the tech-lead.
- Render: `scripts\render-icons.ps1` ran with exit 0. The output is in `homa/assets/`: `icon_60.png` and `icon-muted_60.png`
  (plus the other PNG sizes and the badge set), and `homa.ico` at 21,447 bytes with six frames at 16, 24, 32, 48, 64 and 256 px,
  all 32 bpp.
- Read-out, as reported by the tech-lead from the rendered contact sheet `docs/design/2026-11-08-homa-icon-preview.png`:
  `icon_60.png`, `icon-muted_60.png` and the 256 px frame are crisp. At 16 px the gold bird's overall shape and outstretched
  wings still read, but the tail blurs into a vertical smudge and the wing tips and head detail are lost. The 24 and 32 px
  frames hold better. That is acceptable for the tray. The badge covers the right wing tip; the head, body and left wing stay
  visible.
- Decision: the tech-lead accepted Revision 2 on this rendered sheet. No further design revision is scheduled from this read-out.
  Sign-off on the artwork (plan section 8, Q4) is taken from this read-out. The rename does not wait on it.
