# KalimSmoke

Live dashboard for Kalimantan wildfire smoke: current smoke extent from
Himawari-9, hotspots from NASA FIRMS, and a 0–3 h forward advection of the
smoke field.

Fills the gap between BMKG (current imagery only) and ASMC (daily narrative
outlook): **hourly, gridded, short-range smoke movement with a time slider.**

> Indicative short-range smoke movement, daytime only. Not an official
> forecast — see [BMKG](https://www.bmkg.go.id/) and [ASMC](http://asmc.asean.org/).

Not a research product. No novelty claims, no validation study. See
[PLAN.md](PLAN.md) for the scope decisions and what was deliberately left out.

## How it works

```
every 30 min (GitHub Actions)
  fetch_ahi    latest Himawari-9 FLDK scene from the public noaa-himawari9
               bucket → only the band/segment files touching 100–120°E,
               5°S–8°N → satpy → fixed 0.02° plate carrée grid → state/*.npz
  smoke_mask   B01/B03 reflectance + B03−B06 SWIR contrast + B11−B14
               split-window → smoke density, cloud "obscured", clear
  advect       Farnebäck optical flow between the last two masks →
               semi-Lagrangian integration to +180 min in 30-min steps
  fetch_firms  FIRMS area API, last 24 h VIIRS + MODIS → GeoJSON
  fetch_gfs    (stretch) GFS 850 hPa wind → arrow GeoJSON
  publish      RGBA overlay PNGs + meta.json → force-pushed to gh-pages
```

The Leaflet page reads `meta.json` and swaps image overlays as you move the
time slider.

### Domain

100–120°E, 5°S–8°N, on a fixed 0.02° (~2.2 km) grid — 1000 × 650 cells.

It reaches west to Singapore, Peninsular Malaysia and the Riau fire belt on
purpose: transboundary haze is the question people actually ask, and a grid
that stops at Borneo cannot answer it. The extra width is close to free,
because AHI splits the disk into segments by scan line, so a wider longitude
range is a bigger crop of files already being downloaded — same 28 files,
same ~240 MB.

The map opens centred on Kalimantan (`FOCUS_LON`/`FOCUS_LAT` in `config.py`)
rather than on the middle of the grid, which after the westward extension is
the Java Sea. `common.view_bounds()` mirrors the data bounds about that focus
so the subject stays centred with Singapore and Malaysia visible to its west.

Caveat on the western edge: at 100°E the satellite viewing zenith angle is
roughly 50–55°, so pixels are stretched to 3–4 km and the atmospheric path is
longer than over Borneo. Mask thresholds were tuned over Borneo; treat smoke
detections near the far west as less reliable than those over Kalimantan.

### How this compares to BMKG and ASMC

Checked against both, 2026-08-21/22, rather than assumed.

The comparison that matters is against BMKG's **Citra Sebaran Asap**, their
dedicated smoke product — *not* their Himawari IR/Water Vapour imagery, which
is 10-minute weather imagery and not a smoke product at all.

<https://www.bmkg.go.id/cuaca/satelit/citra-sebaran-asap>

| | BMKG *Citra Sebaran Asap* | ASMC | KalimSmoke |
|---|---|---|---|
| Source | Himawari-9 smoke RGB | NOAA-20 / SNPP (polar) | Himawari-9 |
| Cadence | **1 image per day**, 16:00 WIB | ~1 overpass/day per region | **30 min** |
| Hours | daytime only (visible bands) | 1 daytime pass | daytime only, 09:30-14:00 WIB |
| Coverage | all Indonesia | ASEAN, by region | 100-120E, 5S-8N |
| Smoke | **polygons** + written analysis | visible in false colour, not delineated | **gridded 2 km density** |
| Wind | 1000 hPa vectors overlaid | - | (stretch, GFS 850 hPa) |
| Fire | Geohotspot (Himawari IR) | VIIRS counts by confidence | FIRMS VIIRS+MODIS, 375 m |
| **Forward projection** | direction of travel, in words | daily narrative outlook | **0-3 h gridded, 30-min steps** |
| Status | official, mandated | official, mandated | unvalidated side project |

BMKG hits the same wall we do, for the same reason, and says so: *"Produk ini
hanya tersedia pada siang hingga sore hari"* - visible-band RGB, so daytime
only. Our daylight-only design is not a shortcut; it is what this measurement
is.

Where they are better:

- **Authority and interpretation.** BMKG ships a written analysis naming
  provinces and directions of travel, by meteorologists who know the region.
  ASMC adds regional hotspot counts by confidence and formal haze warnings.
  Neither is replaceable by a threshold on reflectance.
- **ASMC's VIIRS counts** are the right independent check on our FIRMS layer -
  667 for Kalimantan against 10 for Sarawak on 2026-08-21.
- BMKG publishes at 16:00 WIB, a sun elevation of about 26 degrees, which our
  own calibration refuses. Either their RGB method is more robust at low sun
  than our thresholds, or they accept contamination we chose not to. Worth
  understanding before widening our window.

What is actually ours:

- **Cadence.** Nine frames a day against their one. For a field moving at a
  few m/s, a daily snapshot cannot show movement. That is the gap PLAN.md
  aimed at, and it is wider than it first appeared.
- **A gridded, scrubbable field** rather than polygons plus prose.
- Honest uncertainty on the face of the map: cloud hatching, staleness banner,
  a forecast that suppresses itself when too little is visible.

### Checked against BMKG, 2026-08-21

BMKG's 16:00 WIB analysis versus our 14:00 WIB run:

| BMKG says | we say | verdict |
|---|---|---|
| Smoke moving **Barat Laut-Timur Laut** (NW-NE) | flow **from 167 deg**, i.e. toward ~347 deg (NNW) | **match** |
| Wind from **Tenggara** (SE) | 167 deg is SSE | **match** |
| *Transboundary Haze* West Kalimantan -> **Sarawak** | plume extends NW from West Kalimantan toward Sarawak | **match** |
| Smoke in W, C, E, N, S **Kalimantan** | detected in West and Central/South | partial - we miss E/N Kalimantan, which was under cloud |
| Smoke in **Riau, Jambi, N and S Sumatra** | ~0.3% of Sumatra flagged at 14:00 | **timing, not a miss** - see below |

The direction agreement is the most valuable result: an independent expert
assessment of where the smoke is going matches what Farneback derived from two
frames, with no wind input at all.

#### The Sumatra disagreement, resolved

BMKG draws smoke polygons over Riau, Jambi and South Sumatra at 16:00 WIB; we
flag almost nothing at 14:00. That looked like a detection failure. It is a
coverage gap. Median `B03-B06` over land, threshold 6.0:

| region | 14:00 WIB | 16:00 WIB | ratio to control, 14:00 -> 16:00 |
|---|---|---|---|
| Riau | 3.92 | 8.90 | 0.56 -> 0.48 |
| Jambi | 2.45 | 9.00 | 0.35 -> 0.49 |
| South Sumatra | 2.19 | 9.18 | 0.31 -> 0.50 |
| West Kalimantan (control) | 6.97 | 18.41 | - |

Two things follow.

**Inside our window we are right.** At 14:00 Sumatra sits at 2.2-3.9 against a
threshold of 6.0, while West Kalimantan, where both products agree smoke
exists, sits at 6.97. There is no signal there to miss.

**The 16:00 rise is partly real and partly the sun.** Everything inflates at
low sun - the control more than doubles - so the raw jump to ~9 cannot be read
as smoke. Normalised against the control, Jambi and South Sumatra genuinely
strengthen (0.35 -> 0.49, 0.31 -> 0.50) while Riau weakens (0.56 -> 0.48). So
smoke did develop over southern Sumatra during the afternoon, after our day
ends, which is exactly when BMKG publishes.

The disagreement is therefore about *when*, not *what*. It is the strongest
argument yet for widening the window past 14:00 WIB, and it makes the
sun-angle correction the highest-value piece of work left: fixing it would let
this product cover the part of the day when Sumatran haze actually appears.

### Why these choices

| | |
|---|---|
| Hotspots from FIRMS, not our own thermal test | BMKG/FIRMS already publish it, and VIIRS 375 m beats AHI for smouldering peat |
| GFS, not ERA5 | ERA5 has ~5-day latency, fatal for live use |
| Optical flow, not a trained model | No training data needed; the wind field is a sanity check, not an input |
| Daylight only | The visible bands the mask depends on die at night |
| 0–3 h horizon | Past that, the frozen-flow assumption stops holding |
| GitHub Actions + Pages | Zero server cost, same model as the PWA apps |

## Running it

```bash
pip install -r requirements.txt

python -m pipeline.fetch_ahi --ensure-pair    # latest scene (+ a flow partner)
python -m pipeline.smoke_mask --all
python -m pipeline.advect
python -m pipeline.fetch_firms                # needs FIRMS_MAP_KEY
python -m pipeline.publish

python -m http.server -d site 8000            # then open localhost:8000
```

Every stage takes `--date YYYYMMDD_HHMM` (UTC) for backfill and testing, and
`-v` for debug logging. Stages are independent: each reads `state/` and writes
`state/` or `site/data/`.

### How busy the hotspot layer is

`FIRMS_MIN_FRP_MW` in `config.py` controls it. On a bad day FIRMS returns
7000+ detections in this domain, which merge into one red mass and hide the
smoke field they are meant to explain. Measured on 2026-08-21:

| floor | points | share of total radiative power |
|---|---|---|
| 0 MW | 7,222 | 100% |
| 10 MW | ~2,900 | 79% |
| **20 MW (default)** | **1,038** | **58%** |
| 50 MW | ~350 | 30% |

Confidence is not a useful dial here, though it looks like one: of 5,261
detections, VIIRS returned 3,843 nominal, 151 high and **0 low**. Filtering to
"high confidence" would discard 96% of VIIRS. It flags detection reliability,
not fire importance.

**Caveat that matters for this product:** Kalimantan's worst haze comes from
smouldering peat, which burns cool and registers low FRP. Raising the floor
preferentially discards the fires that make the most smoke per unit heat.
Visible in the data — hotspot/smoke enrichment falls from 3.3x unfiltered to
2.7x at the 20 MW floor. Treat it as a readability control, not a relevance
one. The page states the floor and how many detections it hid.

### FIRMS key

Get one free and instantly at
<https://firms.modaps.eosdis.nasa.gov/api/map_key/>. Set it as `FIRMS_MAP_KEY`
locally and as a repository secret of the same name for Actions. Without it,
the hotspot layer falls back to the last cached GeoJSON and flags itself stale;
nothing else is affected.

### Wind arrows (stretch)

`fetch_gfs` needs `cfgrib`, which is not in `requirements.txt`:

```bash
pip install cfgrib eccodes
python -m pipeline.fetch_gfs
```

Without it the layer is simply absent from the page.

## Deploying

1. Push this repo to GitHub.
2. Settings → Secrets and variables → Actions → add `FIRMS_MAP_KEY`.
3. Actions → **pipeline** → *Run workflow* once by hand. It creates the
   `gh-pages` branch.
4. Settings → Pages → source **Deploy from a branch**, branch `gh-pages`, `/`.

The schedule (`*/30`) takes over from there. GitHub runs cron jobs late under
load, so the effective cadence is more like 35–45 min — which is why staleness
is displayed rather than assumed.

## Tuning the mask

This is the part that needs a human. Generate a day of frames and compare them
against BMKG's public smoke RGB:

```bash
for t in 0000 0100 0200 0300 0400 0500 0600 0700 0800 0900; do
  python -m pipeline.fetch_ahi --date 20260821_$t
done
python -m pipeline.smoke_mask --all --force --qa qa/
```

`qa/qa_animation.gif` animates composite-vs-mask side by side. Adjust the
thresholds in `pipeline/config.py` — **every** tunable lives there — until the
mask matches the brown areas in the reference product.

### Where to get the reference imagery

ASMC archives daily NOAA-20/SNPP false-colour imagery by region and date, with
hotspots overlaid — this is the comparison the plan calls for, and it is
browsable rather than needing an account:

<https://asmc.asean.org/satellite-polar/> → Region **Kalimantan**, pick the
date, then *View high-resolution image*. Overpass is around 06:00–06:30 UTC
(13:00–13:30 WIB), which lands conveniently close to our last usable scene at
14:00 WIB.

Daily hotspot counts per region, useful as an independent sanity check on the
FIRMS layer: <https://asmc.asean.org/asmc-haze-hotspot-daily> (switch to the
VIIRS tab; the AVHRR default reads near zero and is not comparable).

