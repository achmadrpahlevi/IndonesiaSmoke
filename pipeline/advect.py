"""Forward-advect the smoke field 0-3 h using Farneback optical flow.

    python -m pipeline.advect                       # latest pair in state/
    python -m pipeline.advect --date 20260821_0600
    python -m pipeline.advect --date 20260821_0600 --verify 20260821_0700

`--verify` scores the forecast against a mask that was actually observed
later — the Sunday AM "done when" check in PLAN.md.

Two rules the forecast never breaks:
  * flow is only trusted where both frames were unobscured (non-negotiable #2)
  * a physically absurd flow field is discarded, not published
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import common
from . import config as C
from .smoke_mask import load_mask_npz

log = None

M_PER_DEG = 111_320.0


def metres_per_cell() -> float:
    return C.GRID_RES_DEG * M_PER_DEG


# --------------------------------------------------------------------------
# Flow
# --------------------------------------------------------------------------

def to_u8(field: np.ndarray) -> np.ndarray:
    """Farneback wants 8-bit single channel."""
    return np.clip(np.nan_to_num(field) * 255.0, 0, 255).astype(np.uint8)


def compute_flow(prev: dict, curr: dict) -> np.ndarray:
    """Displacement in grid cells from `prev` to `curr`, shape (ny, nx, 2)."""
    import cv2

    a = to_u8(prev["smoke"])
    b = to_u8(curr["smoke"])
    flow = cv2.calcOpticalFlowFarneback(a, b, None, **C.FARNEBACK)
    return flow.astype(np.float32)


def condition_flow(flow: np.ndarray, trusted: np.ndarray) -> np.ndarray:
    """Blank the flow where we could not see, then fill the gaps smoothly.

    A plain zero-fill would smear artificial calm into the surrounding field,
    so the smoothing is weighted: only trusted cells contribute, and the
    result is renormalised by the weight that actually arrived.
    """
    from scipy.ndimage import gaussian_filter

    w = trusted.astype(np.float32)
    sigma = C.FLOW_SMOOTH_SIGMA
    den = gaussian_filter(w, sigma, mode="nearest")
    out = np.zeros_like(flow)
    for k in range(2):
        num = gaussian_filter(flow[..., k] * w, sigma, mode="nearest")
        out[..., k] = np.where(den > 1e-3, num / np.maximum(den, 1e-6), 0.0)
    return out


def flow_diagnostics(flow: np.ndarray, weight: np.ndarray, dt_minutes: float) -> dict:
    """Median speed/bearing over the cells that matter."""
    sel = weight > 0
    if not sel.any():
        return {
            "median_speed_ms": 0.0,
            "bearing_from_deg": None,
            "sample_cells": 0,
        }
    dx = flow[..., 0][sel]
    dy = flow[..., 1][sel]
    mpc = metres_per_cell()
    dt_s = max(dt_minutes * 60.0, 1.0)
    speed = np.hypot(dx, dy) * mpc / dt_s

    # Grid rows increase southward, so a positive dy is southward motion.
    u = float(np.median(dx)) * mpc / dt_s  # eastward m/s
    v = -float(np.median(dy)) * mpc / dt_s  # northward m/s
    bearing_from = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0

    return {
        "median_speed_ms": round(float(np.median(speed)), 2),
        "p95_speed_ms": round(float(np.percentile(speed, 95)), 2),
        "bearing_from_deg": round(float(bearing_from), 0),
        "u_ms": round(u, 2),
        "v_ms": round(v, 2),
        "sample_cells": int(sel.sum()),
    }


# --------------------------------------------------------------------------
# Advection
# --------------------------------------------------------------------------

def warp(field: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """Semi-Lagrangian step: pull each cell's new value from where it came."""
    import cv2

    ny, nx = field.shape
    gx, gy = np.meshgrid(
        np.arange(nx, dtype=np.float32), np.arange(ny, dtype=np.float32)
    )
    map_x = (gx - dx).astype(np.float32)
    map_y = (gy - dy).astype(np.float32)
    return cv2.remap(
        field.astype(np.float32),
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )


def integrate(
    smoke: np.ndarray,
    obscured: np.ndarray,
    flow: np.ndarray,
    dt_minutes: float,
    steps=C.FORECAST_STEPS,
):
    """Roll the field forward. Returns (smoke_steps, unverifiable_steps)."""
    per_step = C.FORECAST_STEP_MINUTES / max(dt_minutes, 1e-6)
    dx = flow[..., 0] * per_step
    dy = flow[..., 1] * per_step

    field = smoke.astype(np.float32).copy()
    # Anything sourced from an area we could not see is unverifiable, and
    # stays unverifiable as it travels.
    flag = obscured.astype(np.float32).copy()

    smoke_steps, flag_steps = [], []
    for _ in steps:
        field = warp(field, dx, dy) * C.ADVECTION_DECAY_PER_STEP
        flag = np.clip(warp(flag, dx, dy), 0.0, 1.0)
        smoke_steps.append(field.copy())
        flag_steps.append(flag.copy())
    return np.stack(smoke_steps), np.stack(flag_steps)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def mask_is_usable(path: Path) -> bool:
    """Could this mask actually see anything?

    Night masks are 100% obscured. Pairing one with the first daylight frame
    would have Farneback match darkness against a lit scene, which is exactly
    the situation at dawn every morning.
    """
    try:
        with np.load(path, allow_pickle=True) as data:
            stats = dict(data["stats"][0])
    except (OSError, ValueError, KeyError, IndexError):
        return False
    return float(stats.get("clear_fraction", 0.0)) >= C.MIN_CLEAR_FRACTION


def find_pair(target: datetime | None = None):
    """The two most recent usable masks close enough to compute flow from."""
    masks = common.list_state("mask")
    if target is not None:
        masks = [m for m in masks if m[0] <= target]

    blind = [m for m in masks if not mask_is_usable(m[1])]
    masks = [m for m in masks if mask_is_usable(m[1])]
    if blind:
        log.debug("ignoring %d mask(s) with nothing visible", len(blind))
    if len(masks) < 2:
        return None, None
    (t_prev, p_prev), (t_curr, p_curr) = masks[-2], masks[-1]
    gap = common.minutes_between(t_curr, t_prev)
    if gap > C.MAX_FLOW_PAIR_GAP_MINUTES:
        log.warning(
            "gap between %s and %s is %.0f min, over the %d min limit",
            common.slot_id(t_prev),
            common.slot_id(t_curr),
            gap,
            C.MAX_FLOW_PAIR_GAP_MINUTES,
        )
        return None, None
    return (t_prev, p_prev), (t_curr, p_curr)


def forecast(target: datetime | None = None) -> dict | None:
    pair = find_pair(target)
    if pair[0] is None:
        log.error("no usable pair of masks in %s", C.STATE_DIR)
        return None
    (t_prev, p_prev), (t_curr, p_curr) = pair

    prev = load_mask_npz(p_prev)
    curr = load_mask_npz(p_curr)
    dt_minutes = common.minutes_between(t_curr, t_prev)

    clear_fraction = float(curr["stats"]["clear_fraction"])
    quality = {
        "pair": [common.slot_id(t_prev), common.slot_id(t_curr)],
        "pair_gap_minutes": round(dt_minutes, 1),
        "clear_fraction": round(clear_fraction, 3),
        "flow_rejected": False,
        "suppressed": False,
        "reason": "",
    }

    raw = compute_flow(prev, curr)
    trusted = (
        (prev["obscured"] == 0)
        & (curr["obscured"] == 0)
        & ((prev["smoke_bin"] > 0) | (curr["smoke_bin"] > 0))
    )
    flow = condition_flow(raw, trusted)
    diag = flow_diagnostics(flow, trusted, dt_minutes)
    quality.update(diag)

    if diag["median_speed_ms"] > C.MAX_PLAUSIBLE_SPEED_MS:
        log.warning(
            "flow implies %.1f m/s, over the %.0f m/s limit — discarding",
            diag["median_speed_ms"],
            C.MAX_PLAUSIBLE_SPEED_MS,
        )
        flow = np.zeros_like(flow)
        quality["flow_rejected"] = True
        quality["reason"] = "implausible flow field, forecast frozen"

    if clear_fraction < C.MIN_CLEAR_FRACTION:
        log.warning(
            "only %.0f%% of the domain is clear — suppressing the forecast",
            100 * clear_fraction,
        )
        quality["suppressed"] = True
        quality["reason"] = (
            f"only {100 * clear_fraction:.0f}% of the domain is visible; "
            "forecast withheld"
        )

    smoke_steps, flag_steps = integrate(
        curr["smoke"], curr["obscured"], flow, dt_minutes
    )

    out = {
        "slot": common.slot_id(t_curr),
        "steps": C.FORECAST_STEPS,
        "smoke": smoke_steps.astype(np.float32),
        "unverifiable": flag_steps.astype(np.float32),
        "flow": flow.astype(np.float32),
        "quality": quality,
    }
    save_forecast(t_curr, out)
    log.info(
        "forecast %s: %s at %.1f m/s from %s, %d steps%s",
        common.slot_id(t_curr),
        "FROZEN" if quality["flow_rejected"] else "advecting",
        diag["median_speed_ms"],
        diag["bearing_from_deg"],
        len(C.FORECAST_STEPS),
        " (SUPPRESSED)" if quality["suppressed"] else "",
    )
    return out


