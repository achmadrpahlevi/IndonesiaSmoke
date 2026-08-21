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

LON_MIN, LON_MAX = 108.0, 120.0
LAT_MIN, LAT_MAX = -5.0, 8.0
GRID_RES_DEG = 0.02  # ~2.2 km at the equator

# Rows run north -> south (image convention). Values are pixel centres.
GRID_NX = int(round((LON_MAX - LON_MIN) / GRID_RES_DEG))  # 600
GRID_NY = int(round((LAT_MAX - LAT_MIN) / GRID_RES_DEG))  # 650

# Nearest-neighbour radius for resampling AHI -> grid, metres.
RESAMPLE_RADIUS_M = 3000

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

# UNITS: satpy returns AHI visible bands as PERCENT reflectance (0-100) and
# IR bands as brightness temperature in kelvin. Thresholds below use those
# native units — do not rescale.

# Smoke is bright in the blue band but not cloud-bright.
SMOKE_B01_MIN = 10.0
SMOKE_B01_MAX = 40.0

# Smoke scatters far more blue than red.
SMOKE_B01_MINUS_B03_MIN = 1.5

# THE discriminating test. Aerosol scattering falls off steeply with
# wavelength, so a smoke pall is bright at 0.64 um and nearly invisible at
# 2.3 um. Cloud and bare soil are bright in both, clear vegetation is dark
# in the red and moderately bright in the SWIR (so this goes negative).
SMOKE_B03_MINUS_B06_MIN = 1.0

# Reflectance excess at which the overlay reaches full opacity.
SMOKE_DENSITY_SPAN = 8.0

# Smoke is optically thin in the IR window, so the surface stays warm.
SMOKE_B13_MIN_K = 280.0

# Split-window: water/ice cloud gives B11-B14 near or below zero;
# smoke and dust push it up.
SMOKE_BTD_1114_MIN = -2.5

# Cloud test — anything colder or brighter than this is "obscured",
# never advected (non-negotiable #2). The SWIR pair catches warm low cumulus
# that the 10.4 um temperature test misses.
CLOUD_B13_MAX_K = 280.0
CLOUD_B01_MIN = 38.0
CLOUD_B03_MIN = 22.0
CLOUD_B06_MIN = 18.0

# Minimum solar elevation for the visible bands to be usable.
MIN_SOLAR_ELEVATION_DEG = 12.0

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
FIRMS_MIN_CONFIDENCE = "n"  # nominal or high for VIIRS; >=30 for MODIS
FIRMS_MODIS_MIN_CONFIDENCE = 30
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

# Indonesian western time, for the header.
DISPLAY_TZ_OFFSET_HOURS = 7
DISPLAY_TZ_LABEL = "WIB"

# Cities called out in the site legend (stretch: per-city ETA).
CITIES = [
    {"name": "Pontianak", "lat": -0.02, "lon": 109.34},
    {"name": "Palangkaraya", "lat": -2.21, "lon": 113.92},
    {"name": "Banjarmasin", "lat": -3.32, "lon": 114.59},
    {"name": "Samarinda", "lat": -0.50, "lon": 117.15},
    {"name": "Balikpapan", "lat": -1.24, "lon": 116.85},
]
