"""All tunables live here. PLAN.md non-negotiable #5.

Thresholds will need tweaking after launch against BMKG's smoke RGB.
Nothing in this file should import from the rest of the pipeline.
"""

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

# Scratch space for raw HSD. Deleted immediately after processing
# (non-negotiable #4: Actions runners have ~14 GB, full-disk AHI will fill it).
WORK_DIR = Path(os.environ.get("KALIMSMOKE_WORK", REPO_ROOT / "work"))

# Gridded scenes + masks that survive between runs (advection needs t-1).
STATE_DIR = Path(os.environ.get("KALIMSMOKE_STATE", REPO_ROOT / "state"))

# Everything published to GitHub Pages.
SITE_DATA_DIR = Path(os.environ.get("KALIMSMOKE_OUT", REPO_ROOT / "site" / "data"))

# --------------------------------------------------------------------------
# Domain grid — fixed plate carree, ~2 km
# --------------------------------------------------------------------------

# All of Indonesia, Sabang to Merauke and Rote to Miangas, plus the
# countries its haze actually reaches: Singapore, Peninsular Malaysia,
# Sarawak, Sabah, Brunei, Timor-Leste and the PNG border.
#
# The 47 degrees of longitude is close to free. AHI segments divide the disk
# by scan line, so a wider longitude range is a bigger crop of files already
# being downloaded. Only the southward extension to 11.5S buys a segment:
# segments_for_bbox returns [4,5,6,7,8] here against [4,5,6,7] before, so 35
# files instead of 28, about 300 MB instead of 240 MB.
LON_MIN, LON_MAX = 94.5, 142.0
LAT_MIN, LAT_MAX = -11.5, 8.0
GRID_RES_DEG = 0.02  # ~2.2 km at the equator

# Rows run north -> south (image convention). Values are pixel centres.
GRID_NX = int(round((LON_MAX - LON_MIN) / GRID_RES_DEG))  # 2375
GRID_NY = int(round((LAT_MAX - LAT_MIN) / GRID_RES_DEG))  # 975

# Nearest-neighbour radius for resampling AHI -> grid, metres. At 94.5 E the
# viewing zenith angle is 53.1 degrees (measured with
# pipeline.rayleigh.view_zenith at lon 94.5, lat 0), so a 2 km nadir pixel is
# stretched well past that spacing and the old 5000 m radius left holes
# along the western edge. Verified by counting the NaN fraction west of 97 E
# — see the QA step in Task 10.
RESAMPLE_RADIUS_M = 8000

# --------------------------------------------------------------------------
# Himawari-9 source
# --------------------------------------------------------------------------

AHI_BUCKET = "noaa-himawari9"
AHI_S3_BASE = f"https://{AHI_BUCKET}.s3.amazonaws.com"
AHI_PRODUCT = "AHI-L1b-FLDK"
AHI_SATELLITE_LON = 140.7  # sub-satellite point, degrees east

# Bands we actually download. Every extra band is real bandwidth and disk.
#   B01 0.47 um  blue      — smoke scatters strongly here
#   B03 0.64 um  red       — smoke is dimmer here than cloud
#   B05 1.6 um   SWIR      — cloud/soil bright, smoke nearly transparent
#   B06 2.3 um   SWIR      — the single best smoke/cloud/soil discriminator
#   B11 8.6 um   split-win — B11-B14 separates smoke/dust from water cloud
#   B13 10.4 um  window    — cloud-top temperature
#   B14 11.2 um  split-win
AHI_BANDS = ["B01", "B03", "B05", "B06", "B11", "B13", "B14"]

# satpy dataset names for the bands above.
AHI_DATASETS = {b: b for b in AHI_BANDS}

# FLDK is split into 10 segments, north to south — a fixed property of the
# full disk itself, not of what we ask for. Indonesia straddles the equator
# just as Kalimantan alone did, so the domain still lands mid-disk. Each
# segment boundary is a fraction of disk height, independent of grid
# resolution or domain width, so one calculation in
# fetch_ahi.segments_for_bbox serves whatever bbox we point it at. Computed
# properly there; this is the fallback when the geometry calculation is
# unavailable.
AHI_TOTAL_SEGMENTS = 10
AHI_FALLBACK_SEGMENTS = [4, 5, 6, 7, 8]

