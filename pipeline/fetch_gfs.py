"""GFS 850 hPa wind arrows — the sanity check on the optical flow. (Stretch.)

    python -m pipeline.fetch_gfs
    python -m pipeline.fetch_gfs --date 20260821_0600

Downloads only the two GRIB messages it needs. The public GFS bucket ships a
`.idx` sidecar listing every message's byte offset, so a pair of HTTP range
requests fetches ~1 MB instead of the 500 MB file.

Optional in every sense: it needs cfgrib, and if cfgrib or the data is
missing it writes nothing and exits 0. The smoke product never depends on it.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from . import common
from . import config as C

log = None

OUT_NAME = "wind.geojson"
CYCLE_HOURS = [0, 6, 12, 18]
# GFS lands on the bucket roughly 3.5 h after the cycle time.
CYCLE_LATENCY_HOURS = 4


def latest_cycle(now: datetime) -> datetime:
    ref = now - timedelta(hours=CYCLE_LATENCY_HOURS)
    hour = max(h for h in CYCLE_HOURS if h <= ref.hour)
    return ref.replace(hour=hour, minute=0, second=0, microsecond=0)


def forecast_hour(cycle: datetime, valid: datetime) -> int:
    """Nearest available lead time, clamped to what GFS publishes hourly."""
    lead = (valid - cycle).total_seconds() / 3600.0
    return int(max(0, min(120, round(lead))))


def grib_url(cycle: datetime, fhour: int) -> str:
    return (
        f"{C.GFS_BUCKET_BASE}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/"
        f"gfs.t{cycle:%H}z.pgrb2.0p25.f{fhour:03d}"
    )


def byte_ranges(idx_text: str, wanted: list[str]) -> dict:
    """Parse a GRIB .idx into {name: (start, end)} byte ranges.

    Lines look like:  12:5100234:d=2026082106:UGRD:850 mb:6 hour fcst:
    """
    lines = [l for l in idx_text.splitlines() if l.strip()]
    offsets = []
    for line in lines:
        parts = line.split(":")
        if len(parts) < 3:
            continue
        try:
            offsets.append((int(parts[1]), line))
        except ValueError:
            continue

    found = {}
    for i, (start, line) in enumerate(offsets):
        for name in wanted:
            if name in line:
                end = offsets[i + 1][0] - 1 if i + 1 < len(offsets) else ""
                found[name] = (start, end)
    return found


def fetch_messages(url: str, ranges: dict, session=None) -> bytes:
    session = session or requests
    blobs = []
    for name, (start, end) in sorted(ranges.items(), key=lambda kv: kv[1][0]):
        headers = {"Range": f"bytes={start}-{end}"}
        resp = session.get(url, headers=headers, timeout=C.FIRMS_HTTP_TIMEOUT)
        resp.raise_for_status()
        log.debug("%s: %d bytes", name, len(resp.content))
        blobs.append(resp.content)
    return b"".join(blobs)


def to_geojson(u, v, lats, lons, cycle, fhour, valid) -> dict:
    features = []
    step = max(1, int(round(C.GFS_ARROW_STRIDE_DEG / 0.25)))
    for j in range(0, len(lats), step):
        for i in range(0, len(lons), step):
            uu = float(u[j, i])
            vv = float(v[j, i])
            if not (np.isfinite(uu) and np.isfinite(vv)):
                continue
            speed = float(np.hypot(uu, vv))
            # Meteorological convention: the direction the wind comes FROM.
            bearing_from = (np.degrees(np.arctan2(-uu, -vv)) + 360.0) % 360.0
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(float(lons[i]), 3), round(float(lats[j]), 3)],
                    },
                    "properties": {
                        "u_ms": round(uu, 2),
                        "v_ms": round(vv, 2),
                        "speed_ms": round(speed, 2),
                        "bearing_from_deg": round(float(bearing_from), 1),
                    },
                }
            )
    return {
        "type": "FeatureCollection",
        "properties": {
            "level_hpa": C.GFS_LEVEL_HPA,
            "cycle_utc": cycle.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "forecast_hour": fhour,
            "valid_utc": valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_utc": common.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(features),
        },
        "features": features,
    }


def build(valid: datetime, session=None) -> dict:
    try:
        import xarray as xr  # noqa: F401
        import cfgrib  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(f"cfgrib not installed ({exc}); wind arrows skipped")

    session = session or requests.Session()
    cycle = latest_cycle(valid)
    fhour = forecast_hour(cycle, valid)
    url = grib_url(cycle, fhour)
    log.info("GFS %s f%03d", cycle.strftime("%Y-%m-%d %HZ"), fhour)

    idx = session.get(url + ".idx", timeout=C.FIRMS_HTTP_TIMEOUT)
    idx.raise_for_status()
    wanted = [f":UGRD:{C.GFS_LEVEL_HPA} mb:", f":VGRD:{C.GFS_LEVEL_HPA} mb:"]
    ranges = byte_ranges(idx.text, wanted)
    if len(ranges) != 2:
        raise RuntimeError(f"could not locate {wanted} in the GRIB index")

    blob = fetch_messages(url, ranges, session=session)

    import xarray as xr

    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as fh:
        fh.write(blob)
        tmp = Path(fh.name)
    try:
        ds = xr.open_dataset(
            tmp, engine="cfgrib", backend_kwargs={"indexpath": ""}
        )
        sub = ds.sel(
            latitude=slice(C.LAT_MAX, C.LAT_MIN),
            longitude=slice(C.LON_MIN, C.LON_MAX),
        )
        out = to_geojson(
            sub["u"].values,
            sub["v"].values,
            sub["latitude"].values,
            sub["longitude"].values,
            cycle,
            fhour,
            cycle + timedelta(hours=fhour),
        )
        ds.close()
    finally:
        tmp.unlink(missing_ok=True)
    return out


def main(argv=None) -> int:
    global log
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="valid time, default: latest scene or now")
    p.add_argument("--outdir", default=str(C.SITE_DATA_DIR))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    log = common.setup_logging(args.verbose)

    if args.date:
        valid = common.parse_cli_datetime(args.date)
    else:
        scenes = common.list_state("scene")
        valid = scenes[-1][0] if scenes else common.utcnow()

    try:
        gj = build(valid)
    except Exception as exc:  # stretch layer: never fail the run
        log.warning("wind arrows unavailable: %s", exc)
        return 0

    common.write_json(Path(args.outdir) / OUT_NAME, gj)
    log.info("wrote %s: %d arrows", OUT_NAME, gj["properties"]["count"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
