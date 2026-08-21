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
               bucket → only the band/segment files touching 108–120°E,
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
mask matches the brown areas in BMKG's product.

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

The discriminating knobs, in rough order of impact:

| Constant | Raise it to… |
|---|---|
| `SMOKE_B03_MINUS_B06_MIN` | cut false smoke over bright ground and thin cloud |
| `SMOKE_B01_MIN` | ignore faint haze |
| `CLOUD_B13_MAX_K`, `CLOUD_B06_MIN` | mark more as obscured, forecast less |
| `SMOKE_MIN_BLOB_CELLS` | drop more speckle |

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