# AHI full-disk cadence, minutes. Scenes appear at :00, :10, :20 ...
AHI_SLOT_MINUTES = 10

# How far back to search for a usable scene before giving up.
AHI_MAX_SLOT_LOOKBACK = 12  # 2 hours

# Per-file download timeout / retries.
AHI_HTTP_TIMEOUT = 120
AHI_HTTP_RETRIES = 3

# --------------------------------------------------------------------------
# Smoke mask thresholds  (Saturday PM: tune these against BMKG smoke RGB)
# --------------------------------------------------------------------------

# Remove the Rayleigh path term before thresholding. See pipeline/rayleigh.py
# for why: B03 carries 175x the Rayleigh optical depth of B06, so the smoke
# discriminator accumulates a purely atmospheric signal as the sun drops, and
# the 1/cos(SZA) correction above amplifies it a second time.
#
# The thresholds below it are NOT the uncorrected ones rescaled by hand. They
# were derived by quantile-matching against the 14:00 WIB 2026-08-21 scene --
# the one point cross-validated against FIRMS -- so that the corrected mask
# reproduces the validated answer there exactly (5.19% vs 5.18% smoke, 19.9%
# of hotspots on smoke, 3.8x enrichment, identical either way) while being far
# less sun-dependent away from it. Measured drift across 77 to 33 degrees of
# sun elevation: x34.8 uncorrected, x4.1 corrected.
RAYLEIGH_CORRECT = True

# UNITS: satpy returns AHI visible bands as PERCENT reflectance (0-100) and
# IR bands as brightness temperature in kelvin. Thresholds below use those
# native units — do not rescale.

# Smoke is bright in the blue band but not cloud-bright. On a smoky day the
# whole domain sits high — clear sea measured ~13% and clear forest ~20% in
# the 2026-08-21 scenes — so this only screens out dark water and shadow.
SMOKE_B01_MIN = 3.72 if RAYLEIGH_CORRECT else 14.0
SMOKE_B01_MAX = 40.0

# Smoke scatters far more blue than red.
SMOKE_B01_MINUS_B03_MIN = -1.38 if RAYLEIGH_CORRECT else 6.0

# THE discriminating test, and the first knob to reach for. Aerosol
# scattering falls off steeply with wavelength, so a smoke pall is bright at
# 0.64 um and nearly invisible at 2.3 um, while cloud and bare soil are
# bright in both. Measured behaviour over Kalimantan:
#     >= 3  bleeds into scattered cumulus over land   (~19% of domain)
#     >= 6  the coherent pall, this default           (~ 8%)
#     >= 7  the densest core only                     (~ 5%)
SMOKE_B03_MINUS_B06_MIN = 3.09 if RAYLEIGH_CORRECT else 6.0

# Over water the B03-B06 test above is unsafe. Sediment-laden coastal water
# and river plumes lift red reflectance while SWIR stays near zero, which is
# the same signature smoke produces, so the Sumatra and Kalimantan coasts get
# painted as haze. Measured over the 2026-08-21 05:00 scene:
#
#   region                       B01   B03   B05   B06
#   Riau coastal sediment       16.7   8.5   2.4   1.3   <- was flagged smoke
#   real Kalimantan smoke       26.7  16.8  16.1   7.6
#   open ocean                  12.4   4.6   1.6   1.0
#
# Water is unmistakable at 1.6 um: ~2% against ~16% for land. Over water we
# therefore ignore the SWIR contrast and demand a strong blue signal instead,
# because smoke scatters blue hard while sediment reflects red.
#
# The cutoff is 8, not 5, because at 3-4 km the coastline is a band of MIXED
# land/water pixels — tidal flats, mangrove, estuaries — that land between the
# two pure classes and carry the sediment signature. At 5 they counted as land
# and painted the Musi estuary and the Malacca coast as haze. Measured on the
# 07:00 scene, raising 5 -> 8 removes 75% of the Sumatra coastal artefacts and
# costs 7% of Kalimantan's detections, most of which are the same artefact on
# Borneo's own coast. Above ~10 it starts eating real smoke, whose 1.6 um
# signal comes from the land surface showing through (median 11.8).
WATER_B05_MAX = 8.0
WATER_SMOKE_B01_MIN = 20.0
WATER_SMOKE_B01_MINUS_B03_MIN = 10.0

