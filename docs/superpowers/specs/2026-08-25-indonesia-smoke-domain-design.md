# IndonesiaSmoke — all-Indonesia domain, design

Date: 2026-08-25
Status: approved, not yet implemented
Supersedes: nothing. PLAN.md scope decisions stand except where noted below.

## Goal

Expand the live smoke map from the Kalimantan-and-neighbours domain
(100–120°E, 5°S–8°N) to all of Indonesia plus the countries the haze actually
reaches, and publish it under the name **IndonesiaSmoke** — without
interrupting the map currently live at
<https://achmadrpahlevi.github.io/KalimantanWildfires/> at any point.

The existing repository keeps running, untouched, until the new one has been
validated against an independent sensor in each major region. Cutover is a
deliberate, separate step.

## Non-goals

- No change to the physics of the smoke mask. Thresholds stay as calibrated.
- No night-time product. Daylight-only remains the design (PLAN.md §1).
- No widening of the calibrated sun-angle range. `MIN_SCENE_ELEVATION_DEG`
  stays at 40; what changes is that it is applied per pixel rather than to the
  scene as a whole.
- No new data sources.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Migration | New repo `IndonesiaSmoke`, both sites live in parallel | Zero risk to the live map. Renaming would move the live URL the moment it happened. |
| Extent | 94.5–142.0°E, 11.5°S–8.0°N | All of Indonesia, Sabang to Merauke and Rote to Miangas, plus Singapore, Peninsular Malaysia, Sarawak, Sabah, Brunei, Timor-Leste and the PNG border. Keeps the transboundary question the current map was built to answer. |
| Resolution | Unchanged, 0.02° (~2.2 km) | `SMOKE_MIN_BLOB_CELLS`, Farnebäck `winsize` and `FLOW_SMOOTH_SIGMA` are all expressed in grid cells. Changing `GRID_RES_DEG` silently recalibrates every one of them on top of an already-large change. |
| Sun gating | Per pixel, not per scene | A single domain-wide gate over 47° of longitude either withholds Papua's good morning or publishes Sumatra's uncalibrated one. |
| Displayed time | UTC | One clock for three Indonesian time zones. Local equivalents shown as a secondary line. |

## 1. Domain and grid

In `pipeline/config.py`:

```python
LON_MIN, LON_MAX = 94.5, 142.0
LAT_MIN, LAT_MAX = -11.5, 8.0
# GRID_NX 2375, GRID_NY 975 — 2.32M cells, 3.6x the current 650k
```

Cost, measured rather than assumed:

- **Download.** `fetch_ahi.segments_for_bbox` computes segments from the
  bounding box and already returns `[4,5,6,7]` today. At the new extent it
  returns `[4,5,6,7,8]`: 35 files instead of 28, roughly 300 MB instead of
  240 MB. AHI segments divide the disk by scan line, so the 47° of extra
  longitude is free; only the southward extension to 11.5°S buys a segment.
- **Compute.** The mask, advect and publish stages each take 1–2 s today
  against an 80 s fetch. At 3.6x the cells they are still seconds. The job
  stays download-bound, at roughly 3 minutes against 2 today.
- **Page weight.** The published set is about 1.1 MB now (seven PNGs plus the
  FIRMS GeoJSON) and becomes roughly 3–3.5 MB. Accepted for now; PNG palette
  quantisation is the cheap lever if it becomes a problem.

Other changes in the same file:

- `AHI_FALLBACK_SEGMENTS`: `[5, 6, 7]` becomes `[4, 5, 6, 7, 8]`. The live path
  computes segments properly; only this fallback is stale.
- `RESAMPLE_RADIUS_M`: 5000 becomes 8000. At 94.5°E the viewing zenith angle is
  about 60°, so a 2 km nadir pixel is stretched past 5 km and a 5 km
  nearest-neighbour radius leaves holes along the western edge. **This must be
  verified on a real scene, not assumed**: count the NaN fraction west of 97°E
  before and after.
- `CITIES`: the eleven current entries become a national set — Jakarta, Medan,
  Palembang, Pekanbaru, Surabaya, Makassar, Manado, Ambon, Jayapura, Kupang,
  plus the existing Pontianak, Palangkaraya, Banjarmasin, Samarinda and
  Balikpapan — with Singapore, Kuala Lumpur, Kuching and Bandar Seri Begawan
  kept as the downwind callouts.
