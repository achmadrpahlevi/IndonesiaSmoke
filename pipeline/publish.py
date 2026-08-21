"""Render the published product: overlay PNGs + meta.json.

    python -m pipeline.publish
    python -m pipeline.publish --outdir site/data

Writes into site/data/:
    smoke_now.png     current smoke field
    obscured.png      hatched "we cannot see here"
    fcst_030.png ...  forecast steps
    meta.json         timestamps, quality flags, layer index, caption

Night handling (PLAN.md §6): when the domain is dark the existing product is
left in place and meta.json is marked frozen. A stale map that says so beats
a blank one that does not.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

from . import advect as advect_mod
from . import common
from . import config as C
from .smoke_mask import load_mask_npz

log = None


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def smoke_png(density: np.ndarray) -> "Image.Image":
    """Density 0-1 -> RGBA overlay, transparent where there is no smoke."""
    from PIL import Image

    d = np.clip(np.nan_to_num(density), 0.0, 1.0).astype(np.float32)
    shown = d >= C.SMOKE_DRAW_FLOOR

    lo = np.array(C.SMOKE_RGB_LIGHT, dtype=np.float32)
    hi = np.array(C.SMOKE_RGB, dtype=np.float32)
    t = d[..., None]
    rgb = (lo * (1 - t) + hi * t).astype(np.uint8)

    alpha = C.SMOKE_MIN_ALPHA + d * (C.SMOKE_MAX_ALPHA - C.SMOKE_MIN_ALPHA)
    alpha = np.where(shown, alpha, 0).astype(np.uint8)

    return Image.fromarray(np.dstack([rgb, alpha[..., None]]))


def obscured_png(obscured: np.ndarray) -> "Image.Image":
    """Diagonal hatching. Never a solid fill — it must not read as data."""
    from PIL import Image

    m = np.asarray(obscured).astype(bool)
    ny, nx = m.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    hatch = ((xx + yy) % C.OBSCURED_HATCH_PERIOD) < C.OBSCURED_HATCH_WIDTH

    rgb = np.zeros((ny, nx, 3), dtype=np.uint8)
    rgb[..., :] = np.array(C.OBSCURED_RGB, dtype=np.uint8)
    alpha = np.where(m & hatch, C.OBSCURED_ALPHA, 0).astype(np.uint8)
    return Image.fromarray(np.dstack([rgb, alpha[..., None]]))


def save_png(img, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return path.name


# --------------------------------------------------------------------------
# meta.json
# --------------------------------------------------------------------------

def iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_meta(scene_slot, mask, fc, layers, firms_props) -> dict:
    stats = mask["stats"]
    quality = dict(fc["quality"]) if fc else {"suppressed": True, "reason": "no forecast"}
    now = common.utcnow()

    forecast_index = []
    for minutes, name in layers["forecast"]:
        valid = scene_slot + timedelta(minutes=minutes)
        forecast_index.append(
            {
                "minutes": minutes,
                "file": name,
                "valid_utc": iso(valid),
                "valid_local": common.to_display_tz(valid).strftime("%H:%M"),
            }
        )

    return {
        "generated_utc": iso(now),
        "scene_utc": iso(scene_slot),
        "scene_local": common.to_display_tz(scene_slot).strftime("%Y-%m-%d %H:%M"),
        "tz_label": C.DISPLAY_TZ_LABEL,
        "tz_offset_hours": C.DISPLAY_TZ_OFFSET_HOURS,
        "stale_after_minutes": C.STALE_MINUTES,
        "age_minutes_at_build": round(common.minutes_between(now, scene_slot), 1),
        "daylight": True,
        "frozen": False,
        "bounds": common.leaflet_bounds(),
        "view_bounds": common.view_bounds(),
        "focus": [C.FOCUS_LAT, C.FOCUS_LON],
        "grid": {
            "nx": C.GRID_NX,
            "ny": C.GRID_NY,
            "res_deg": C.GRID_RES_DEG,
        },
        "caption": C.CAPTION,
        "layers": {
            "now": layers["now"],
            "obscured": layers["obscured"],
            "forecast": forecast_index,
        },
        "scene_stats": {k: round(float(v), 4) for k, v in stats.items()},
        "quality": quality,
        "firms": firms_props or {},
        "cities": C.CITIES,
    }


def freeze_existing(outdir: Path, scene_slot, reason: str) -> int:
    """Night, or nothing new worth drawing: keep what is up, label it."""
    meta_path = outdir / "meta.json"
    meta = common.read_json(meta_path)
    now = common.utcnow()
    if not meta:
        meta = {
            "generated_utc": iso(now),
            "scene_utc": iso(scene_slot),
            "scene_local": common.to_display_tz(scene_slot).strftime("%Y-%m-%d %H:%M"),
            "tz_label": C.DISPLAY_TZ_LABEL,
            "tz_offset_hours": C.DISPLAY_TZ_OFFSET_HOURS,
            "stale_after_minutes": C.STALE_MINUTES,
            "bounds": common.leaflet_bounds(),
            "view_bounds": common.view_bounds(),
            "focus": [C.FOCUS_LAT, C.FOCUS_LON],
            "caption": C.CAPTION,
            "layers": {"now": None, "obscured": None, "forecast": []},
            "cities": C.CITIES,
        }
    meta["daylight"] = False
    meta["frozen"] = True
    meta["frozen_reason"] = reason
    meta["checked_utc"] = iso(now)
    meta["last_scene_checked_utc"] = iso(scene_slot)
    common.write_json(meta_path, meta)
    log.warning("froze existing product: %s", reason)
    return 0


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def newest_daylight_mask(masks):
    """The most recent mask that actually saw something.

    Not simply the most recent mask. A night frame is newer than every
    daylight frame of the day it ends, so picking by time alone means the
    product goes dark at dusk and stays dark — and any backfill of an earlier
    daylight scene is silently discarded in favour of the darkness.
    """
    for slot, path in reversed(masks):
        mask = load_mask_npz(path)
        if float(mask["stats"].get("daylit_fraction", 0.0)) >= C.DAYLIGHT_MIN_FRACTION:
            return slot, path, mask
    return None, None, None


def publish(outdir: Path) -> int:
    masks = common.list_state("mask")
    if not masks:
        log.error("no masks in %s — run smoke_mask first", C.STATE_DIR)
        return 1

    scene_slot, mask_file, mask = newest_daylight_mask(masks)
    if scene_slot is None:
        newest_slot = masks[-1][0]
        _, mean_elev = common.domain_is_daylit(newest_slot)
        return freeze_existing(
            outdir,
            newest_slot,
            f"night over the domain (mean solar elevation {mean_elev:.0f} deg) "
            "and no daylight scene held in state",
        )

    # Render the last daylight product rather than assuming one is already
    # published: at dusk, and on a cold cache, there may be nothing up yet.
    lit_now, mean_elev = common.domain_is_daylit(common.utcnow())
    is_current = scene_slot == masks[-1][0] and lit_now
    frozen_reason = ""
    if not is_current:
        frozen_reason = (
            f"night over the domain (mean solar elevation {mean_elev:.0f} deg); "
            f"showing the last daylight scene, {common.to_display_tz(scene_slot):%H:%M} "
            f"{C.DISPLAY_TZ_LABEL}"
        )
        log.warning("publishing frozen: %s", frozen_reason)

    fc = None
    fc_files = common.list_state("forecast")
    if fc_files:
        fc_slot, fc_path = fc_files[-1]
        if common.slot_id(fc_slot) == common.slot_id(scene_slot):
            fc = advect_mod.load_forecast(fc_path)
        else:
            log.warning(
                "latest forecast is for %s but latest mask is %s — publishing "
                "the current field without a forecast",
                common.slot_id(fc_slot),
                common.slot_id(scene_slot),
            )

    outdir.mkdir(parents=True, exist_ok=True)
    layers = {
        "now": save_png(smoke_png(mask["smoke"]), outdir / "smoke_now.png"),
        "obscured": save_png(
            obscured_png(mask["obscured"]), outdir / "obscured.png"
        ),
        "forecast": [],
    }

    suppressed = bool(fc and fc["quality"].get("suppressed"))
    if fc and not suppressed:
        for i, minutes in enumerate(fc["steps"]):
            # Do not draw forecast smoke that came from behind cloud.
            field = np.where(fc["unverifiable"][i] > 0.5, 0.0, fc["smoke"][i])
            name = save_png(smoke_png(field), outdir / f"fcst_{minutes:03d}.png")
            layers["forecast"].append((int(minutes), name))
    elif suppressed:
        log.warning("forecast suppressed: %s", fc["quality"].get("reason"))

    firms = common.read_json(outdir / "firms.geojson") or {}
    meta = build_meta(scene_slot, mask, fc, layers, firms.get("properties"))
    meta["daylight"] = bool(is_current)
    meta["frozen"] = not is_current
    if frozen_reason:
        meta["frozen_reason"] = frozen_reason
    common.write_json(outdir / "meta.json", meta)

    log.info(
        "published %s: %d forecast steps, %.2f%% smoke, %.0f%% obscured -> %s",
        common.slot_id(scene_slot),
        len(layers["forecast"]),
        100 * mask["stats"]["smoke_fraction"],
        100 * mask["stats"]["obscured_fraction"],
        outdir,
    )
    return 0


def main(argv=None) -> int:
    global log
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--outdir", default=str(C.SITE_DATA_DIR))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    log = common.setup_logging(args.verbose)
    return publish(Path(args.outdir))


if __name__ == "__main__":
    sys.exit(main())