def forecast_path(slot: datetime) -> Path:
    return Path(C.STATE_DIR) / f"forecast_{common.slot_id(slot)}.npz"


def save_forecast(slot: datetime, out: dict) -> Path:
    path = forecast_path(slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        slot=out["slot"],
        steps=np.array(out["steps"]),
        smoke=out["smoke"],
        unverifiable=out["unverifiable"],
        flow=out["flow"],
        quality=np.array([out["quality"]], dtype=object),
    )
    common.prune_state("forecast", keep=4)
    return path


def load_forecast(path: Path) -> dict:
    with np.load(path, allow_pickle=True) as data:
        return {
            "slot": str(data["slot"]),
            "steps": [int(s) for s in data["steps"]],
            "smoke": data["smoke"],
            "unverifiable": data["unverifiable"],
            "flow": data["flow"],
            "quality": dict(data["quality"][0]),
        }


# --------------------------------------------------------------------------
# Verification against a later observation
# --------------------------------------------------------------------------

def verify(fc: dict, truth_slot: datetime) -> dict | None:
    """Score a forecast step against the mask actually observed then."""
    path = common.mask_path(truth_slot)
    if not path.exists():
        log.warning("no observed mask for %s", common.slot_id(truth_slot))
        return None
    truth = load_mask_npz(path)
    base = common.parse_slot_id(fc["slot"])
    lead = common.minutes_between(truth_slot, base)

    if lead not in [float(s) for s in fc["steps"]]:
        log.warning("%.0f min is not a forecast step; scoring nearest", lead)
    idx = int(np.argmin([abs(s - lead) for s in fc["steps"]]))

    pred = fc["smoke"][idx] > 0.05
    obs = truth["smoke_bin"] > 0
    seen = truth["obscured"] == 0

    pred, obs = pred & seen, obs & seen
    hits = int((pred & obs).sum())
    misses = int((~pred & obs).sum())
    false_alarms = int((pred & ~obs).sum())

    persistence = None
    prev_mask = common.mask_path(base)
    if prev_mask.exists():
        p0 = load_mask_npz(prev_mask)["smoke_bin"] > 0
        p0 = p0 & seen
        p_hits = int((p0 & obs).sum())
        p_fa = int((p0 & ~obs).sum())
        p_miss = int((~p0 & obs).sum())
        persistence = round(p_hits / max(p_hits + p_fa + p_miss, 1), 3)

    score = {
        "lead_minutes": int(fc["steps"][idx]),
        "hits": hits,
        "misses": misses,
        "false_alarms": false_alarms,
        "pod": round(hits / max(hits + misses, 1), 3),
        "far": round(false_alarms / max(hits + false_alarms, 1), 3),
        "csi": round(hits / max(hits + misses + false_alarms, 1), 3),
        "persistence_csi": persistence,
    }
    log.info(
        "verify +%d min: CSI %.3f (persistence %.3f) POD %.3f FAR %.3f",
        score["lead_minutes"],
        score["csi"],
        persistence if persistence is not None else float("nan"),
        score["pod"],
        score["far"],
    )
    return score


def main(argv=None) -> int:
    global log
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="run as if this were the latest slot")
    p.add_argument("--verify", metavar="SLOT", help="score against this observed slot")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    log = common.setup_logging(args.verbose)
    common.ensure_dirs(C.STATE_DIR)

    target = common.parse_cli_datetime(args.date) if args.date else None
    fc = forecast(target)
    if fc is None:
        # No usable pair is the expected state at night, not a failure.
        lit, elev = common.domain_is_daylit(common.utcnow())
        if not lit:
            log.info(
                "domain is dark (mean solar elevation %.0f deg) — nothing to "
                "advect; the last daylight forecast stands",
                elev,
            )
            return 0
        return 1

    if args.verify:
        verify(fc, common.parse_cli_datetime(args.verify))
    return 0


if __name__ == "__main__":
    sys.exit(main())
