"""Turn a gridded AHI scene into smoke / cloud-obscured / clear.

    python -m pipeline.smoke_mask                    # every scene without a mask
    python -m pipeline.smoke_mask --date 20260821_0600 --force
    python -m pipeline.smoke_mask --all --qa qa/     # + diagnostic PNGs and a GIF

The QA output is the Saturday PM step in PLAN.md: it writes, per scene, a
side-by-side of a natural-ish colour composite and the derived mask, plus an
animated GIF of the day. Eyeball those against BMKG's public smoke RGB and
tune the thresholds in config.py until the brown areas line up. That
comparison IS the QA step.

Every threshold lives in config.py. Nothing here is tuned inline.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import common
from . import config as C
from .fetch_ahi import load_scene_npz

log = None


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def sunz_correct(band: np.ndarray, elevation: np.ndarray) -> np.ndarray:
    """Raw AHI albedo -> reflectance normalised for solar zenith angle.

    Without this the same smoke reads ~30% fainter at 16:00 than at 13:00 and
    the mask quietly shrinks through every afternoon.
    """
    arr = np.asarray(band, dtype=np.float32)
    if not C.SUNZ_CORRECT:
        return arr
    mu = np.clip(np.sin(np.radians(elevation)), C.MIN_COS_SZA, 1.0)
    return (arr / mu).astype(np.float32)



def classify(grids: dict, slot: datetime) -> dict:
    """Scene bands -> masks.

    Returns a dict with:
      smoke     float32 0-1 density (0 where not smoke)
      smoke_bin uint8  binary smoke
      obscured  uint8  cloud, missing data, or too dark to judge
      clear     uint8  usable and smoke-free
      stats     dict of coverage fractions
    """
    lon2d, lat2d = common.grid_mesh()
    elev = common.solar_elevation(slot, lat2d, lon2d)

    b01, b03, b05, b06 = (
        sunz_correct(grids[b], elev) for b in ("B01", "B03", "B05", "B06")
    )

    # Water is identified from the UNCORRECTED 1.6 um band. Rayleigh is
    # negligible there (tau 0.0013) and the water test is a surface property,
    # so correcting it would only add noise.
    b05_surface = b05
    b01_toa, b03_toa = b01, b03   # water branch stays on uncorrected values
    if C.RAYLEIGH_CORRECT:
        from . import rayleigh

        sza = 90.0 - elev
        saz = rayleigh.solar_azimuth(slot, lat2d, lon2d)
        vza = rayleigh.view_zenith(lat2d, lon2d)
        vaz = rayleigh.view_azimuth(lat2d, lon2d)
        raa = np.abs(saz - vaz)
        raa = np.where(raa > 180.0, 360.0 - raa, raa)
        b01 = b01 - rayleigh.path_reflectance("B01", sza, vza, raa)
        b03 = b03 - rayleigh.path_reflectance("B03", sza, vza, raa)
        b06 = b06 - rayleigh.path_reflectance("B06", sza, vza, raa)
    b11 = grids["B11"].astype(np.float32)
    b13 = grids["B13"].astype(np.float32)
    b14 = grids["B14"].astype(np.float32)

    valid = (
        np.isfinite(b01)
        & np.isfinite(b03)
        & np.isfinite(b05)
        & np.isfinite(b06)
        & np.isfinite(b13)
        & np.isfinite(b14)
    )

    # Water absorbs 1.6 um almost completely, land does not: ~2% against ~16%.
    # The cutoff sits well above pure water so that mixed coastal pixels —
    # tidal flats, estuaries, mangrove — fall on the water side and face the
    # stricter test, rather than passing as land with a sediment signature.
    water = valid & (b05_surface < C.WATER_B05_MAX)

    daylit = elev >= C.MIN_SOLAR_ELEVATION_DEG

    # Cloud: cold tops, blinding brightness, or bright in visible AND SWIR
    # at once — which smoke never is.
    cloud = np.where(
        valid,
        (b13 < C.CLOUD_B13_MAX_K)
        | (b01 > C.CLOUD_B01_MIN)
        | ((b03 > C.CLOUD_B03_MIN) & (b06 > C.CLOUD_B06_MIN)),
        False,
    )

    # Never claim to see through cloud, missing data or darkness.
    obscured = (~valid) | (~daylit) | cloud

    with np.errstate(invalid="ignore", divide="ignore"):
        blue_excess = b01 - b03
        vis_swir = b03 - b06  # the smoke discriminator
        btd = b11 - b14

    usable = valid & daylit & ~cloud & (b13 >= C.SMOKE_B13_MIN_K) & (btd >= C.SMOKE_BTD_1114_MIN)

    over_land = (
        (b01 >= C.SMOKE_B01_MIN)
        & (blue_excess >= C.SMOKE_B01_MINUS_B03_MIN)
        & (vis_swir >= C.SMOKE_B03_MINUS_B06_MIN)
    )

    # Sediment fakes the SWIR contrast, so over water lean on blue instead.
    #
    # This branch deliberately uses UNCORRECTED reflectance. Over dark water
    # the measured signal is mostly atmosphere, and these sediment-rejection
    # thresholds were validated against raw values across several scenes.
    # Recalibrating them for Rayleigh-corrected input at a single scene did
    # not generalise: it held at 14:00 and let 73% of the noon detections back
    # in over water, with the Malacca Strait returning to 6.4%.
    over_water = (
        (b01_toa >= C.WATER_SMOKE_B01_MIN)
        & (b01_toa <= C.SMOKE_B01_MAX)
        & ((b01_toa - b03_toa) >= C.WATER_SMOKE_B01_MINUS_B03_MIN)
    )

    smoke_bin = usable & np.where(water, over_water, over_land)

    smoke_bin = remove_small_blobs(smoke_bin, C.SMOKE_MIN_BLOB_CELLS)

    density = np.clip(
        (vis_swir - C.SMOKE_B03_MINUS_B06_MIN) / C.SMOKE_DENSITY_SPAN, 0.0, 1.0
    ).astype(np.float32)
    density = np.where(smoke_bin, np.maximum(density, 0.15), 0.0).astype(np.float32)

    n = float(smoke_bin.size)
    stats = {
        "water_fraction": float(water.sum() / n),
        "valid_fraction": float(valid.sum() / n),
        "daylit_fraction": float(daylit.sum() / n),
        "cloud_fraction": float(cloud.sum() / n),
        "obscured_fraction": float(obscured.sum() / n),
        "clear_fraction": float((~obscured).sum() / n),
        "smoke_fraction": float(smoke_bin.sum() / n),
        "mean_solar_elevation": float(elev.mean()),
    }

    return {
        "smoke": density,
        "smoke_bin": smoke_bin.astype(np.uint8),
        "obscured": obscured.astype(np.uint8),
        "clear": ((~obscured) & (~smoke_bin)).astype(np.uint8),
        "stats": stats,
    }


def remove_small_blobs(mask: np.ndarray, min_cells: int) -> np.ndarray:
    """Drop speckle. Optical flow on speckle produces confident nonsense."""
    if min_cells <= 1 or not mask.any():
        return mask
    try:
        from scipy import ndimage
    except ImportError:  # pragma: no cover
        return mask
    labels, count = ndimage.label(mask)
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    keep = np.zeros(sizes.shape, dtype=bool)
    keep[1:] = sizes[1:] >= min_cells
    return keep[labels]


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def save_mask(slot: datetime, result: dict) -> Path:
    path = common.mask_path(slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        slot=common.slot_id(slot),
        smoke=result["smoke"],
        smoke_bin=result["smoke_bin"],
        obscured=result["obscured"],
        clear=result["clear"],
        stats=np.array([result["stats"]], dtype=object),
    )
    s = result["stats"]
    log.info(
        "mask %s: smoke %.2f%% obscured %.1f%% clear %.1f%% sun %.0f deg",
        common.slot_id(slot),
        100 * s["smoke_fraction"],
        100 * s["obscured_fraction"],
        100 * s["clear_fraction"],
        s["mean_solar_elevation"],
    )
    return path


def load_mask_npz(path: Path) -> dict:
    with np.load(path, allow_pickle=True) as data:
        return {
            "slot": str(data["slot"]),
            "smoke": data["smoke"],
            "smoke_bin": data["smoke_bin"],
            "obscured": data["obscured"],
            "clear": data["clear"],
            "stats": dict(data["stats"][0]),
        }


# --------------------------------------------------------------------------
# QA rendering — this is what you compare against BMKG
# --------------------------------------------------------------------------

def _stretch(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((np.nan_to_num(a, nan=lo) - lo) / (hi - lo), 0, 1)


def qa_frame(grids: dict, result: dict, slot: datetime):
    """Composite | mask, side by side, as a PIL image."""
    from PIL import Image, ImageDraw

    # Poor-man's natural colour: red=B03, green=mix, blue=B01. Good enough to
    # recognise cloud vs smoke vs land by eye. Sun-corrected so frames are
    # comparable across the day, same as the mask sees them.
    lon2d, lat2d = common.grid_mesh()
    elev = common.solar_elevation(slot, lat2d, lon2d)
    r = _stretch(sunz_correct(grids["B03"], elev), 0, 60)
    b = _stretch(sunz_correct(grids["B01"], elev), 0, 60)
    g = 0.55 * r + 0.45 * b
    rgb = (np.dstack([r, g, b]) ** (1 / 1.6) * 255).astype(np.uint8)

    overlay = rgb.copy()
    smoke = result["smoke"]
    obsc = result["obscured"].astype(bool)

    tint = np.array(C.SMOKE_RGB, dtype=np.float32)
    a = np.clip(smoke, 0, 1)[..., None]
    overlay = (overlay * (1 - a) + tint * a).astype(np.uint8)

    # Hatch the obscured area so it reads as "we cannot see", not "nothing here".
    yy, xx = np.mgrid[0 : overlay.shape[0], 0 : overlay.shape[1]]
    hatch = ((xx + yy) % 14) < 2
    overlay[obsc & hatch] = np.array(C.OBSCURED_RGB, dtype=np.uint8)

    canvas = Image.new(
        "RGB", (rgb.shape[1] * 2 + 12, rgb.shape[0] + 24), (18, 18, 22)
    )
    canvas.paste(Image.fromarray(rgb), (0, 24))
    canvas.paste(Image.fromarray(overlay), (rgb.shape[1] + 12, 24))
    d = ImageDraw.Draw(canvas)
    s = result["stats"]
    d.text(
        (4, 6),
        f"{common.slot_id(slot)} UTC  |  "
        f"{common.to_display_tz(slot):%H:%M} {C.DISPLAY_TZ_LABEL}   "
        f"smoke {100 * s['smoke_fraction']:.2f}%  "
        f"obscured {100 * s['obscured_fraction']:.0f}%",
        fill=(230, 230, 235),
    )
    return canvas


def write_qa(qa_dir: Path, frames: list) -> None:
    if not frames:
        return
    qa_dir.mkdir(parents=True, exist_ok=True)
    for slot, img in frames:
        img.save(qa_dir / f"qa_{common.slot_id(slot)}.png")
    if len(frames) > 1:
        first = frames[0][1]
        first.save(
            qa_dir / "qa_animation.gif",
            save_all=True,
            append_images=[f[1] for f in frames[1:]],
            duration=600,
            loop=0,
        )
        log.info("wrote %s (%d frames)", qa_dir / "qa_animation.gif", len(frames))
    log.info("QA frames in %s — compare these against BMKG's smoke RGB", qa_dir)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def process(slot: datetime, force: bool = False, qa: bool = False):
    scene_file = common.scene_path(slot)
    if not scene_file.exists():
        log.warning("no gridded scene for %s", common.slot_id(slot))
        return None
    out = common.mask_path(slot)
    if out.exists() and not force and not qa:
        log.debug("%s exists, skipping", out.name)
        return None
    grids = load_scene_npz(scene_file)
    result = classify(grids, slot)
    save_mask(slot, result)
    frame = qa_frame(grids, result, slot) if qa else None
    return (slot, frame) if frame is not None else (slot, None)


def main(argv=None) -> int:
    global log
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="single slot, e.g. 20260821_0600")
    p.add_argument("--all", action="store_true", help="every gridded scene in state/")
    p.add_argument("--force", action="store_true")
    p.add_argument("--qa", metavar="DIR", help="write QA frames + GIF to DIR")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    log = common.setup_logging(args.verbose)
    common.ensure_dirs(C.STATE_DIR)

    if args.date:
        slots = [common.parse_cli_datetime(args.date)]
    elif args.all:
        slots = [s for s, _ in common.list_state("scene")]
    else:
        scenes = common.list_state("scene")
        if not scenes:
            log.error("no scenes in %s — run fetch_ahi first", C.STATE_DIR)
            return 1
        slots = [scenes[-1][0]]

    frames = []
    for slot in slots:
        got = process(slot, force=args.force, qa=bool(args.qa))
        if got and got[1] is not None:
            frames.append(got)

    if args.qa:
        write_qa(Path(args.qa), frames)

    common.prune_state("mask", keep=8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
