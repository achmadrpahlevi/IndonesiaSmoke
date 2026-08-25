# KalimSmoke — Weekend Build Plan

> **Superseded in part, 2026-08-25.** The domain and the sun-angle gate were
> reworked for all of Indonesia — see
> `docs/superpowers/specs/2026-08-25-indonesia-smoke-domain-design.md`. The
> scope decisions in §1 and the non-negotiables in §5 still stand.

Live dashboard for Kalimantan wildfire smoke: current smoke extent from Himawari-9, hotspots from FIRMS, and a 0–3 h forward advection of the smoke field. Fills the gap between BMKG (current imagery only) and ASMC (daily narrative outlook): **hourly, gridded, short-range smoke movement with a time slider.**

Not a research product. No novelty claims, no validation study. "Daytime only, indicative" is the standing caption.

---

## 1. Scope decisions (already made — do not reopen)

| Decision | Choice | Why |
|---|---|---|
| Hotspot detection | **NASA FIRMS API** (VIIRS 375 m + MODIS), not our own B07 thresholding | BMKG/FIRMS already publish this; VIIRS beats AHI for smouldering peat |
| Winds | **GFS 0.25°** (AWS bucket), NOT ERA5 | ERA5 has ~5-day latency; fatal for live use |
| Smoke motion | **Farnebäck optical flow** on consecutive AHI smoke masks | No training needed; wind field is a sanity check only |
| Night handling | **None (v1)** | Daylight-only; visible bands die at night |
| Forecast horizon | **0–3 h**, 30-min steps | Beyond 3 h optical flow assumptions break |
| Hosting | **GitHub Actions (schedule) + GitHub Pages + Leaflet** | Same model as the PWA apps; zero server cost |
| Cadence | **30 min** | Actions schedules run late anyway; 10-min AHI cadence is overkill for smoke |

## 2. Architecture

```
GitHub Actions (cron: every 30 min)
 ├── fetch_ahi.py      → latest Himawari-9 scene from s3://noaa-himawari9 (anon)
 │                       subset lon 108–120°E, lat 5°S–8°N, reproject to plate carrée
 ├── smoke_mask.py     → B01/B03 reflectance + B11−B14 split-window → binary smoke mask
 │                       + cloud mask → "obscured" layer (never advect what we can't see)
 ├── fetch_firms.py    → FIRMS API, last 24 h VIIRS+MODIS points → GeoJSON
 ├── advect.py         → Farnebäck flow between last two masks → displacement field
 │                       → integrate forward: masks at t+30 … t+180 min
 ├── fetch_gfs.py      → (stretch) GFS 850 hPa wind → arrow overlay GeoJSON
 └── publish.py        → PNGs + GeoJSON + meta.json {timestamp, daylight, quality flags}
                          commit to gh-pages branch

GitHub Pages
 └── index.html (Leaflet)
      layers: smoke now / smoke +0.5…+3 h (time slider) / FIRMS points /
              obscured-by-cloud hatching / (stretch) wind arrows
      header: "Last updated HH:MM WIB" — RED if > 90 min stale
```

## 3. Repo layout

```
kalimsmoke/
├── PLAN.md                  ← this file
├── .github/workflows/pipeline.yml   (schedule: */30, workflow_dispatch for manual runs)
├── pipeline/
│   ├── fetch_ahi.py
│   ├── smoke_mask.py
│   ├── fetch_firms.py
│   ├── advect.py
│   ├── fetch_gfs.py         (stretch)
│   ├── publish.py
│   └── config.py            (bbox, grid, thresholds, horizons — all constants here)
├── site/
│   ├── index.html
│   └── data/                (written by Actions: latest.png, fcst_*.png, firms.geojson, meta.json)
└── requirements.txt         satpy[ahi_hsd], s3fs, opencv-python-headless, pyresample,
                             numpy, xarray, pillow, requests
```

## 4. Schedule