# Smoke over water must brighten the RED band too, not just the blue.
#
# Without this the water test read "bright blue with a large blue-minus-red
# excess", which is the definition of clear tropical water. It only survived
# because afternoon glint lifts B06 and dulls the contrast; in the morning,
# with no glint, clear water measured B01 20.3 and blue excess 12.5 and sailed
# through. 74% of a 30.70% morning map was open sea.
#
# Clear water stays dark at 0.64 um whatever the geometry, so requiring red
# separates it from smoke. Measured on the 08:50 WIB scene, enrichment against
# fires that preceded it:
#     >=  0   30.70% smoke, 1.5x   (chance)
#     >= 12   10.89%,       4.3x
#     >= 14    7.73%,       6.0x   <- default, beats the afternoon's 4.2x
#     >= 16    6.50%,       7.1x
# The afternoon is unaffected at every value, because afternoon water never
# passes the B01 gate anyway. This is a morning fix that costs the rest of the
# day nothing.
WATER_SMOKE_B03_MIN = 14.0

# Reflectance excess at which the overlay reaches full opacity.
SMOKE_DENSITY_SPAN = 8.0

# Smoke is optically thin in the IR window, so the surface stays warm.
SMOKE_B13_MIN_K = 280.0

# Split-window. Textbook says water/ice cloud sits near or below zero and
# smoke pushes it up — but over this domain it mostly separates land (median
# -1.8) from sea (-2.5), i.e. surface emissivity, not aerosol. Kept as a
# permissive guard against cloud edges rather than a smoke test. Do not
# tighten it without checking you are not just selecting land.
SMOKE_BTD_1114_MIN = -3.5

# Cloud test — anything colder or brighter than this is "obscured",
# never advected (non-negotiable #2). The SWIR pair catches warm low cumulus
# that the 10.4 um temperature test misses.
CLOUD_B13_MAX_K = 280.0
CLOUD_B01_MIN = 27.71 if RAYLEIGH_CORRECT else 38.0
CLOUD_B03_MIN = 22.0
CLOUD_B06_MIN = 18.0

# Minimum solar elevation for the visible bands to be usable.
#
# 40, not the 12 you would pick from "can the sensor see anything". Measured
# smoke fraction against sun angle on 2026-08-21, thresholds fixed:
#
#     12:00 WIB  77 deg   1.2%      15:00 WIB  40 deg  16.2%
#     13:00      68       2.5%      15:30      33      26.1%
#     14:00      54       5.2%      16:00      26      45.1%
#     14:30      47       8.1%      16:30      18      53.7%
#
# Smoke does not grow tenfold in three hours. At 18 deg the slant path is
# ~3.2 air masses, so thin regional haze that is invisible overhead becomes
# optically thick and the mask starts painting the whole domain. This is not
# a units artefact: normalised indices, (a-b)/(a+b), which are invariant to
# the 1/cos(SZA) correction, drift just as badly, because the change is
# genuine radiative transfer rather than scaling.
#
# TWO different questions, which must not share a threshold.
#
# 1. Can the sensor see THIS PIXEL at all? Below ~12 deg the visible bands are
#    useless, so the pixel is marked obscured and hatched. This is per pixel
#    and it is what draws the grey areas on the map.
MIN_SOLAR_ELEVATION_DEG = 12.0

# 2. Is THIS SCENE inside the range the thresholds were calibrated in? This is
#    per scene, and a scene that fails is not published at all — the last good
#    one stays up, frozen and labelled.
#
#    50 deg ends the day at 14:00 WIB. Conflating the two numbers was a
#    mistake: raising the per-pixel floor to 50 hatched a third of the 14:00
#    scene, which is a worse map, while a 40 deg version hatched half the
#    domain and still read 3x the noon smoke fraction on what was left.
#
#    14:00 WIB / ~54 deg is also the only point ever cross-checked against an
#    independent sensor — hotspot/smoke enrichment of 4.4x against FIRMS — so
#    the product stays inside the range that check covers.
#
#    Widening this is a research task: it needs atmospheric correction, or
#    thresholds expressed as a function of air mass. See README "Known limits".
MIN_SCENE_ELEVATION_DEG = 40.0