### What the first comparison found (2026-08-21)

Compared ASMC NOAA-20 06:25 UTC against our 07:00 UTC mask.

Agrees:

- Hotspot geography. ASMC counted 667 for Kalimantan against 10 for Sarawak,
  and its clusters sit where ours do — West Kalimantan plus Central/South.
- Cloud over North and East Kalimantan.
- Smoke over West Kalimantan and along the southern coast.

Does not agree, and the resolution is not what it looked like:

- ASMC shows a large tan sheet over the South China Sea north-west of Borneo
  that our mask does not flag. That looks like a miss. It is not. Smoke is
  transparent at 1.6 and 2.3 um, so smoke over water leaves SWIR near the
  open-sea value of ~2%. Measured over that area: **B05 17.3, B06 13.0**,
  against 19.5 and 14.5 for open sea further north — bright in every band,
  which is glint or thin cirrus, not smoke. Declining to call it smoke is
  correct.

Still open after this comparison:

- **Sun glint defeats the water test.** Those glint pixels read B05 ~17, well
  above `WATER_B05_MAX`, so they are treated as land and get the land rules.
  Harmless in this scene — the SWIR contrast is far too low to trigger smoke —
  but it is luck rather than design.
- **West Kalimantan reads 49.6% of unobscured land as smoke**, which is high
  against an ASMC frame where that area is mostly green with dense hotspots.
  Some is certainly real, given the fire count. Whether all of it is remains
  the open question, and it is the next thing to chase.