### Friday night (1 h) — de-risk the only real risk
- [ ] Clean env, `pip install satpy[ahi_hsd] s3fs`
- [ ] Load ONE AHI segment from `s3://noaa-himawari9` anonymously; render B03 to PNG
- [ ] Register for a FIRMS API key (instant, free)
- If satpy fights back tonight, the weekend plan still works — but fix it tonight, not Saturday.

### Saturday AM — ingest
- [ ] `fetch_ahi.py`: latest scene → Kalimantan subset → fixed plate carrée grid (~2 km) → NetCDF/npz
- [ ] Must handle: missing scene (AHI housekeeping gaps), partial segments → skip cleanly, never crash
- **Done when:** one command produces a reprojected multi-band array for "now"

### Saturday PM — smoke mask
- [ ] `smoke_mask.py`: reflectance + split-window thresholds → smoke / cloud / clear
- [ ] Run over the past 24 h of scenes; **eyeball every frame against BMKG's public smoke RGB** — this comparison IS the QA step, do not skip it
- [ ] Tune thresholds in `config.py` until the mask visibly matches BMKG's brown smoke areas
- **Done when:** an animated GIF of today's masks looks like smoke, not noise

### Sunday AM — motion + hotspots
- [ ] `advect.py`: `cv2.calcOpticalFlowFarneback` on consecutive masks → forward-integrate to +3 h
- [ ] Zero out flow under the cloud/obscured mask; carry an "unverifiable" flag per pixel
- [ ] `fetch_firms.py` → GeoJSON
- **Done when:** for a case this week, the +1 h forecast roughly matches the actual +1 h mask

### Sunday PM — ship
- [ ] Leaflet page: base map, layer toggles, time slider over forecast steps, FIRMS points, staleness banner
- [ ] `pipeline.yml`: schedule every 30 min + `workflow_dispatch`; commit outputs to gh-pages
- [ ] Two full end-to-end Actions runs pass before calling it done
- **Done when:** the public URL shows smoke moving, timestamped, on a phone

### Stretch (only if Sunday PM is clear)
- GFS 850 hPa wind arrows; night-time fallback via wind advection; per-city ETA callouts (Palangkaraya, Pontianak, Banjarmasin)

## 5. Non-negotiables

1. **Staleness is visible.** `meta.json` timestamp rendered in the header; red banner past 90 min. A confident stale map is worse than no map.
2. **Cloud honesty.** Obscured areas get hatching, not an advected guess.
3. **Caption on every view:** *"Indicative short-range smoke movement, daytime only. Not an official forecast — see BMKG and ASMC."*
4. **Delete raw HSD immediately after processing.** Actions runners have ~14 GB; full-disk AHI will fill it.
5. **All tunables in `config.py`.** Thresholds will need tweaking after launch; don't bury them.

## 6. Known failure modes

| Failure | Symptom | Mitigation |
|---|---|---|
| satpy/HSD deps | Friday install pain | That's why it's Friday's task |
| AHI scene gap | No new frame at :00/:30 | Reuse last valid pair; flag in meta.json |
| Night | No visible bands ~18:30–06:00 WIB | "Daytime only" badge; freeze last daylight forecast, clearly labelled |
| Widespread cloud (ASMC flags this now) | Mask mostly "obscured" | Hatching layer; suppress forecast if <20% of domain is clear |
| Actions runs late | 45-min effective cadence | Acceptable; staleness banner covers it |
| FIRMS rate limit | Empty hotspot layer | Cache last good GeoJSON; layer degrades independently |

## 7. Claude Code notes

- Phases are independent: `fetch_ahi` → `smoke_mask` → `advect` are a pipeline; `fetch_firms` and the Leaflet page can be built in parallel sessions.
- Each script runs standalone with a `--date` arg for backfill/testing; the Actions workflow just chains them.
- Test data: pull scenes from earlier this week (fires are active now — real smoke to tune against).
- Keep it single-repo, no packages, no framework. Boring code that runs at 3 AM.