# How much of the domain must be inside the calibrated range for the scene to
# be published at all.
#
# On the Kalimantan domain this was effectively 0.5, applied to the whole
# domain at once. Across 47 degrees of longitude — 3.1 hours of solar time —
# that gate leaves a publishing window of roughly four hours and discards
# both Papua's morning and Sumatra's afternoon. The sun test is now per pixel
# (see common.calibrated_mask), so this only has to answer "is there anything
# worth drawing", and the day runs from about 23:15 UTC to 09:00 UTC.
#
# Raise it to shorten the day at both ends without touching the physics.
MIN_CALIBRATED_FRACTION = 0.05

# Scenes below this elevation are published but carry a caveat on the page.
# Between 40 and 50 degrees the mask still agrees with FIRMS (3.5x enrichment
# against 3.8x at the validated point) but the domain smoke fraction roughly
# doubles, and the detections that appear west of Borneo are the least
# trustworthy part of it: that is where coastal sediment and mixed land/water
# pixels live, and where we have no independent confirmation.
# Publish only from local solar afternoon. Elevation alone is not enough:
# yesterday at 14:30 and 47 degrees the mask read 7.16%, today at 08:50 and 46
# degrees it read 30.70% and painted Sumatra, the Malacca Strait and
# Peninsular Malaysia. Same sun height, four times the smoke.
#
# The difference is geometry, not elevation. In the morning the sun is east
# and Himawari sits at 140.7E, also east, so sun and sensor are on the same
# side: near-specular over water and a scattering angle nothing was calibrated
# against, because every scene used to tune this was afternoon. Until the
# morning is understood it is not published.
# Emergency morning block, now disabled. It was set to 12.0 when the morning
# map came out as smoke everywhere; the cause turned out to be the water test
# above rather than anything intrinsic to morning geometry, and with that
# fixed the morning outscores the afternoon on hotspot agreement. Left in
# place as a valve: raise it to withhold early scenes without a code change.
MIN_SCENE_LOCAL_HOUR = 0.0

# Note shown when an unusual share of the detection sits over water.
#
# Shallow turbid water -- the Java Sea shelf east of Lampung, the Sunda and
# Karimata Straits, estuaries -- reads B05 around 6 to 9, between open water
# at ~2 and land at ~16 or more, and it lifts the red band the way thin smoke
# does. Measured on 2026-08-22 those pixels satisfied BOTH branches: B01 28.7,
# blue excess 13.4, B03 15.3, all above their thresholds. Raising WATER_B05_MAX
# to 12 moved them across the boundary and changed nothing, while costing the
# afternoon more than half of West Kalimantan's detections.
#
# There is no threshold that separates them from thin smoke in these seven
# bands. It needs bathymetry, or temporal persistence -- sediment does not
# move and smoke does. Until then the map says which parts to distrust.
WATER_NOTE_ABOVE_FRACTION = 0.10

# One sentence. A caveat people actually read beats a paragraph they skip.
# {pct} is filled in by publish, so the number moves with the scene.
CAVEAT_WATER = (
    "{pct}% of detection is over shallow coastal water, where turbidity looks "
    "like thin smoke — treat it as unconfirmed."
)

# CAVEAT_LOW_SUN and CAVEAT_BELOW_ELEVATION_DEG were removed when sun gating
# went per pixel. The caveat said "Kalimantan is the calibrated part of this
# map", which was true of a scene-level gate on a domain small enough to have
# one sun angle. The uncalibrated hatch now shows exactly which pixels are
# outside the range, on the map, which is better than a sentence saying that
# some of them are.

# satpy hands back raw AHI albedo, which falls as the sun drops. Left
# uncorrected the smoke field appears to shrink every afternoon — an
# artefact optical flow reads as convergence. Dividing by cos(solar zenith)
# makes the thresholds above mean the same thing all day. The floor keeps
# the division from exploding near the terminator.
SUNZ_CORRECT = True

MIN_COS_SZA = 0.15  # ~81 degrees zenith
VISIBLE_BANDS = ["B01", "B02", "B03", "B04", "B05", "B06"]