**The current defaults have not been compared against BMKG yet.** They were
set from the 2026-08-21 05:00–07:00 UTC scenes and give 6–10% smoke over the
domain, concentrated on the West Kalimantan coast and the Central Kalimantan
peatlands. That is plausible but unverified — the BMKG comparison is still the
gate before anyone should trust the numbers.

Three things learned calibrating them, worth not rediscovering:

- **Blue-minus-red alone does not work here.** Over dark forest it is
  dominated by Rayleigh scattering, so it painted most of Borneo as smoke.
  `B03−B06` (red minus 2.3 µm SWIR) is the test that actually separates
  smoke from cloud and soil, because aerosol scattering is nearly absent at
  2.3 µm. It is the dominant control — the other thresholds barely move the
  answer once it is set.
- **There is no clean-atmosphere background on a smoky day.** Clear sea
  measured B01 ≈ 13% and clear forest ≈ 20%, not the ~8% a clean profile
  would give. Thresholds picked from textbook clear values are inert here.
- **`B11−B14` separates land from sea, not smoke from clear** over this
  domain (medians −1.8 vs −2.5). It is kept only as a permissive cloud-edge
  guard. Tighten it and you are selecting land, not smoke.

### Water, sediment, and the coastline

The single biggest source of false smoke is not cloud — it is water. Turbid
coastal water and river plumes lift red reflectance while SWIR stays near
zero, which is exactly the `B03−B06` signature smoke produces. Before this was
handled, the Malacca Strait read 9.4% smoke against Kalimantan's 8.8%.

