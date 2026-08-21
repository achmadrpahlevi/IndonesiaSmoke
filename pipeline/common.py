"""Shared helpers: the fixed grid, npz state I/O, time and sun geometry.

Deliberately small. Anything a human will want to tweak belongs in config.py,
not here.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import config as C


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # satpy and friends are chatty at INFO.
    for noisy in ("satpy", "pyresample", "pyproj", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("kalimsmoke")


# --------------------------------------------------------------------------
# The fixed output grid
# --------------------------------------------------------------------------

def grid_lons() -> np.ndarray:
    """Pixel-centre longitudes, west to east."""
    return C.LON_MIN + (np.arange(C.GRID_NX) + 0.5) * C.GRID_RES_DEG


def grid_lats() -> np.ndarray:
    """Pixel-centre latitudes, north to south (image row order)."""
    return C.LAT_MAX - (np.arange(C.GRID_NY) + 0.5) * C.GRID_RES_DEG


def grid_mesh() -> tuple[np.ndarray, np.ndarray]:
    lon2d, lat2d = np.meshgrid(grid_lons(), grid_lats())
    return lon2d, lat2d


def grid_area_def():
    """pyresample AreaDefinition for the published grid.

    Imported lazily so that pure-logic tests do not need pyresample.
    """
    from pyresample.geometry import AreaDefinition

    return AreaDefinition(
        area_id="kalimantan",
        description="Kalimantan smoke domain, plate carree",
        proj_id="eqc",
        projection={"proj": "longlat", "datum": "WGS84"},
        width=C.GRID_NX,
        height=C.GRID_NY,
        area_extent=(C.LON_MIN, C.LAT_MIN, C.LON_MAX, C.LAT_MAX),
    )


def leaflet_bounds() -> list[list[float]]:
    """[[south, west], [north, east]] — what L.imageOverlay wants."""
    return [[C.LAT_MIN, C.LON_MIN], [C.LAT_MAX, C.LON_MAX]]


def view_bounds() -> list[list[float]]:
    """Opening view: the data bounds mirrored about the focus point.

    Fitting the raw data bounds would centre the map on the middle of the
    grid, which after the westward extension is the Java Sea. Mirroring about
    Kalimantan keeps the subject centred while still showing everything to
    its west.
    """
    west = min(C.LON_MIN, 2 * C.FOCUS_LON - C.LON_MAX)
    east = max(C.LON_MAX, 2 * C.FOCUS_LON - C.LON_MIN)
    south = min(C.LAT_MIN, 2 * C.FOCUS_LAT - C.LAT_MAX)
    north = max(C.LAT_MAX, 2 * C.FOCUS_LAT - C.LAT_MIN)
    return [[south, west], [north, east]]


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

SLOT_FMT = "%Y%m%d_%H%M"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=timezone.utc)


def floor_to_slot(dt: datetime, minutes: int = C.AHI_SLOT_MINUTES) -> datetime:
    """Round a time down to the AHI observation slot containing it."""
    dt = dt.astimezone(timezone.utc)
    return dt.replace(
        minute=(dt.minute // minutes) * minutes, second=0, microsecond=0
    )


def slot_id(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(SLOT_FMT)


def parse_slot_id(text: str) -> datetime:
    return datetime.strptime(text, SLOT_FMT).replace(tzinfo=timezone.utc)


def parse_cli_datetime(text: str) -> datetime:
    """Accept the several shapes a human types into --date."""
    text = text.strip()
    for fmt in ("%Y%m%d_%H%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y%m%d%H%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(
        f"unrecognised datetime {text!r}; try 20260821_0600 or 2026-08-21T06:00"
    )


def to_display_tz(dt: datetime) -> datetime:
    return dt.astimezone(timezone(timedelta(hours=C.DISPLAY_TZ_OFFSET_HOURS)))


def minutes_between(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 60.0


# --------------------------------------------------------------------------
# Sun geometry — NOAA low-precision solar position, good to ~0.1 deg.
# Local implementation so the mask has no extra runtime dependency.
# --------------------------------------------------------------------------

def solar_elevation(dt: datetime, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Solar elevation in degrees for arrays of lat/lon at time `dt` (UTC)."""
    dt = dt.astimezone(timezone.utc)
    day_of_year = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0

    # Fractional year, radians.
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1 + (hour - 12) / 24.0)

    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    lat_r = np.radians(lat)
    # True solar time in minutes, then hour angle.
    time_offset = eqtime + 4.0 * lon
    tst = hour * 60.0 + time_offset
    ha = np.radians(tst / 4.0 - 180.0)

    cos_zen = np.sin(lat_r) * math.sin(decl) + np.cos(lat_r) * math.cos(decl) * np.cos(ha)
    cos_zen = np.clip(cos_zen, -1.0, 1.0)
    return 90.0 - np.degrees(np.arccos(cos_zen))


def domain_is_daylit(dt: datetime) -> tuple[bool, float]:
    """(scene is inside the calibrated sun-angle range, mean solar elevation).

    Deliberately the SCENE-level test, not the per-pixel visibility one. A
    scene can be perfectly visible and still be outside the range the mask
    thresholds were tuned in, in which case it is withheld rather than
    published as a confident map of haze.
    """
    lats = grid_lats()[::40]
    lons = grid_lons()[::40]
    lon2d, lat2d = np.meshgrid(lons, lats)
    elev = solar_elevation(dt, lat2d, lon2d)
    frac_lit = float(np.mean(elev >= C.MIN_SCENE_ELEVATION_DEG))
    return frac_lit >= 0.5, float(np.mean(elev))


# --------------------------------------------------------------------------
# State I/O
# --------------------------------------------------------------------------

def ensure_dirs(*paths: Path) -> None:
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, payload: dict) -> None:
    atomic_write_bytes(
        Path(path), json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
    )


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def scene_path(slot: datetime) -> Path:
    return Path(C.STATE_DIR) / f"scene_{slot_id(slot)}.npz"


def mask_path(slot: datetime) -> Path:
    return Path(C.STATE_DIR) / f"mask_{slot_id(slot)}.npz"


def list_state(prefix: str) -> list[tuple[datetime, Path]]:
    """All saved scenes/masks, oldest first."""
    out = []
    state = Path(C.STATE_DIR)
    if not state.is_dir():
        return out
    for p in sorted(state.glob(f"{prefix}_*.npz")):
        stem = p.stem[len(prefix) + 1 :]
        try:
            out.append((parse_slot_id(stem), p))
        except ValueError:
            continue
    out.sort(key=lambda t: t[0])
    return out


def prune_state(prefix: str, keep: int = 8, drop_dark: bool = True) -> None:
    """State is a rolling window; advection only ever needs the last two.

    Dark slots are dropped first, before the newest-N rule is applied. A scene
    from last night is newer than every daylight scene of the day, so a plain
    newest-N window fills up with darkness and evicts the very partner a
    backfill just downloaded — which is how a freshly fetched flow partner
    ended up deleted seconds after arriving.
    """
    entries = list_state(prefix)
    if drop_dark:
        keepable = []
        for slot, path in entries:
            if domain_is_daylit(slot)[0]:
                keepable.append((slot, path))
            else:
                try:
                    path.unlink()
                except OSError:
                    pass
        entries = keepable
    for _, path in entries[:-keep] if len(entries) > keep else []:
        try:
            path.unlink()
        except OSError:
            pass