# Speckle removal: drop smoke blobs smaller than this many grid cells.
SMOKE_MIN_BLOB_CELLS = 12

# Suppress the forecast entirely if less than this fraction of the domain
# is clear enough to see (PLAN.md §6, widespread cloud).
MIN_CLEAR_FRACTION = 0.20

# --------------------------------------------------------------------------
# Advection
# --------------------------------------------------------------------------

FORECAST_STEP_MINUTES = 30
FORECAST_HORIZON_MINUTES = 180
FORECAST_STEPS = list(
    range(
        FORECAST_STEP_MINUTES,
        FORECAST_HORIZON_MINUTES + FORECAST_STEP_MINUTES,
        FORECAST_STEP_MINUTES,
    )
)  # [30, 60, 90, 120, 150, 180]

# Farneback parameters. pyr_scale/levels/winsize are the ones worth touching.
FARNEBACK = dict(
    pyr_scale=0.5,
    levels=4,
    winsize=31,
    iterations=3,
    poly_n=7,
    poly_sigma=1.5,
    flags=0,
)

# Reject the flow field outright if the median displacement implies a wind
# faster than this. Optical flow on a noisy mask can produce nonsense.
MAX_PLAUSIBLE_SPEED_MS = 25.0

# Smooth the flow field before integrating (grid cells, gaussian sigma).
FLOW_SMOOTH_SIGMA = 6.0

# The pair of scenes used for flow must be this far apart, in minutes.
# Too close and the displacement is lost in the noise; too far and the
# frozen-flow assumption stops holding.
MIN_FLOW_PAIR_GAP_MINUTES = 15
MAX_FLOW_PAIR_GAP_MINUTES = 40

# Per-step decay applied to the advected smoke field, representing dispersion.
# 1.0 = no decay.
ADVECTION_DECAY_PER_STEP = 0.94

# --------------------------------------------------------------------------
# FIRMS hotspots
# --------------------------------------------------------------------------

FIRMS_API_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
FIRMS_SOURCES = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "MODIS_NRT"]
# FIRMS counts back N UTC days INCLUDING today, so day_range=1 means "since
# 00:00 UTC", not "the last 24 hours". At 01:30 UTC that returned zero rows
# for this domain while day_range=2 returned 4217 — the layer emptied itself
# every night at 00:00 UTC (07:00 WIB) and stayed empty until the first
# overpass around 06:00 UTC (13:00 WIB), which is most of our publishing day.
#
# Fetch two days and filter to a true rolling window below.
FIRMS_DAY_RANGE = 2
FIRMS_MAX_AGE_HOURS = 24

# Confidence is a weak dial here. Of 5261 live detections on 2026-08-21,
# VIIRS returned 3843 nominal, 151 high and 0 low — so "high only" would
# discard 96% of VIIRS. It flags detection reliability, not fire importance.
# Keep it as a floor that removes junk, and rank by power instead.
FIRMS_MIN_CONFIDENCE = "n"  # nominal or high for VIIRS
FIRMS_MODIS_MIN_CONFIDENCE = 30

# Minimum fire radiative power, MW. This is the knob that controls how busy
# the map is. Measured on the same 5261 detections:
#     >=  0 MW   5261 points, 100% of radiative power
#     >= 10 MW   2223 points,  79%
#     >= 20 MW   1026 points,  58%
#     >= 30 MW    657 points,  45%
#     >= 50 MW    302 points,  30%   <- default
#
# CAVEAT, and it bites this product specifically: Kalimantan's worst haze
# comes from SMOULDERING PEAT, which burns cool and registers low FRP.
# Raising this floor preferentially discards the fires that generate the most
# smoke per unit of heat. It is a readability control, not a relevance one.
# Set to 0 to publish every detection.
FIRMS_MIN_FRP_MW = 50.0
FIRMS_HTTP_TIMEOUT = 60
FIRMS_MAP_KEY_ENV = "FIRMS_MAP_KEY"

# --------------------------------------------------------------------------
# GFS winds (stretch)
# --------------------------------------------------------------------------

GFS_BUCKET_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GFS_LEVEL_HPA = 850
GFS_ARROW_STRIDE_DEG = 1.0  # one arrow per degree

# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

# Header goes red past this age.
STALE_MINUTES = 90