- `validate.REGIONS`: add Java, Sulawesi, Maluku and Papua alongside the
  existing Kalimantan, Sumatra and Malacca Strait.

### A deliberate design that gets deleted

`common.view_bounds()` currently mirrors the data bounds about `FOCUS_LON` so
that Borneo stays centred after the westward extension to 100°E. On a
full-country domain that mirroring produces an opening view over 190° wide. It
collapses to `leaflet_bounds()`, and `FOCUS_LON`/`FOCUS_LAT` survive only as
the map's initial centre. The docstring explaining the mirroring goes with it —
the reason it existed no longer applies.

## 2. Per-pixel sun gating

Today `common.domain_is_daylit(dt)` returns a single boolean for the whole
domain: publish if at least 50% of it sits above `MIN_SCENE_ELEVATION_DEG`.
Over 47° of longitude — 3.1 hours of solar time — that gate would leave a
publishing window of roughly four hours, discarding both Papua's morning and
Sumatra's afternoon.

New in `pipeline/common.py`:

```python
def calibrated_mask(dt) -> np.ndarray:
    """Per-pixel: is this pixel inside the calibrated sun-angle range?"""
    return solar_elevation(dt, lat2d, lon2d) >= C.MIN_SCENE_ELEVATION_DEG
```

`domain_is_daylit` keeps its `(bool, float)` signature so the freeze machinery
in `publish.py` needs no restructuring, but its boolean changes from
`frac_lit >= 0.5` to `frac_lit >= C.MIN_CALIBRATED_FRACTION`, a new constant
defaulting to 0.05. A scene publishes when any usable part of the country is in
window.

The resulting day is an outcome of the physics, not a hardcoded clock. With
`MIN_SCENE_ELEVATION_DEG` at 40, Papua enters the window around 23:15 UTC
(08:15 WIT) and Sumatra leaves it around 09:00 UTC (16:00 WIB) — close to the
23:30–07:30 UTC target, and still tunable through the single existing constant.

In `pipeline/smoke_mask.py`, uncalibrated pixels fold into `obscured`, so
nothing outside the calibration range is ever advected. This preserves PLAN.md
non-negotiable #2. They are additionally published as their own layer so the
page can hatch "sun angle outside calibration" distinctly from "cloud" — two
different reasons to distrust a pixel should not look alike.

### The headline number changes meaning

`smoke_fraction` is `smoke.sum() / n` over the whole domain. With most of the
country out of window at any instant, that number collapses toward zero and is
no longer comparable to anything. It becomes a fraction of the
calibrated-and-unobscured area and is **renamed in `meta.json` to
`smoke_fraction_of_visible`**, so that nobody compares it to the 5–11%
reference values in OPERATIONS.md without noticing the denominator moved.
OPERATIONS.md's health check and reference tables are updated in the same
change.

### New failure mode: the calibration footprint moves

The calibrated region sweeps east to west across the day. At its leading and
trailing edges, smoke appears and disappears for reasons that have nothing to
do with wind, and Farnebäck reads that as motion — a false flow field at the
exact edges of the map, every cycle.

Mitigation: compute optical flow only where **both** frames are calibrated,
then erode that region by approximately `winsize / 2` (16 cells at
`winsize=31`) before the flow call. The existing `MAX_PLAUSIBLE_SPEED_MS`
rejection stays as the outer guard.

This is untested territory and gets a regression test that constructs two
synthetic frames differing only by footprint edge and asserts the derived flow
is null there.

## 3. Time and page

`DISPLAY_TZ_OFFSET_HOURS` goes 7 to 0 and `DISPLAY_TZ_LABEL` goes `"WIB"` to
`"UTC"`. `common.to_display_tz` is a single seam, so every timestamp in
`publish.py`, `smoke_mask.py` and `validate.py` follows without further change.

`MIN_SCENE_LOCAL_HOUR` stays at 0.0 (disabled). Its comment must note that the
valve is now interpreted in UTC, not WIB, should anyone reach for it.

The page header shows UTC as the primary timestamp with a secondary line giving
all three Indonesian equivalents:

```
Scene 2026-08-25 04:30 UTC
= 11:30 WIB · 12:30 WITA · 13:30 WIT
```