`B05` (1.6 µm) separates them cleanly, because water absorbs SWIR and land
does not:

| surface | B01 | B03 | B05 | B06 |
|---|---|---|---|---|
| open ocean | 12.4 | 4.6 | **1.6** | 1.0 |
| turbid coastal water | 16.7 | 8.5 | **2.4** | 1.3 |
| mixed coastal pixel | 18.9 | 11.8 | **7.0** | 4.7 |
| real smoke over land | 26.7 | 16.8 | **16.1** | 7.6 |

Over water (`B05 < WATER_B05_MAX`) the mask ignores the SWIR contrast and
demands a strong blue signal instead, since smoke scatters blue hard while
sediment reflects red.

The cutoff is **8, not 5**, because at 3–4 km resolution the coast is a band
of *mixed* land/water pixels — tidal flats, mangrove, estuaries — that land
between the pure classes and carry the sediment signature. At 5 they counted
as land and painted the Musi estuary and the Malacca coast as haze.

Cross-checking against FIRMS (thermal sensors, no physics shared with the AHI
tests) confirms the fix removed artefacts rather than signal: hotspot/smoke
enrichment rose from **2.7× to 4.4×** as the false positives came out.

### Absence of smoke is sometimes just absence of fire

Worth knowing before "fixing" a quiet region. On 2026-08-21 the mask found
essentially no smoke over Sumatra, which looks like under-detection until you
check the numbers:

| | Sumatra | Kalimantan |
|---|---|---|
| FIRMS hotspots ≥20 MW | 88 | 1,025 |
| near-fire `B03−B06` | 2.69 | 5.67 |
| background `B03−B06` | 3.41 | 2.69 |