CAPTION = (
    "Indicative short-range smoke movement, daytime only. "
    "Not an official forecast — see BMKG and ASMC."
)

# Smoke overlay ramp: thin smoke is pale, thick smoke is brown.
# Opacity scales with density on top of the colour ramp.
SMOKE_RGB_LIGHT = (255, 216, 150)
SMOKE_RGB = (176, 96, 42)
SMOKE_MIN_ALPHA = 40
SMOKE_MAX_ALPHA = 205

# Density below this is not drawn at all — keeps the map from looking dirty.
SMOKE_DRAW_FLOOR = 0.05

OBSCURED_RGB = (150, 150, 160)
OBSCURED_ALPHA = 90
OBSCURED_HATCH_PERIOD = 10  # pixels between hatch lines
OBSCURED_HATCH_WIDTH = 2

# "Outside the calibrated sun-angle range" is a different statement from
# "cloud in the way", and drawing them the same way would tell the reader
# they are the same problem. Cooler colour, wider spacing, so the two hatches
# are distinguishable at a glance without a legend lookup.
UNCALIBRATED_RGB = (120, 140, 175)
UNCALIBRATED_ALPHA = 80
UNCALIBRATED_HATCH_PERIOD = 16
UNCALIBRATED_HATCH_WIDTH = 2

# Below this daylit fraction the domain is treated as night: the last good
# daylight product is frozen and labelled rather than overwritten.
DAYLIGHT_MIN_FRACTION = 0.25

# Where the map opens. On the old domain this was deliberately NOT the middle
# of the grid: FOCUS_LON pulled the view back onto Borneo so the westward
# extension to Singapore did not shove the subject off to the right. A
# full-country domain has no single subject to pull towards, so this is now
# simply the centre of the data and view_bounds() is the data bounds.
FOCUS_LON = 118.25
FOCUS_LAT = -1.75

# Displayed time. UTC, because the domain spans WIB (UTC+7), WITA (UTC+8)
# and WIT (UTC+9) and no single Indonesian zone describes it. The page shows
# all three local equivalents on a second line, so nobody has to do the
# arithmetic; this is the one clock everything is stamped in.
#
# to_display_tz is the single seam, so publish, smoke_mask and validate all
# follow from these two values.
DISPLAY_TZ_OFFSET_HOURS = 0
DISPLAY_TZ_LABEL = "UTC"

# Cities called out in the site legend. Chosen for coverage rather than
# population: at least one anchor per major island so a reader can locate
# themselves anywhere in the domain, plus the downwind capitals that made
# the transboundary case for the original grid.
CITIES = [
    # Sumatra
    {"name": "Medan", "lat": 3.59, "lon": 98.67},
    {"name": "Pekanbaru", "lat": 0.51, "lon": 101.45},
    {"name": "Palembang", "lat": -2.99, "lon": 104.76},
    # Java
    {"name": "Jakarta", "lat": -6.21, "lon": 106.85},
    {"name": "Surabaya", "lat": -7.25, "lon": 112.75},
    # Kalimantan
    {"name": "Pontianak", "lat": -0.02, "lon": 109.34},
    {"name": "Palangkaraya", "lat": -2.21, "lon": 113.92},
    {"name": "Banjarmasin", "lat": -3.32, "lon": 114.59},
    {"name": "Samarinda", "lat": -0.50, "lon": 117.15},
    {"name": "Balikpapan", "lat": -1.24, "lon": 116.85},
    # Sulawesi and the east
    {"name": "Makassar", "lat": -5.15, "lon": 119.43},
    {"name": "Manado", "lat": 1.47, "lon": 124.84},
    {"name": "Ambon", "lat": -3.65, "lon": 128.19},
    {"name": "Jayapura", "lat": -2.53, "lon": 140.72},
    {"name": "Kupang", "lat": -10.18, "lon": 123.61},
    # Downwind, and the reason the domain still reaches this far west
    {"name": "Singapore", "lat": 1.35, "lon": 103.82},
    {"name": "Kuala Lumpur", "lat": 3.14, "lon": 101.69},
    {"name": "Kuching", "lat": 1.55, "lon": 110.34},
    {"name": "Bandar Seri Begawan", "lat": 4.90, "lon": 114.94},
]