`site/index.html` changes: `<title>`, meta description, the map's `aria-label`,
the `#title` element, and the initial view. `STALE_MINUTES` stays at 90.

`CAVEAT_LOW_SUN` is deleted rather than rewritten. It asserts that "Kalimantan
is the calibrated part of this map", which the per-pixel hatch now shows
directly. `CAVEAT_WATER` and `WATER_NOTE_ABOVE_FRACTION` are unchanged.

## 4. Repository and cutover

Create `IndonesiaSmoke` and push the **full history**, not a fresh
initialisation. The commit messages carry reasoning that OPERATIONS.md actively
refers back to — "the culprit was the water test, not the geometry" — and a
clean tree is not worth losing it.

- Environment prefixes `KALIMSMOKE_WORK` / `_STATE` / `_OUT` become
  `INDOSMOKE_*`. They are used only in CI, so no compatibility shim.
- Workflow identity `kalimsmoke-bot` becomes `indosmoke-bot`; the concurrency
  group and cache keys likewise.
- `FIRMS_MAP_KEY` must be added as a secret to the new repository **before**
  the first run. Without it the hotspot layer falls back to cache and looks
  plausible while being wrong.
- Cron moves to `17,47 * * * *`. Two repositories on one FIRMS key firing in
  the same minute is a rate-limit collision. The odd-minute reasoning in
  OPERATIONS.md — GitHub silently drops scheduled runs queued at the top of the
  hour — continues to apply.
- GitHub Pages enabled on `gh-pages`.

`KalimantanWildfires` is not modified at all until cutover is called. Cutover
is then two steps: replace its `site/index.html` with a redirect and a short
notice, and disable its schedule. GitHub keeps resolving the existing URL, so
the link in `linkedin_post.txt` continues to work.

## 5. Hotspot density

`FIRMS_MIN_FRP_MW` stays at 50 for the first runs. The all-Indonesia domain
will return far more than the 302 points measured over the current domain, but
the right response is to measure the new count and then decide. Raising the
floor preferentially discards smouldering peat, which produces the most smoke
per unit of heat — it is a readability control, not a relevance one.

## 6. Tests and the acceptance gate

The 82 existing tests are **updated, not deleted**. Several pin reasoning
rather than behaviour — the polar-segment assertion, the water-test regressions
— and a test that stops asserting the fix it was written for is worse than no
test at all.

New coverage:

- `calibrated_mask` returns the grid shape and agrees with `solar_elevation` at
  the threshold.
- Flow is null across a synthetic calibration-footprint edge (§2).
- `smoke_fraction_of_visible` uses the calibrated, unobscured denominator.
- Timestamps render as UTC.
- `segments_for_bbox` returns `[4, 5, 6, 7, 8]` at the new extent.
- `view_bounds() == leaflet_bounds()`.

### Acceptance gate before cutover

Run `python -m pipeline.validate` on scenes covering **Sumatra, Kalimantan,
Sulawesi and Papua**, and require FIRMS hotspot enrichment above 3x in each.

This is not optional. Kalimantan at 14:00 WIB is the only point these
thresholds have ever been cross-checked against an independent sensor.
Everything east of Borneo is unvalidated, and the name IndonesiaSmoke claims
otherwise.

## 7. New known limits, for the README

- **Scattering geometry varies far more than before.** Papua sits at the
  sub-satellite point (viewing zenith ~0°), Borneo at ~26°, Sabang at ~60°.
  Every threshold in `config.py` was tuned at Borneo's geometry.
- **Sun glint near nadir.** The morning water-test fix
  (`WATER_SMOKE_B03_MIN = 14`) was derived for Borneo morning geometry, where
  sun and sensor share a side at ~26° off nadir. It has never been tested over
  the Banda and Arafura seas with the satellite directly overhead. Expect this
  to need work.
- **Java, Bali and Nusa Tenggara.** Dry-season bare soil and volcanic terrain
  are false-positive sources never tested by this pipeline.
- The existing limits — shallow coastal water, West Kalimantan extent,
  sun-angle drift inside the window, inactive wind arrows — carry over
  unchanged.

## Open questions

None blocking. `RESAMPLE_RADIUS_M = 8000` is a calculated starting value that
the first real scene will confirm or correct.
