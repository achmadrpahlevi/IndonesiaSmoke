"""Unit tests for the parts that are pure logic.

Everything here runs offline in a second or two. The satellite I/O is
verified by actually running the pipeline; what these tests protect is the
arithmetic that is easy to get quietly wrong — grid geometry, sun angles,
the mask rules, and the direction things move in.

    python -m pytest tests -q
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from pipeline import advect, common
from pipeline import config as C
from pipeline import fetch_ahi, fetch_firms, smoke_mask

UTC = timezone.utc
NOON = datetime(2026, 8, 21, 6, 0, tzinfo=UTC)  # 13:00 WIB, high sun


# --------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------

def test_grid_shape_matches_config():
    assert common.grid_lons().shape == (C.GRID_NX,)
    assert common.grid_lats().shape == (C.GRID_NY,)


def test_grid_covers_the_bbox_and_stays_inside_it():
    lons, lats = common.grid_lons(), common.grid_lats()
    assert C.LON_MIN < lons[0] < lons[-1] < C.LON_MAX
    assert C.LAT_MIN < lats[-1] < lats[0] < C.LAT_MAX
    # Half a cell in from each edge — pixel centres, not edges.
    assert lons[0] == pytest.approx(C.LON_MIN + C.GRID_RES_DEG / 2)
    assert lats[0] == pytest.approx(C.LAT_MAX - C.GRID_RES_DEG / 2)


def test_rows_run_north_to_south():
    """Image convention. Get this backwards and every forecast flips."""
    lats = common.grid_lats()
    assert lats[0] > lats[-1]


def test_domain_reaches_singapore_and_peninsular_malaysia():
    """The product claims to answer 'does the haze reach Singapore'. It can
    only do that if Singapore is inside the grid."""
    for name, lat, lon in [
        ("Singapore", 1.35, 103.82),
        ("Kuala Lumpur", 3.14, 101.69),
        ("Pontianak", -0.02, 109.34),
        ("Palangkaraya", -2.21, 113.92),
    ]:
        assert C.LON_MIN <= lon <= C.LON_MAX, name
        assert C.LAT_MIN <= lat <= C.LAT_MAX, name


def test_opening_view_is_centred_on_kalimantan():
    (south, west), (north, east) = common.view_bounds()
    assert (west + east) / 2 == pytest.approx(C.FOCUS_LON)
    assert (south + north) / 2 == pytest.approx(C.FOCUS_LAT)


def test_opening_view_still_contains_all_the_data():
    """Centring must never crop the grid out of frame."""
    (vs, vw), (vn, ve) = common.view_bounds()
    (ds, dw), (dn, de) = common.leaflet_bounds()
    assert vw <= dw and ve >= de
    assert vs <= ds and vn >= dn


def test_leaflet_bounds_are_south_west_then_north_east():
    (s, w), (n, e) = common.leaflet_bounds()
    assert s < n and w < e
    assert (s, w, n, e) == (C.LAT_MIN, C.LON_MIN, C.LAT_MAX, C.LON_MAX)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    ["20260821_0600", "2026-08-21T06:00", "2026-08-21 06:00", "202608210600"],
)
def test_cli_datetime_accepts_the_shapes_people_type(text):
    assert common.parse_cli_datetime(text) == NOON


def test_cli_datetime_rejects_nonsense():
    with pytest.raises(ValueError):
        common.parse_cli_datetime("last tuesday")


def test_slot_floor_snaps_down_to_the_ahi_cadence():
    t = datetime(2026, 8, 21, 6, 7, 43, tzinfo=UTC)
    assert common.floor_to_slot(t) == datetime(2026, 8, 21, 6, 0, tzinfo=UTC)


def test_slot_id_round_trips():
    assert common.parse_slot_id(common.slot_id(NOON)) == NOON


def test_display_tz_is_wib():
    assert common.to_display_tz(NOON).hour == 13


# --------------------------------------------------------------------------
# Sun geometry — night handling depends on this being right
# --------------------------------------------------------------------------

def test_sun_is_high_over_kalimantan_at_local_midday():
    elev = common.solar_elevation(NOON, np.array([-1.0]), np.array([114.0]))
    assert elev[0] > 60


def test_sun_is_below_the_horizon_at_local_midnight():
    midnight = datetime(2026, 8, 21, 17, 0, tzinfo=UTC)  # 00:00 WIB
    elev = common.solar_elevation(midnight, np.array([-1.0]), np.array([114.0]))
    assert elev[0] < 0


def test_domain_daylight_flag_flips_between_day_and_night():
    assert common.domain_is_daylit(NOON)[0] is True
    assert common.domain_is_daylit(datetime(2026, 8, 21, 17, 0, tzinfo=UTC))[0] is False


# --------------------------------------------------------------------------
# AHI addressing
# --------------------------------------------------------------------------

def test_segments_are_a_contiguous_in_range_block():
    segs = fetch_ahi.segments_for_bbox()
    assert segs == list(range(segs[0], segs[-1] + 1))
    assert 1 <= segs[0] <= segs[-1] <= C.AHI_TOTAL_SEGMENTS


def test_equatorial_domain_lands_mid_disk():
    """Kalimantan straddles the equator, so it must not be a polar segment."""
    segs = fetch_ahi.segments_for_bbox(margin=0)
    assert 3 <= segs[0] and segs[-1] <= 8


def test_object_keys_use_the_right_resolution_token_per_band():
    keys = fetch_ahi.wanted_keys(NOON, [5], ["B01", "B03", "B13"])
    assert keys[0].endswith("HS_H09_20260821_0600_B01_FLDK_R10_S0510.DAT.bz2")
    assert keys[1].endswith("HS_H09_20260821_0600_B03_FLDK_R05_S0510.DAT.bz2")
    assert keys[2].endswith("HS_H09_20260821_0600_B13_FLDK_R20_S0510.DAT.bz2")


def test_slot_prefix_matches_the_bucket_layout():
    assert fetch_ahi.slot_prefix(NOON) == "AHI-L1b-FLDK/2026/08/21/0600/"


# --------------------------------------------------------------------------
# Smoke mask
# --------------------------------------------------------------------------

def synthetic_scene(**patches):
    """A clear-forest scene, with optional rectangles painted into it."""
    shape = (C.GRID_NY, C.GRID_NX)
    base = {
        "B01": 8.0, "B03": 4.0, "B05": 14.0, "B06": 12.0,
        "B11": 294.0, "B13": 297.0, "B14": 296.0,
    }
    grids = {b: np.full(shape, v, dtype=np.float32) for b, v in base.items()}
    for name, (sl, values) in patches.items():
        for band, value in values.items():
            grids[band][sl] = value
    return grids


SMOKE_VALUES = {
    "B01": 22.0, "B03": 15.0, "B05": 10.0, "B06": 8.0,
    "B11": 292.0, "B13": 295.0, "B14": 294.0,
}
CLOUD_VALUES = {
    "B01": 55.0, "B03": 60.0, "B05": 40.0, "B06": 35.0,
    "B11": 248.0, "B13": 250.0, "B14": 250.0,
}
PATCH = (slice(100, 140), slice(100, 140))


def test_clear_forest_is_neither_smoke_nor_obscured():
    out = smoke_mask.classify(synthetic_scene(), NOON)
    assert out["stats"]["smoke_fraction"] == 0.0
    assert out["stats"]["obscured_fraction"] < 0.01


def test_a_smoke_patch_is_detected_and_only_there():
    out = smoke_mask.classify(
        synthetic_scene(p=(PATCH, SMOKE_VALUES)), NOON
    )
    assert out["smoke_bin"][PATCH].all()
    assert out["smoke_bin"].sum() == 40 * 40
    assert 0.0 < out["smoke"][PATCH].min() <= out["smoke"][PATCH].max() <= 1.0


def test_cloud_is_obscured_and_never_reported_as_smoke():
    out = smoke_mask.classify(
        synthetic_scene(p=(PATCH, CLOUD_VALUES)), NOON
    )
    assert out["obscured"][PATCH].all()
    assert not out["smoke_bin"][PATCH].any()


def test_bright_bare_ground_is_not_smoke():
    """Soil is as bright in the SWIR as in the red, so B03-B06 rejects it.

    Values chosen to stay under the cloud test, so this exercises the smoke
    discriminator rather than passing for the wrong reason.
    """
    soil = {"B01": 18.0, "B03": 18.0, "B05": 28.0, "B06": 22.0,
            "B11": 300.0, "B13": 305.0, "B14": 304.0}
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, soil)), NOON)
    assert not out["obscured"][PATCH].any(), "should not be screened out as cloud"
    assert not out["smoke_bin"][PATCH].any()


# --------------------------------------------------------------------------
# Water. Sediment plumes fake the smoke signature, and the domain now reaches
# the Malacca Strait, where getting this wrong would answer "does the haze
# reach Singapore" with somebody's river outflow.
# --------------------------------------------------------------------------

SEDIMENT_WATER = {
    "B01": 17.0, "B03": 8.5, "B05": 2.4, "B06": 1.3,
    "B11": 298.0, "B13": 300.0, "B14": 300.0,
}
SMOKE_OVER_WATER = {
    "B01": 30.0, "B03": 16.0, "B05": 2.5, "B06": 1.5,
    "B11": 296.0, "B13": 299.0, "B14": 298.0,
}


def test_turbid_coastal_water_is_not_smoke():
    """Sediment lifts red while SWIR stays near zero — the same B03-B06 that
    smoke produces. Measured values from the Riau coast, 2026-08-21."""
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SEDIMENT_WATER)), NOON)
    assert not out["smoke_bin"][PATCH].any()


def test_thick_smoke_over_water_is_still_detected():
    """The water rule must not simply blind the map at sea; smoke crossing to
    Singapore travels over the Strait."""
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SMOKE_OVER_WATER)), NOON)
    assert out["smoke_bin"][PATCH].all()


def test_water_is_identified_by_the_16um_band():
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SEDIMENT_WATER)), NOON)
    # The synthetic background is land-like (B05 = 14), the patch is water.
    expected = (PATCH[0].stop - PATCH[0].start) * (PATCH[1].stop - PATCH[1].start)
    assert out["stats"]["water_fraction"] * C.GRID_NY * C.GRID_NX == pytest.approx(
        expected, rel=0.01
    )


def test_land_smoke_rules_still_apply_off_water():
    """Modest smoke over land must survive. It would fail the stricter water
    test, so the fix must not be applied everywhere."""
    land_smoke = {
        "B01": 18.0, "B03": 11.0, "B05": 16.0, "B06": 4.0,
        "B11": 293.0, "B13": 296.0, "B14": 295.0,
    }
    # Too faint for the at-sea rule on both counts.
    assert land_smoke["B01"] < C.WATER_SMOKE_B01_MIN
    assert land_smoke["B01"] - land_smoke["B03"] < C.WATER_SMOKE_B01_MINUS_B03_MIN
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, land_smoke)), NOON)
    assert out["smoke_bin"][PATCH].all()


# --------------------------------------------------------------------------
# Solar zenith correction — without it the mask shrinks every afternoon
# --------------------------------------------------------------------------

def test_sun_correction_brightens_more_as_the_sun_gets_lower():
    band = np.array([[10.0, 10.0]], dtype=np.float32)
    high = smoke_mask.sunz_correct(band, np.array([[90.0, 90.0]]))
    low = smoke_mask.sunz_correct(band, np.array([[30.0, 30.0]]))
    assert high[0, 0] == pytest.approx(10.0)
    assert low[0, 0] == pytest.approx(20.0)  # sin(30) = 0.5


def test_sun_correction_is_bounded_near_the_terminator():
    """A naive 1/cos blows up at sunrise; the floor keeps it finite."""
    out = smoke_mask.sunz_correct(np.array([[10.0]], np.float32), np.array([[0.5]]))
    assert np.isfinite(out).all()
    assert out[0, 0] == pytest.approx(10.0 / C.MIN_COS_SZA)


def test_the_same_smoke_is_detected_early_and_late_in_the_day():
    """The point of the correction: thresholds mean the same thing all day."""
    afternoon = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)  # 16:00 WIB, low sun
    raw_scene = synthetic_scene(p=(PATCH, SMOKE_VALUES))

    # Dim the whole scene the way a lower sun would.
    lon2d, lat2d = common.grid_mesh()
    mu = np.sin(np.radians(common.solar_elevation(afternoon, lat2d, lon2d)))
    dimmed = dict(raw_scene)
    for band in ("B01", "B03", "B05", "B06"):
        dimmed[band] = (raw_scene[band] * np.clip(mu, C.MIN_COS_SZA, 1.0)).astype(
            np.float32
        )

    out = smoke_mask.classify(dimmed, afternoon)
    assert out["smoke_bin"][PATCH].all()


def test_nothing_is_detected_at_night():
    midnight = datetime(2026, 8, 21, 17, 0, tzinfo=UTC)
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SMOKE_VALUES)), midnight)
    assert out["stats"]["smoke_fraction"] == 0.0
    assert out["stats"]["obscured_fraction"] == 1.0


def test_missing_data_counts_as_obscured_not_clear():
    grids = synthetic_scene()
    grids["B13"][PATCH] = np.nan
    out = smoke_mask.classify(grids, NOON)
    assert out["obscured"][PATCH].all()


def test_speckle_below_the_blob_floor_is_dropped():
    tiny = (slice(200, 202), slice(200, 202))  # 4 cells, floor is 12
    out = smoke_mask.classify(synthetic_scene(p=(tiny, SMOKE_VALUES)), NOON)
    assert out["smoke_bin"].sum() == 0


def test_blob_filter_keeps_large_and_drops_small():
    m = np.zeros((40, 40), dtype=bool)
    m[2:4, 2:4] = True     # 4 cells
    m[10:20, 10:20] = True  # 100 cells
    kept = smoke_mask.remove_small_blobs(m, 12)
    assert kept[10:20, 10:20].all()
    assert not kept[2:4, 2:4].any()


# --------------------------------------------------------------------------
# Advection
# --------------------------------------------------------------------------

def uniform_flow(dx, dy, shape=(60, 60)):
    flow = np.zeros(shape + (2,), dtype=np.float32)
    flow[..., 0] = dx
    flow[..., 1] = dy
    return flow


def test_warp_moves_a_blob_in_the_direction_of_the_flow():
    field = np.zeros((60, 60), dtype=np.float32)
    field[30, 30] = 1.0
    moved = advect.warp(field, np.full((60, 60), 4.0, np.float32),
                        np.zeros((60, 60), np.float32))
    row, col = np.unravel_index(np.argmax(moved), moved.shape)
    assert (row, col) == (30, 34)


def test_integration_advances_one_step_per_forecast_interval():
    field = np.zeros((60, 60), dtype=np.float32)
    field[30, 20] = 1.0
    obscured = np.zeros((60, 60), dtype=np.uint8)
    flow = uniform_flow(3.0, 0.0)
    smoke, _ = advect.integrate(
        field, obscured, flow, dt_minutes=C.FORECAST_STEP_MINUTES, steps=[30, 60, 90]
    )
    cols = [int(np.unravel_index(np.argmax(s), s.shape)[1]) for s in smoke]
    assert cols == [23, 26, 29]


def test_integration_rescales_when_the_pair_gap_is_not_the_step():
    """A 15-minute pair must still produce 30-minute steps."""
    field = np.zeros((60, 60), dtype=np.float32)
    field[30, 20] = 1.0
    smoke, _ = advect.integrate(
        field, np.zeros((60, 60), np.uint8), uniform_flow(2.0, 0.0),
        dt_minutes=15, steps=[30],
    )
    assert int(np.unravel_index(np.argmax(smoke[0]), smoke[0].shape)[1]) == 24


def test_smoke_decays_as_it_disperses():
    field = np.ones((60, 60), dtype=np.float32)
    smoke, _ = advect.integrate(
        field, np.zeros((60, 60), np.uint8), uniform_flow(0.0, 0.0),
        dt_minutes=30, steps=[30, 60],
    )
    assert smoke[0][30, 30] == pytest.approx(C.ADVECTION_DECAY_PER_STEP)
    assert smoke[1][30, 30] == pytest.approx(C.ADVECTION_DECAY_PER_STEP ** 2)


def test_obscured_areas_stay_flagged_as_they_travel():
    obscured = np.zeros((60, 60), dtype=np.uint8)
    obscured[30, 20] = 1
    _, flag = advect.integrate(
        np.zeros((60, 60), np.float32), obscured, uniform_flow(3.0, 0.0),
        dt_minutes=30, steps=[30],
    )
    assert flag[0][30, 23] > 0.5


def test_bearing_is_reported_as_the_direction_wind_comes_from():
    """Smoke moving east came from the west: 270 degrees."""
    flow = uniform_flow(5.0, 0.0)
    diag = advect.flow_diagnostics(flow, np.ones((60, 60), bool), 30.0)
    assert diag["bearing_from_deg"] == pytest.approx(270.0)
    assert diag["u_ms"] > 0 and diag["v_ms"] == pytest.approx(0.0)


def test_northward_motion_reports_a_southerly():
    # Rows increase southward, so northward motion is negative dy.
    diag = advect.flow_diagnostics(uniform_flow(0.0, -5.0), np.ones((60, 60), bool), 30.0)
    assert diag["bearing_from_deg"] == pytest.approx(180.0)
    assert diag["v_ms"] > 0


def test_speed_conversion_is_metres_per_second():
    # One cell per minute = ~2226 m / 60 s.
    diag = advect.flow_diagnostics(
        uniform_flow(30.0, 0.0, shape=(10, 10)), np.ones((10, 10), bool), 30.0
    )
    expected = advect.metres_per_cell() / 60.0
    assert diag["median_speed_ms"] == pytest.approx(expected, rel=0.01)


def test_flow_conditioning_ignores_untrusted_cells():
    """A wild value under cloud must not leak into the trusted field."""
    flow = np.zeros((60, 60, 2), dtype=np.float32)
    flow[..., 0] = 2.0
    flow[20:30, 20:30, 0] = 500.0  # nonsense behind cloud
    trusted = np.ones((60, 60), dtype=bool)
    trusted[20:30, 20:30] = False
    out = advect.condition_flow(flow, trusted)
    assert out[..., 0].max() < 3.0


# --------------------------------------------------------------------------
# Verification scoring
# --------------------------------------------------------------------------

def test_a_perfect_forecast_scores_csi_one(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    truth_slot = NOON + timedelta(minutes=30)

    smoke = np.zeros((C.GRID_NY, C.GRID_NX), dtype=np.float32)
    smoke[PATCH] = 1.0
    np.savez_compressed(
        common.mask_path(truth_slot),
        slot=common.slot_id(truth_slot),
        smoke=smoke,
        smoke_bin=(smoke > 0).astype(np.uint8),
        obscured=np.zeros_like(smoke, dtype=np.uint8),
        clear=np.ones_like(smoke, dtype=np.uint8),
        stats=np.array([{"clear_fraction": 1.0}], dtype=object),
    )

    fc = {
        "slot": common.slot_id(NOON),
        "steps": [30, 60],
        "smoke": np.stack([smoke, smoke]),
    }
    advect.log = common.setup_logging(False)
    score = advect.verify(fc, truth_slot)
    assert score["csi"] == 1.0
    assert score["misses"] == 0 and score["false_alarms"] == 0


def test_a_displaced_forecast_is_penalised(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    truth_slot = NOON + timedelta(minutes=30)

    obs = np.zeros((C.GRID_NY, C.GRID_NX), dtype=np.float32)
    obs[PATCH] = 1.0
    np.savez_compressed(
        common.mask_path(truth_slot),
        slot=common.slot_id(truth_slot),
        smoke=obs,
        smoke_bin=(obs > 0).astype(np.uint8),
        obscured=np.zeros_like(obs, dtype=np.uint8),
        clear=np.ones_like(obs, dtype=np.uint8),
        stats=np.array([{"clear_fraction": 1.0}], dtype=object),
    )

    pred = np.zeros_like(obs)
    pred[300:340, 300:340] = 1.0  # nowhere near
    advect.log = common.setup_logging(False)
    score = advect.verify({"slot": common.slot_id(NOON), "steps": [30],
                           "smoke": np.stack([pred])}, truth_slot)
    assert score["csi"] == 0.0
    assert score["pod"] == 0.0


# --------------------------------------------------------------------------
# FIRMS parsing
# --------------------------------------------------------------------------

def test_viirs_confidence_letters_are_ranked_not_parsed_as_numbers():
    assert fetch_firms.confidence_ok({"confidence": "h"})
    assert fetch_firms.confidence_ok({"confidence": "n"})
    assert not fetch_firms.confidence_ok({"confidence": "l"})


def test_modis_confidence_is_numeric():
    assert fetch_firms.confidence_ok({"confidence": "80"})
    assert not fetch_firms.confidence_ok({"confidence": "10"})


def test_hotspots_outside_the_domain_are_dropped():
    inside = fetch_firms.row_to_feature(
        {"longitude": "114.0", "latitude": "-2.0", "acq_date": "2026-08-21",
         "acq_time": "0530", "frp": "12.5", "confidence": "n"},
        "VIIRS_SNPP_NRT",
    )
    assert inside["geometry"]["coordinates"] == [114.0, -2.0]
    assert inside["properties"]["acq_utc"] == "2026-08-21T05:30Z"

    outside = fetch_firms.row_to_feature(
        {"longitude": "140.0", "latitude": "35.0"}, "MODIS_NRT"
    )
    assert outside is None


def test_malformed_rows_do_not_crash_the_layer():
    assert fetch_firms.row_to_feature({"longitude": "x", "latitude": "y"}, "s") is None
    assert fetch_firms.row_to_feature({}, "s") is None


def test_weak_fires_are_filtered_by_radiative_power(monkeypatch):
    monkeypatch.setattr(C, "FIRMS_MIN_FRP_MW", 20.0)
    assert fetch_firms.frp_ok({"frp": "35.2"})
    assert fetch_firms.frp_ok({"frp": "20"})
    assert not fetch_firms.frp_ok({"frp": "8.4"})


def test_unmeasured_fires_are_kept_not_treated_as_zero(monkeypatch):
    """Absent FRP is unknown, not weak. Dropping it would bias the map."""
    monkeypatch.setattr(C, "FIRMS_MIN_FRP_MW", 20.0)
    assert fetch_firms.frp_ok({})
    assert fetch_firms.frp_ok({"frp": ""})
    assert fetch_firms.frp_ok({"frp": "not-a-number"})


def test_zero_floor_disables_the_power_filter(monkeypatch):
    monkeypatch.setattr(C, "FIRMS_MIN_FRP_MW", 0.0)
    assert fetch_firms.frp_ok({"frp": "0.1"})


def test_firms_area_string_is_west_south_east_north():
    """Order matters and is easy to transpose. Derived from config so that
    legitimately moving the domain does not fail the test."""
    assert fetch_firms.area_string() == "{},{},{},{}".format(
        C.LON_MIN, C.LAT_MIN, C.LON_MAX, C.LAT_MAX
    )
    west, south, east, north = (float(v) for v in fetch_firms.area_string().split(","))
    assert west < east and south < north


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_overlay_is_transparent_where_there_is_no_smoke():
    from pipeline import publish

    density = np.zeros((10, 10), dtype=np.float32)
    density[5, 5] = 0.9
    rgba = np.array(publish.smoke_png(density))
    assert rgba[0, 0, 3] == 0
    assert rgba[5, 5, 3] > C.SMOKE_MIN_ALPHA


def test_thicker_smoke_is_more_opaque():
    from pipeline import publish

    density = np.array([[0.1, 0.9]], dtype=np.float32)
    rgba = np.array(publish.smoke_png(density))
    assert rgba[0, 1, 3] > rgba[0, 0, 3]


def test_obscured_overlay_is_hatched_not_solid():
    from pipeline import publish

    m = np.ones((20, 20), dtype=np.uint8)
    alpha = np.array(publish.obscured_png(m))[..., 3]
    assert alpha.max() == C.OBSCURED_ALPHA
    assert (alpha == 0).any(), "a solid fill would read as data, not as a gap"
