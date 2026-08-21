"""Last 24 h of VIIRS + MODIS hotspots from NASA FIRMS, as GeoJSON.

    python -m pipeline.fetch_firms
    FIRMS_MAP_KEY=xxxx python -m pipeline.fetch_firms --days 2

Get a key (free, instant) at https://firms.modaps.eosdis.nasa.gov/api/map_key/
and set it as FIRMS_MAP_KEY locally / as a GitHub Actions secret.

This layer degrades independently of everything else (PLAN.md §6): no key,
a rate limit, or a dead endpoint all fall back to the last good GeoJSON with
a `stale` flag rather than failing the run.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import common
from . import config as C

log = None

OUT_NAME = "firms.geojson"


def map_key() -> str | None:
    key = os.environ.get(C.FIRMS_MAP_KEY_ENV, "").strip()
    return key or None


def area_string() -> str:
    """FIRMS wants west,south,east,north."""
    return f"{C.LON_MIN},{C.LAT_MIN},{C.LON_MAX},{C.LAT_MAX}"


def fetch_source(key: str, source: str, days: int, session=None) -> list[dict]:
    """One FIRMS source -> list of CSV row dicts. Raises on transport error."""
    session = session or requests
    url = f"{C.FIRMS_API_BASE}/{key}/{source}/{area_string()}/{days}"
    resp = session.get(url, timeout=C.FIRMS_HTTP_TIMEOUT)
    resp.raise_for_status()
    text = resp.text.strip()

    # FIRMS signals problems with a plain-text body and a 200 status.
    if not text or text.lower().startswith(("invalid", "error", "you have exceeded")):
        raise RuntimeError(f"{source}: {text[:160] or 'empty response'}")
    if "latitude" not in text.splitlines()[0].lower():
        raise RuntimeError(f"{source}: unexpected response {text[:160]!r}")

    return list(csv.DictReader(io.StringIO(text)))


def frp_ok(row: dict) -> bool:
    """Keep only fires above the radiative-power floor.

    A detection with no FRP reported is kept: absent is not the same as zero,
    and silently dropping unmeasured fires would bias the map.
    """
    if C.FIRMS_MIN_FRP_MW <= 0:
        return True
    raw = (row.get("frp") or "").strip()
    if not raw:
        return True
    try:
        return float(raw) >= C.FIRMS_MIN_FRP_MW
    except ValueError:
        return True


def confidence_ok(row: dict) -> bool:
    """VIIRS confidence is l/n/h; MODIS is 0-100."""
    raw = (row.get("confidence") or "").strip().lower()
    if not raw:
        return True
    if raw in ("l", "n", "h"):
        order = {"l": 0, "n": 1, "h": 2}
        return order.get(raw, 0) >= order.get(C.FIRMS_MIN_CONFIDENCE, 1)
    try:
        return float(raw) >= C.FIRMS_MODIS_MIN_CONFIDENCE
    except ValueError:
        return True


def row_to_feature(row: dict, source: str) -> dict | None:
    try:
        lon = float(row["longitude"])
        lat = float(row["latitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (C.LON_MIN <= lon <= C.LON_MAX and C.LAT_MIN <= lat <= C.LAT_MAX):
        return None

    acq_date = (row.get("acq_date") or "").strip()
    acq_time = (row.get("acq_time") or "").strip().zfill(4)
    when = None
    if acq_date and len(acq_time) == 4:
        try:
            when = datetime.strptime(
                f"{acq_date} {acq_time}", "%Y-%m-%d %H%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            when = None

    def num(field):
        try:
            return float(row[field])
        except (KeyError, TypeError, ValueError):
            return None

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
        "properties": {
            "source": source,
            "satellite": (row.get("satellite") or "").strip(),
            "acq_utc": when.strftime("%Y-%m-%dT%H:%MZ") if when else None,
            "confidence": (row.get("confidence") or "").strip(),
            "frp": num("frp"),
            "brightness": num("bright_ti4") or num("brightness"),
            "daynight": (row.get("daynight") or "").strip(),
        },
    }


def collect(days: int, session=None) -> tuple[list[dict], list[str], int]:
    key = map_key()
    if not key:
        raise RuntimeError(
            f"{C.FIRMS_MAP_KEY_ENV} is not set — get one at "
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/"
        )
    features: list[dict] = []
    used: list[str] = []
    errors: list[str] = []
    dropped: dict = {}
    for source in C.FIRMS_SOURCES:
        try:
            rows = fetch_source(key, source, days, session=session)
        except (requests.RequestException, RuntimeError) as exc:
            log.warning("FIRMS %s unavailable: %s", source, exc)
            errors.append(str(exc))
            continue
        kept = weak = 0
        for row in rows:
            if not confidence_ok(row):
                continue
            feat = row_to_feature(row, source)
            if not feat:
                continue
            if not frp_ok(row):
                weak += 1
                continue
            features.append(feat)
            kept += 1
        used.append(source)
        dropped[source] = weak
        log.info(
            "FIRMS %s: %d rows, %d kept, %d below %.0f MW",
            source, len(rows), kept, weak, C.FIRMS_MIN_FRP_MW,
        )

    if not used:
        raise RuntimeError("; ".join(errors) or "no FIRMS source responded")
    return features, used, sum(dropped.values())


def write_geojson(
    features: list[dict],
    sources: list[str],
    stale: bool,
    note: str,
    below_frp: int = 0,
):
    out = Path(C.SITE_DATA_DIR) / OUT_NAME
    payload = {
        "type": "FeatureCollection",
        "properties": {
            "generated_utc": common.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": sources,
            "day_range": C.FIRMS_DAY_RANGE,
            "count": len(features),
            "min_frp_mw": C.FIRMS_MIN_FRP_MW,
            "below_frp_floor": below_frp,
            "stale": stale,
            "note": note,
        },
        "features": features,
    }
    common.write_json(out, payload)
    log.info("wrote %s: %d hotspots%s", out, len(features), " (STALE)" if stale else "")
    return out


def degrade(reason: str) -> int:
    """Keep the last good layer rather than publishing an empty one."""
    out = Path(C.SITE_DATA_DIR) / OUT_NAME
    existing = common.read_json(out)
    if existing and existing.get("features") is not None:
        props = existing.setdefault("properties", {})
        props["stale"] = True
        props["note"] = reason
        props["checked_utc"] = common.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        common.write_json(out, existing)
        log.warning(
            "kept %d cached hotspots; %s", len(existing.get("features", [])), reason
        )
    else:
        write_geojson([], [], stale=True, note=reason)
        log.warning("no cached hotspots to fall back on; %s", reason)
    # Hotspots are a secondary layer. Never fail the pipeline over them.
    return 0


def main(argv=None) -> int:
    global log
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--days", type=int, default=C.FIRMS_DAY_RANGE)
    p.add_argument(
        "--date", help="accepted for symmetry with the other stages; unused"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    log = common.setup_logging(args.verbose)
    common.ensure_dirs(C.SITE_DATA_DIR)

    try:
        features, sources, below = collect(args.days)
    except RuntimeError as exc:
        return degrade(str(exc))

    write_geojson(features, sources, stale=False, note="", below_frp=below)
    if below:
        log.info(
            "%d detections below the %.0f MW floor were not published "
            "(config.FIRMS_MIN_FRP_MW)",
            below, C.FIRMS_MIN_FRP_MW,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