Near Kalimantan's fires the signal is +3.0 above background. Near Sumatra's it
is *below* background — there is no pall to detect. Before loosening
thresholds for a region, check whether the radiometric enhancement exists at
all; if it does not, the mask is right and the region simply is not burning.

The discriminating knobs, in rough order of impact:

| Constant | Raise it to… |
|---|---|
| `SMOKE_B03_MINUS_B06_MIN` | cut false smoke over bright ground and thin cloud |
| `WATER_B05_MAX` | treat more of the coastline as water, killing sediment false alarms (costs real smoke above ~10) |
| `WATER_SMOKE_B01_MIN` | require thicker smoke before believing it at sea |
| `SMOKE_B01_MIN` | ignore faint haze |
| `CLOUD_B13_MAX_K`, `CLOUD_B06_MIN` | mark more as obscured, forecast less |
| `SMOKE_MIN_BLOB_CELLS` | drop more speckle |

### Standing QA check

```bash
python -m pipeline.validate --date 20260821_0700
```

Scores the mask against FIRMS hotspots. The two share no physics — FIRMS is
thermal infrared, the mask is visible/SWIR reflectance — so agreement is
evidence rather than a restatement of our own assumptions.

**It counts only fires acquired at or before the scene time.** Scoring a
midday scene against the whole 24 h list charges it for fires that had not
started yet, and that artefact is large enough to mislead badly:

| WIB | vs all 24 h fires | vs fires that preceded the scene |
|---|---|---|
| 12:00 | 2.4x | **14.3x** (n=11) |
| 12:30 | 1.0x | **3.9x** (n=13) |
| 13:00 | 2.1x | **9.8x** (n=9) |
| 14:00 | 3.8x | 4.3x |
| 15:00 | 3.5x | 3.6x |

Read naively, the first column says the morning product is worthless — 12:30
at 1.0x is exactly chance. It is not; the mask is far more consistent across
the day than that suggests. Morning sample sizes are small, so those figures
are noisy, but they are all higher rather than lower.

Score a forecast against what actually happened:

```bash
python -m pipeline.advect --date 20260821_0600 --verify 20260821_0700
```

It reports CSI/POD/FAR alongside the persistence CSI. If advection does not
beat persistence, the flow is not earning its place. On the 2026-08-21 case
it does, modestly:

| lead | CSI | persistence CSI | POD | FAR |
|---|---|---|---|---|
| +30 min | 0.446 | 0.420 | 0.697 | 0.447 |
| +60 min | 0.310 | 0.276 | 0.434 | 0.480 |

One case is not a validation study, and the plan does not claim one.

## Known limits

**Daylight only, and a narrower day than you might expect: 09:30–14:00 WIB.**
The mask needs a solar elevation of at least 50°. That is not a sensor limit,
it is a calibration limit, and it was measured rather than assumed. Holding
thresholds fixed and sweeping the sun angle on 2026-08-21:

| WIB | sun elev | smoke detected |
|---|---|---|
| 12:00 | 77° | 1.2% |
| 14:00 | 54° | 5.2% |
| 15:00 | 40° | 16.2% |
| 16:00 | 26° | 45.1% |
| 16:30 | 18° | 53.7% |

Smoke does not grow tenfold in three hours. At 18° the slant path is about
3.2 air masses, so thin regional haze that is invisible overhead turns
optically thick and the mask paints the whole domain. It is not a scaling
artefact — normalised indices `(a−b)/(a+b)`, which are invariant to the
1/cos(SZA) correction, drift just as badly, because this is real radiative
transfer rather than a units problem.

#### Why it drifts, and how far a fix got

The cause is Rayleigh scattering, and it is measurable rather than
speculative. Rayleigh optical depth goes as roughly wavelength^-4:

| band | wavelength | Rayleigh optical depth |
|---|---|---|
| B01 | 0.47 um | 0.1851 |
| B03 | 0.64 um | 0.0525 |
| B06 | 2.26 um | **0.0003** |

So `B03 - B06`, the discriminator the whole mask rests on, differences a band
with meaningful Rayleigh against one with essentially none — a factor of 175.
It therefore accumulates a purely atmospheric term as the sun drops. Worse,
Rayleigh path reflectance itself scales as 1/cos(SZA), and the solar-zenith
correction divides by cos(SZA) again, so the artefact is amplified twice.

