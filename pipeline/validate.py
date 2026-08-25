"""Score a published mask against FIRMS hotspots — the standing QA check.

    python -m pipeline.validate                 # newest mask in state/
    python -m pipeline.validate --date 20260821_0700

Reports enrichment: how much more often a hotspot lands on detected smoke than
chance would give. FIRMS is thermal (VIIRS/MODIS infrared); the mask is AHI
visible/SWIR reflectance. They share no physics, so agreement is real evidence
rather than a restatement of our own assumptions.

ONLY hotspots acquired at or before the scene time are counted. Scoring a
midday scene against the full 24 h list charges it for fires that had not
started yet, which made the morning look far worse than it is — 12:00 scored
2.4x against everything and 14.3x against fires that actually preceded it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import common
from . import config as C
from .smoke_mask import load_mask_npz

log = None

# Regions scored independently against FIRMS. Kalimantan at 14:00 WIB is the
# only one ever cross-checked against an independent sensor on the old
# domain; the rest exist so that the acceptance gate has something to fail.
REGIONS = {
    "Sumatra": (95.0, 106.5, -6.0, 6.0),
    "Kalimantan": (109.0, 118.0, -4.0, 3.0),
    "Java": (105.0, 115.0, -9.0, -5.5),
    "Sulawesi": (118.5, 125.5, -6.0, 2.0),
    "Maluku": (125.0, 135.0, -8.5, 3.0),
    "Papua": (130.5, 141.5, -9.5, 0.5),
    "Malacca Strait": (100.5, 104.0, 1.0, 5.0),
}


def acq_time(feature: dict):
    raw = (feature.get("properties") or {}).get("acq_utc")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def enrichment(smoke, obscured, features):
    """(enrichment, hits, scored) for a set of hotspots."""
    lons, lats = common.grid_lons(), common.grid_lats()
    hits = scored = 0
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        i = int(np.argmin(np.abs(lons - lon)))
        j = int(np.argmin(np.abs(lats - lat)))
        if obscured[j, i]:
            continue
        scored += 1
        hits += bool(smoke[j, i])
    if not scored:
        return float("nan"), 0, 0
    frac = max(float(smoke.mean()), 1e-9)
    return (hits / scored) / frac, hits, scored


def main(argv=None) -> int:
    global log
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="slot to score, default: newest mask")
    p.add_argument("--firms", default=None, help="path to firms.geojson")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    log = common.setup_logging(args.verbose)

    if args.date:
        slot = common.parse_cli_datetime(args.date)
    else:
        masks = common.list_state("mask")
        if not masks:
            log.error("no masks in %s", C.STATE_DIR)
            return 1
        slot = masks[-1][0]

    path = common.mask_path(slot)
    if not path.exists():
        log.error("no mask for %s", common.slot_id(slot))
        return 1
    m = load_mask_npz(path)
    smoke = m["smoke_bin"] > 0
    obscured = m["obscured"] > 0

    firms_path = Path(args.firms or (Path(C.SITE_DATA_DIR) / "firms.geojson"))
    gj = common.read_json(firms_path)
    if not gj or not gj.get("features"):
        log.error("no hotspots in %s — run fetch_firms first", firms_path)
        return 1
    feats = gj["features"]
    prior = [f for f in feats if (t := acq_time(f)) and t <= slot]

    print("scene %s (%s %s)  smoke %.2f%%  obscured %.1f%%" % (
        common.slot_id(slot), common.to_display_tz(slot).strftime("%H:%M"),
        C.DISPLAY_TZ_LABEL, 100 * smoke.mean(), 100 * obscured.mean()))
    e_all, _, n_all = enrichment(smoke, obscured, feats)
    e_pri, h_pri, n_pri = enrichment(smoke, obscured, prior)
    print("  hotspot enrichment, fires before this scene : %.1fx  (%d/%d)"
          % (e_pri, h_pri, n_pri))
    print("  same against the full 24 h list             : %.1fx  (n=%d)"
          % (e_all, n_all))
    if n_pri < 30:
        print("  NOTE: only %d prior fires, so that figure is noisy" % n_pri)

    lon2d, lat2d = common.grid_mesh()
    print("  smoke by region (unobscured):")
    for name, (a, b, c, d) in REGIONS.items():
        sel = (lon2d > a) & (lon2d < b) & (lat2d > c) & (lat2d < d) & ~obscured
        if sel.any():
            print("    %-16s %5.1f%%" % (name, 100 * smoke[sel].mean()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
