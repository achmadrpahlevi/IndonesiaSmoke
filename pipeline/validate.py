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

# The four the cutover gate is written against (docs/superpowers/specs,
# OPERATIONS.md "acceptance gate"). Marked in the per-region table so the
# person deciding whether to cut over does not have to cross-reference.
GATE_REGIONS = ("Sumatra", "Kalimantan", "Sulawesi", "Papua")

# Below this many scored hotspots the enrichment ratio is not reported as a
# number at all. Three points in Papua can put any figure on the screen, and
# a confident "8.4x" derived from three fires is worse than saying nothing:
# the gate is read by a human who cannot see n unless it is printed. Same
# floor the domain-wide NOTE already uses.
MIN_SCORED_FOR_ENRICHMENT = 30


def acq_time(feature: dict):
    raw = (feature.get("properties") or {}).get("acq_utc")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def enrichment(smoke, obscured, features, region=None):
    """(enrichment, hits, scored) for a set of hotspots.

    `region` is an optional boolean grid mask. It restricts BOTH the hotspots
    counted and the smoke rate they are compared against, which is the only
    way a per-region number means anything: scoring Papua's fires against the
    whole country's smoke rate answers a question nobody asked.

    THE DENOMINATOR IS THE POPULATION ACTUALLY SCORED. Hotspots over obscured
    pixels are skipped, so chance has to be measured over the unobscured
    pixels too. Dividing by the whole-grid smoke rate inflates the ratio by
    exactly 1/clear_fraction — ~1.7x on the Kalimantan domain, where the
    whole grid was calibrated, but 3-20x here, because uncalibrated pixels
    are now folded into `obscured` and clear_fraction sits at 0.05-0.35 for
    most of the publishing day. At 0.20 clear a chance-level mask (true
    0.99x) reported 4.95x, and the cutover gate is "above 3x".
    """
    lons, lats = common.grid_lons(), common.grid_lats()
    scoreable = ~obscured if region is None else (~obscured & region)
    hits = scored = 0
    for f in features:
        lon, lat = f["geometry"]["coordinates"]
        i = int(np.argmin(np.abs(lons - lon)))
        j = int(np.argmin(np.abs(lats - lat)))
        if not scoreable[j, i]:
            continue
        scored += 1
        hits += bool(smoke[j, i])
    if not scored or not scoreable.any():
        return float("nan"), 0, 0
    frac = max(float(smoke[scoreable].mean()), 1e-9)
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

    # "of visible", not of the grid. The whole-grid smoke percentage was
    # renamed to smoke_fraction_of_visible everywhere else in this branch,
    # for the reason smoke_mask.classify gives: across 47 degrees of
    # longitude most of the grid is out of window at any instant, so the
    # whole-grid figure collapses toward zero and is not comparable to the
    # 5-11% reference values. This is also the denominator enrichment uses.
    visible = ~obscured
    smoke_of_visible = (
        100 * float(smoke[visible].mean()) if visible.any() else float("nan")
    )
    print("scene %s (%s %s)  smoke %.2f%% of visible  obscured %.1f%%" % (
        common.slot_id(slot), common.to_display_tz(slot).strftime("%H:%M"),
        C.DISPLAY_TZ_LABEL, smoke_of_visible, 100 * obscured.mean()))
    e_all, _, n_all = enrichment(smoke, obscured, feats)
    e_pri, h_pri, n_pri = enrichment(smoke, obscured, prior)
    print("  hotspot enrichment, fires before this scene : %.1fx  (%d/%d)"
          % (e_pri, h_pri, n_pri))
    print("  same against the full 24 h list             : %.1fx  (n=%d)"
          % (e_all, n_all))
    if n_pri < MIN_SCORED_FOR_ENRICHMENT:
        print("  NOTE: only %d prior fires, so that figure is noisy" % n_pri)

    # Per region, because that is what the cutover gate asks for. Enrichment
    # here is against fires that preceded the scene, same as the headline
    # figure above — the 24 h list charges a scene for fires that had not
    # started yet.
    lon2d, lat2d = common.grid_mesh()
    print("")
    print("  by region, fires before this scene "
          "(* = acceptance-gate region):")
    print("    %-17s %10s %12s %9s" % ("", "smoke", "enrichment", "hotspots"))
    for name, (a, b, c, d) in REGIONS.items():
        box = (lon2d > a) & (lon2d < b) & (lat2d > c) & (lat2d < d)
        label = ("* " if name in GATE_REGIONS else "  ") + name
        sel = box & visible
        if not sel.any():
            # Routine, not an error: at any instant a third of the country is
            # outside the calibrated window and therefore obscured.
            print("    %-17s %10s %12s %9s"
                  % (label, "-", "not visible", "-"))
            continue
        e, _, n = enrichment(smoke, obscured, prior, region=box)
        smoke_pct = "%.1f%%" % (100 * smoke[sel].mean())
        if n < MIN_SCORED_FOR_ENRICHMENT:
            # Not a number. Three hotspots can produce any ratio at all, and
            # the gate is read by a human deciding whether to cut over.
            print("    %-17s %10s %12s %9d"
                  % (label, smoke_pct, "too few", n))
        else:
            print("    %-17s %10s %11.1fx %9d" % (label, smoke_pct, e, n))
    print("  Enrichment is measured against the smoke rate over that "
          "region's own")
    print("  visible pixels, so regions are comparable with each other and "
          "with")
    print("  the domain-wide figures above. Fewer than %d scored hotspots "
          "reports" % MIN_SCORED_FOR_ENRICHMENT)
    print("  the count instead of a ratio.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
