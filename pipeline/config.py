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

# Extends west to 100 E so the domain covers Singapore, Peninsular Malaysia
# and the Riau/Sumatra fire belt — the question "does the haze reach
# Singapore" cannot be answered by a grid that stops at Borneo. This costs no
# extra download: AHI segments divide the disk by scan line (latitude), so a
# wider longitude range is a bigger crop of files we already fetch.
LON_MIN, LON_MAX = 100.0, 120.0
LAT_MIN, LAT_MAX = -5.0, 8.0
GRID_RES_DEG = 0.02  # ~2.2 km at the equator

# Rows run north -> south (image convention). Values are pixel centres.
GRID_NX = int(round((LON_MAX - LON_MIN) / GRID_RES_DEG))  # 600
GRID_NY = int(round((LAT_MAX - LAT_MIN) / GRID_RES_DEG))  # 650

# Nearest-neighbour radius for resampling AHI -> grid, metres. The western
# edge is ~40 degrees off the sub-satellite point, where a 2 km nadir pixel
# is stretched to 3-4 km, so the radius has to exceed the nadir spacing or
# that edge comes back full of holes.
RESAMPLE_RADIUS_M = 5000

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

# FLDK is split into 10 segments, north to south. Kalimantan straddles the
# equator, which lands mid-disk. Computed properly in fetch_ahi.segments_for_bbox;
# this is the fallback when the geometry calculation is unavailable.
AHI_TOTAL_SEGMENTS = 10
AHI_FALLBACK_SEGMENTS = [5, 6, 7]

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
MIN_SCENE_ELEVATION_DEG = 50.0

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
FIRMS_DAY_RANGE = 1  # last 24 h

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

# Below this daylit fraction the domain is treated as night: the last good
# daylight product is frozen and labelled rather than overwritten.
DAYLIGHT_MIN_FRACTION = 0.25

# Where the map opens. Widening the domain west to Singapore would otherwise
# shove Borneo off to the right, so the initial view is centred on Kalimantan
# and extended east by however much was added in the west. The result keeps
# Kalimantan in the middle with Singapore and Malaysia visible on the left.
FOCUS_LON = 114.0
FOCUS_LAT = 0.5

# Indonesian western time, for the header.
DISPLAY_TZ_OFFSET_HOURS = 7
DISPLAY_TZ_LABEL = "WIB"

# Cities called out in the site legend (stretch: per-city ETA).
CITIES = [
    # Kalimantan
    {"name": "Pontianak", "lat": -0.02, "lon": 109.34},
    {"name": "Palangkaraya", "lat": -2.21, "lon": 113.92},
    {"name": "Banjarmasin", "lat": -3.32, "lon": 114.59},
    {"name": "Samarinda", "lat": -0.50, "lon": 117.15},
    {"name": "Balikpapan", "lat": -1.24, "lon": 116.85},
    {"name": "Kuching", "lat": 1.55, "lon": 110.34},
    # Downwind, and the reason the domain reaches this far west
    {"name": "Singapore", "lat": 1.35, "lon": 103.82},
    {"name": "Kuala Lumpur", "lat": 3.14, "lon": 101.69},
    {"name": "Johor Bahru", "lat": 1.49, "lon": 103.74},
    {"name": "Malacca", "lat": 2.19, "lon": 102.25},
    {"name": "Pekanbaru", "lat": 0.51, "lon": 101.45},
]