`pipeline/rayleigh.py` implements a single-scattering path-reflectance
correction (satellite zenith from the geostationary geometry, solar azimuth,
scattering angle, Rayleigh phase function). Measured effect over 54 deg to
33 deg of sun elevation:

| statistic | drift |
|---|---|
| raw smoke fraction | **x5.0** |
| corrected B01, p90 | x1.11 |
| corrected `B03-B06`, p99 | **x1.02** |
| corrected `B03-B06`, p90 | x1.57 |

The thick-smoke tail becomes stationary. What still drifts is the median and
p90, which is consistent with aerosol path radiance rather than Rayleigh —
that is, thin haze genuinely becoming optically thicker along a longer slant
path, which is a real signal and not an artefact.

**This is now wired in** (`RAYLEIGH_CORRECT` in `config.py`). Subtracting
Rayleigh lowers every reflectance, so the thresholds beneath it are not the
old ones rescaled by eye — they were derived by quantile-matching against the
14:00 WIB scene, the one point cross-validated against FIRMS, so the corrected
mask reproduces the validated answer there exactly:

| | 14:00 smoke | Malacca | Riau | FIRMS enrichment |
|---|---|---|---|---|
| before | 5.18% | 0.6% | 0.7% | 3.8x |
| after | 5.19% | 0.6% | 0.7% | 3.8x |

while drift inside the window drops from x4.5 to x2.7 (77 to 54 degrees).

One part deliberately stays uncorrected: **the over-water branch**. Over dark
water the measured signal is mostly atmosphere, and those sediment-rejection
thresholds were validated on raw values across several scenes. Recalibrating
them for corrected input at a single scene did not generalise — it held at
14:00 and let 73% of the noon detections back in over water, with the Malacca
Strait returning to 6.4%. Caught by revalidation, not by inspection, which is
the argument for running the whole check after any threshold change.

The window still ends at 14:00 WIB. Extending it is now a separate decision
with a measurement behind it rather than a guess: corrected drift across 77 to
33 degrees is x4.1, against x34.8 uncorrected.

A 40° floor was tried first, stretching the day to 15:00 WIB, and rejected on
sight: by then the sun is under 40° across the eastern half of the domain, so
that half came back hatched as unusable while the rest still read three times
the noon smoke fraction. Half a map of haze is not better than no map.

Two consequences worth stating plainly:

- The last useful scene of the day is 14:00 WIB, so the last forecast reaches
  17:00 WIB. Afternoons and evenings show that frozen product, labelled.
- Even inside the window there is drift (1.2% at noon, 5.2% at 14:00). Some of
  that is real afternoon fire activity; some is the same effect. Resolving it
  is exactly what the BMKG comparison is for.

## Guarantees the code keeps

1. **Staleness is visible.** `meta.json` carries `scene_utc`; the page computes
   age in the browser and goes red past 90 minutes. A confident stale map is
   worse than no map.
2. **Cloud honesty.** Obscured areas are hatched, never advected. Forecast
   smoke that came from behind cloud is not drawn.
3. **The caption is on every view.**
4. **Raw HSD is deleted immediately** — `fetch_ahi` removes it in a `finally`,
   success or failure. A full-disk scene is ~240 MB and the runner has ~14 GB.
5. **All tunables in `config.py`.**
6. **Secondary layers degrade alone.** No FIRMS key, no GFS, or an implausible
   flow field each degrade their own layer and leave the rest standing.
7. **Thresholds mean the same thing all day.** Visible reflectance is divided
   by cos(solar zenith) before classification. Without it the mask shrank
   through every afternoon — 9.6% at 12:00 WIB down to 3.4% at 14:00 — purely
   from the sun dropping, which optical flow would then read as convergence.

## Tests

```bash
python -m pytest tests -q
```

Offline unit tests for the arithmetic that is easy to get quietly wrong: grid
geometry and row order, solar elevation, segment addressing, the mask rules
against synthetic smoke/cloud/soil, advection direction and scaling, the
meteorological bearing convention, and the verification scores.

## Layout

```
pipeline/       config.py + one module per stage, run as python -m pipeline.<stage>
site/           index.html + data/ (written by the pipeline, published to gh-pages)
tests/          offline unit tests
state/          gridded scenes, masks, forecasts between runs (gitignored)
work/           raw HSD scratch, deleted after every run (gitignored)
```
