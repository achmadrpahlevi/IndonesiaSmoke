"""Fetch the latest usable Himawari-9 scene and put it on the Kalimantan grid.

    python -m pipeline.fetch_ahi                 # latest available scene
    python -m pipeline.fetch_ahi --date 20260821_0600
    python -m pipeline.fetch_ahi --date 20260821_0600 --back 3   # + 3 earlier slots

Reads the public noaa-himawari9 bucket over anonymous HTTPS (no credentials,
no s3fs). Downloads only the band/segment files that intersect the domain,
loads them with satpy, resamples to the fixed plate carree grid and writes
state/scene_<slot>.npz. Raw HSD is deleted before the function returns —
PLAN.md non-negotiable #4.

Never raises on a missing or partial scene: it walks backwards through slots
and reports what it found.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import warnings
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests

from . import config as C
from . import common

log = None  # set in main()

S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

# Resolution token used in the object names, per band.
BAND_RES_TOKEN = {
    "B01": "R10",
    "B02": "R10",
    "B03": "R05",
    "B04": "R10",
}


def res_token(band: str) -> str:
    return BAND_RES_TOKEN.get(band, "R20")


# --------------------------------------------------------------------------
# Bucket listing
# --------------------------------------------------------------------------

def slot_prefix(slot: datetime) -> str:
    return (
        f"{C.AHI_PRODUCT}/{slot:%Y}/{slot:%m}/{slot:%d}/{slot:%H%M}/"
    )


def s3_list(prefix: str, max_keys: int = 1000, session=None) -> list[str]:
    """Object keys under `prefix`. Anonymous, paginated."""
    session = session or requests
    keys: list[str] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": str(max_keys)}
        if token:
            params["continuation-token"] = token
        resp = session.get(
            C.AHI_S3_BASE, params=params, timeout=C.AHI_HTTP_TIMEOUT
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for node in root.findall(f"{S3_NS}Contents"):
            key = node.findtext(f"{S3_NS}Key")
            if key:
                keys.append(key)
        if root.findtext(f"{S3_NS}IsTruncated") == "true":
            token = root.findtext(f"{S3_NS}NextContinuationToken")
            if not token:
                break
        else:
            break
    return keys


# --------------------------------------------------------------------------
# Which FLDK segments touch Kalimantan
# --------------------------------------------------------------------------

def segments_for_bbox(
    lon_min: float = C.LON_MIN,
    lon_max: float = C.LON_MAX,
    lat_min: float = C.LAT_MIN,
    lat_max: float = C.LAT_MAX,
    margin: int = 1,
) -> list[int]:
    """1-based FLDK segment numbers covering the bbox.

    Segments split the disk into `AHI_TOTAL_SEGMENTS` equal bands of scan
    lines, north to south. The fraction of the disk a latitude falls at is
    resolution independent, so one calculation serves every band.
    """
    try:
        from pyproj import Transformer
    except ImportError:  # pragma: no cover - pyproj ships with pyresample
        return list(C.AHI_FALLBACK_SEGMENTS)

    proj = (
        f"+proj=geos +lon_0={C.AHI_SATELLITE_LON} +h=35785863 "
        "+a=6378137 +b=6356752.3 +units=m +sweep=y +no_defs"
    )
    try:
        tf = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
        lons = np.linspace(lon_min, lon_max, 7)
        lats = np.linspace(lat_min, lat_max, 7)
        lon2d, lat2d = np.meshgrid(lons, lats)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _, y = tf.transform(lon2d.ravel(), lat2d.ravel())
        y = np.asarray(y, dtype=float)
        y = y[np.isfinite(y)]
        if y.size == 0:
            return list(C.AHI_FALLBACK_SEGMENTS)
    except Exception:  # pragma: no cover - defensive
        return list(C.AHI_FALLBACK_SEGMENTS)

    y_max = 5500000.0  # half the full-disk extent, metres
    frac = (y_max - y) / (2 * y_max)
    frac = np.clip(frac, 0.0, 0.999999)
    seg = np.floor(frac * C.AHI_TOTAL_SEGMENTS).astype(int) + 1

    lo = max(1, int(seg.min()) - margin)
    hi = min(C.AHI_TOTAL_SEGMENTS, int(seg.max()) + margin)
    return list(range(lo, hi + 1))


def wanted_keys(slot: datetime, segments: list[int], bands: list[str]) -> list[str]:
    prefix = slot_prefix(slot)
    out = []
    for band in bands:
        for seg in segments:
            name = (
                f"HS_H09_{slot:%Y%m%d}_{slot:%H%M}_{band}_FLDK_"
                f"{res_token(band)}_S{seg:02d}{C.AHI_TOTAL_SEGMENTS:02d}.DAT.bz2"
            )
            out.append(prefix + name)
    return out


# --------------------------------------------------------------------------
# Scene discovery
# --------------------------------------------------------------------------

def find_scene(
    target: datetime,
    lookback: int = C.AHI_MAX_SLOT_LOOKBACK,
    bands: list[str] | None = None,
    segments: list[int] | None = None,
    session=None,
):
    """Walk back from `target` until every wanted file exists.

    Returns (slot, keys) or (None, []). AHI housekeeping gaps and partially
    uploaded slots are both handled by simply trying the previous slot.
    """
    bands = bands or C.AHI_BANDS
    segments = segments or segments_for_bbox()
    slot = common.floor_to_slot(target)
    for attempt in range(lookback):
        candidate = slot - timedelta(minutes=attempt * C.AHI_SLOT_MINUTES)
        try:
            available = set(s3_list(slot_prefix(candidate), session=session))
        except requests.RequestException as exc:
            log.warning("listing %s failed: %s", candidate, exc)
            continue
        if not available:
            log.debug("slot %s empty", common.slot_id(candidate))
            continue
        keys = wanted_keys(candidate, segments, bands)
        missing = [k for k in keys if k not in available]
        if missing:
            log.debug(
                "slot %s incomplete, %d/%d files missing",
                common.slot_id(candidate),
                len(missing),
                len(keys),
            )
            continue
        log.info(
            "scene %s complete: %d files, segments %s",
            common.slot_id(candidate),
            len(keys),
            segments,
        )
        return candidate, keys
    return None, []


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def download_keys(keys: list[str], dest: Path, session=None) -> list[Path]:
    session = session or requests.Session()
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    total = 0
    for key in keys:
        out = dest / Path(key).name
        url = f"{C.AHI_S3_BASE}/{key}"
        for attempt in range(1, C.AHI_HTTP_RETRIES + 1):
            try:
                with session.get(
                    url, stream=True, timeout=C.AHI_HTTP_TIMEOUT
                ) as resp:
                    resp.raise_for_status()
                    with open(out, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            fh.write(chunk)
                break
            except (requests.RequestException, OSError) as exc:
                log.warning("download %s attempt %d failed: %s", out.name, attempt, exc)
                out.unlink(missing_ok=True)
                if attempt == C.AHI_HTTP_RETRIES:
                    raise
        total += out.stat().st_size
        paths.append(out)
    log.info("downloaded %d files, %.0f MB", len(paths), total / 1e6)
    return paths


# --------------------------------------------------------------------------
# satpy load + resample
# --------------------------------------------------------------------------

def load_to_grid(paths: list[Path], bands: list[str]) -> dict:
    """HSD files -> {band: 2D float32 on the published grid}."""
    import tempfile

    from satpy import Scene

    # satpy decompresses each .bz2 to a tempfile and removes it in __del__.
    # Point that at our scratch dir so anything it fails to clean up (Windows
    # keeps the mmap open) still dies with the rest of work/.
    tempfile.tempdir = str(Path(C.WORK_DIR))

    area = common.grid_area_def()
    datasets = [C.AHI_DATASETS[b] for b in bands]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        scn = Scene(reader="ahi_hsd", filenames=[str(p) for p in paths])
        # pad_data=False keeps memory to the segments we actually fetched
        # instead of allocating the whole 22000-line disk for B03.
        scn.load(datasets, pad_data=False)
        cropped = scn.crop(
            ll_bbox=(C.LON_MIN, C.LAT_MIN, C.LON_MAX, C.LAT_MAX)
        )
        resampled = cropped.resample(
            area,
            resampler="nearest",
            radius_of_influence=C.RESAMPLE_RADIUS_M,
            reduce_data=True,
        )

        out = {}
        for band, name in zip(bands, datasets):
            arr = np.asarray(resampled[name].values, dtype=np.float32)
            if arr.shape != (C.GRID_NY, C.GRID_NX):
                raise RuntimeError(
                    f"{band}: got shape {arr.shape}, expected "
                    f"{(C.GRID_NY, C.GRID_NX)}"
                )
            out[band] = arr

    # satpy holds the unzipped temp files open until the Scene is dropped.
    del scn, cropped, resampled
    return out


def save_scene(slot: datetime, grids: dict) -> Path:
    path = common.scene_path(slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    coverage = {}
    payload = {}
    for band, arr in grids.items():
        payload[band] = arr
        coverage[band] = float(np.mean(np.isfinite(arr)))
    np.savez_compressed(
        path,
        slot=common.slot_id(slot),
        bands=np.array(sorted(grids), dtype=object),
        **payload,
    )
    log.info(
        "wrote %s (%.1f MB) coverage %s",
        path.name,
        path.stat().st_size / 1e6,
        {b: f"{v:.0%}" for b, v in coverage.items()},
    )
    return path


def load_scene_npz(path: Path) -> dict:
    with np.load(path, allow_pickle=True) as data:
        bands = [str(b) for b in data["bands"]]
        grids = {b: data[b] for b in bands}
        grids["_slot"] = str(data["slot"])
    return grids


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def fetch_one(target: datetime, force: bool = False, session=None):
    """Fetch, grid and store one scene. Returns the slot, or None."""
    segments = segments_for_bbox()
    slot, keys = find_scene(target, segments=segments, session=session)
    if slot is None:
        log.error("no complete AHI scene within lookback window of %s", target)
        return None

    out_path = common.scene_path(slot)
    if out_path.exists() and not force:
        log.info("%s already gridded, skipping (use --force to redo)", out_path.name)
        return slot

    work = Path(C.WORK_DIR) / common.slot_id(slot)
    try:
        paths = download_keys(keys, work, session=session)
        grids = load_to_grid(paths, C.AHI_BANDS)
        save_scene(slot, grids)
    finally:
        # Non-negotiable #4 — raw HSD dies here, success or failure.
        shutil.rmtree(work, ignore_errors=True)
        log.debug("cleared %s", work)
    return slot


def main(argv=None) -> int:
    global log
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        help="target UTC time, e.g. 20260821_0600 (default: now)",
    )
    parser.add_argument(
        "--back",
        type=int,
        default=0,
        help="also fetch N earlier slots, for backfill and flow pairs",
    )
    parser.add_argument(
        "--ensure-pair",
        action="store_true",
        help=(
            "after the latest scene, fetch an earlier one if state/ has no "
            "partner close enough to compute optical flow from (cold cache)"
        ),
    )
    parser.add_argument(
        "--skip-night",
        action="store_true",
        help=(
            "exit immediately when the domain is dark. The mask needs visible "
            "bands, so a night scene is ~240 MB fetched to produce nothing"
        ),
    )
    parser.add_argument("--force", action="store_true", help="re-grid even if present")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    log = common.setup_logging(args.verbose)

    # A previous run may have died, or (on Windows) failed to unlink files
    # satpy still had memory-mapped. Those locks are gone now, so start clean.
    shutil.rmtree(C.WORK_DIR, ignore_errors=True)
    common.ensure_dirs(C.STATE_DIR, C.WORK_DIR)

    target = (
        common.parse_cli_datetime(args.date) if args.date else common.utcnow()
    )

    if args.skip_night:
        lit, elev = common.domain_is_daylit(target)
        if not lit:
            log.info(
                "domain is dark at %s (mean solar elevation %.0f deg) — "
                "skipping the fetch; the published product stays frozen",
                common.slot_id(target),
                elev,
            )
            return 0

    session = requests.Session()
    got = []
    cursor = target
    for i in range(args.back + 1):
        slot = fetch_one(cursor, force=args.force, session=session)
        if slot is None:
            break
        got.append(slot)
        cursor = slot - timedelta(minutes=C.AHI_SLOT_MINUTES)

    if args.ensure_pair and got:
        latest = got[0]
        existing = [s for s, _ in common.list_state("scene") if s < latest]
        gaps = [common.minutes_between(latest, s) for s in existing]
        if not any(
            C.MIN_FLOW_PAIR_GAP_MINUTES <= g <= C.MAX_FLOW_PAIR_GAP_MINUTES
            for g in gaps
        ):
            partner = latest - timedelta(minutes=C.FORECAST_STEP_MINUTES)
            log.info(
                "no flow partner for %s in state — fetching %s",
                common.slot_id(latest),
                common.slot_id(partner),
            )
            extra = fetch_one(partner, session=session)
            if extra is not None:
                got.append(extra)

    common.prune_state("scene", keep=6)

    if not got:
        log.error("nothing fetched")
        return 1
    log.info("fetched %s", [common.slot_id(s) for s in got])
    print(common.slot_id(got[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
