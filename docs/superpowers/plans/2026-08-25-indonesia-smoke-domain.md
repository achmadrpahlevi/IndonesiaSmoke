# IndonesiaSmoke All-Indonesia Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the live smoke map from 100–120°E / 5°S–8°N to all of Indonesia plus the countries its haze reaches, gate the sun angle per pixel instead of per scene, display time in UTC, and ship it from a new `IndonesiaSmoke` repository without ever interrupting the map currently live.

**Architecture:** Everything is driven from `pipeline/config.py`. The domain change is four constants; the substantive work is turning `common.domain_is_daylit`'s single domain-wide boolean into a per-pixel calibration mask that flows through `smoke_mask` (a new obscured class), `advect` (flow restricted to the eroded intersection of two frames' calibrated footprints) and `publish` (a second hatch layer and a re-based smoke fraction). The Leaflet page gains a UTC clock with a three-zone local line and a toggle for the new layer.

**Tech Stack:** Python 3.11, numpy, scipy.ndimage, opencv-python-headless (Farnebäck), satpy + pyresample (AHI ingest), Pillow, pytest, Leaflet 1.9, GitHub Actions + GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-08-25-indonesia-smoke-domain-design.md`

## Global Constraints

- Work happens on branch `indonesia-domain` in the existing repository. **`main` is never touched until Task 10.** The scheduled workflow runs from the default branch, so the live map cannot see this branch.
- All tunables live in `pipeline/config.py`. Nothing is tuned inline (PLAN.md non-negotiable #5).
- Nothing outside the calibrated sun-angle range is ever advected (PLAN.md non-negotiable #2).
- Domain: `LON_MIN, LON_MAX = 94.5, 142.0`; `LAT_MIN, LAT_MAX = -11.5, 8.0`; `GRID_RES_DEG = 0.02` unchanged → `GRID_NX = 2375`, `GRID_NY = 975`.
- `MIN_SCENE_ELEVATION_DEG = 40.0` unchanged in value; changed in application from per-scene to per-pixel.
- `MIN_SOLAR_ELEVATION_DEG = 12.0` unchanged — it remains the per-pixel *visibility* test and must not be conflated with the calibration test.
- `MIN_CALIBRATED_FRACTION = 0.05` — new constant, the share of the domain that must be inside the calibrated range for a scene to publish at all.
- Display: `DISPLAY_TZ_OFFSET_HOURS = 0`, `DISPLAY_TZ_LABEL = "UTC"`.
- No smoke-mask threshold values change. `SMOKE_*`, `WATER_*`, `CLOUD_*` and `RAYLEIGH_CORRECT` are untouched.
- Run `python -m pytest tests -q` after every task. It must stay green.
- Every task ends in a commit. Commit messages are normal prose, not caveman.

**A warning that will cost you an hour if ignored:** after Task 1 the grid shape changes from (650, 1000) to (975, 2375). Any `state/*.npz` on your disk from before Task 1 has the old shape and will produce broadcasting errors. Delete `state/` locally the first time you run the pipeline after Task 1. Task 1 adds a guard so this fails loudly rather than strangely.

---

### Task 1: Domain extent, grid geometry, and a guard against stale state

**Files:**
- Modify: `pipeline/config.py:44-56` (domain grid block), `pipeline/config.py:73-74` (`AHI_FALLBACK_SEGMENTS`), `pipeline/config.py:58` (`RESAMPLE_RADIUS_M`), `pipeline/config.py:425-431` (`FOCUS_LON`/`FOCUS_LAT`)
- Modify: `pipeline/common.py:59-94` (`grid_area_def`, `view_bounds`)
- Modify: `pipeline/smoke_mask.py:230-239` (`load_mask_npz`)
- Modify: `pipeline/advect.py:159-171` (`mask_is_usable`)
- Modify: `pipeline/publish.py:167-185` (`newest_daylight_mask`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `C.LON_MIN/LON_MAX/LAT_MIN/LAT_MAX/GRID_NX/GRID_NY` at their new values; `common.view_bounds() -> list[list[float]]` now equal to `common.leaflet_bounds()`; `smoke_mask.mask_shape_ok(path: Path) -> bool`.

- [ ] **Step 1: Write the failing tests**

Replace `test_domain_reaches_singapore_and_peninsular_malaysia` (`tests/test_pipeline.py:53-60`) and `test_opening_view_is_centred_on_kalimantan` (`:62-66`) with these, and add the rest:

```python
def test_domain_reaches_the_whole_country_and_its_haze_neighbours():
    """The name claims all of Indonesia. The grid has to back that up, and
    still contain the downwind cities the product was originally built for."""
    for name, lat, lon in [
        ("Sabang", 5.89, 95.32),
        ("Merauke", -8.49, 140.40),
        ("Rote", -10.75, 123.12),
        ("Miangas", 5.56, 126.58),
        ("Jayapura", -2.53, 140.72),
        ("Kupang", -10.18, 123.61),
        ("Singapore", 1.35, 103.82),
        ("Kuala Lumpur", 3.14, 101.69),
        ("Pontianak", -0.02, 109.34),
    ]:
        assert C.LON_MIN <= lon <= C.LON_MAX, name
        assert C.LAT_MIN <= lat <= C.LAT_MAX, name


def test_grid_dimensions_match_the_new_extent():
    assert C.GRID_NX == 2375
    assert C.GRID_NY == 975


def test_opening_view_is_the_data_itself():
    """The mirroring trick existed to keep Borneo centred after the westward
    extension to 100E. On a full-country domain it produces a view nearly 200
    degrees wide, so the opening view is simply the data bounds."""
    assert common.view_bounds() == common.leaflet_bounds()


def test_the_new_extent_still_lands_mid_disk():
    """Papua sits under the sub-satellite point and Sabang is far west, but
    the whole domain must still be an equatorial band of segments."""
    segs = fetch_ahi.segments_for_bbox(margin=0)
    assert 3 <= segs[0] and segs[-1] <= 8


def test_mask_from_a_different_grid_is_rejected(tmp_path):
    """Changing the domain invalidates every cached npz. Without this guard
    a restored Actions cache broadcasts a (650, 1000) mask against a
    (975, 2375) grid and fails somewhere far away from the cause."""
    path = tmp_path / "mask_20260821_0700.npz"
    np.savez_compressed(
        path,
        slot="20260821_0700",
        smoke=np.zeros((650, 1000), dtype=np.float32),
        smoke_bin=np.zeros((650, 1000), dtype=np.uint8),
        obscured=np.zeros((650, 1000), dtype=np.uint8),
        clear=np.zeros((650, 1000), dtype=np.uint8),
        stats=np.array([{"clear_fraction": 1.0}], dtype=object),
    )
    assert smoke_mask.mask_shape_ok(path) is False
    assert advect.mask_is_usable(path) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests -q -k "new_extent or whole_country or grid_dimensions or opening_view or different_grid"`
Expected: FAIL. `test_grid_dimensions_match_the_new_extent` fails on `assert 600 == 2375`; `test_mask_from_a_different_grid_is_rejected` fails with `AttributeError: module 'pipeline.smoke_mask' has no attribute 'mask_shape_ok'`.

- [ ] **Step 3: Widen the domain in config.py**

Replace the comment and constants at `pipeline/config.py:44-56`:

```python
# All of Indonesia, Sabang to Merauke and Rote to Miangas, plus the
# countries its haze actually reaches: Singapore, Peninsular Malaysia,
# Sarawak, Sabah, Brunei, Timor-Leste and the PNG border.
#
# The 47 degrees of longitude is close to free. AHI segments divide the disk
# by scan line, so a wider longitude range is a bigger crop of files already
# being downloaded. Only the southward extension to 11.5S buys a segment:
# segments_for_bbox returns [4,5,6,7,8] here against [4,5,6,7] before, so 35
# files instead of 28, about 300 MB instead of 240 MB.
LON_MIN, LON_MAX = 94.5, 142.0
LAT_MIN, LAT_MAX = -11.5, 8.0
GRID_RES_DEG = 0.02  # ~2.2 km at the equator

# Rows run north -> south (image convention). Values are pixel centres.
GRID_NX = int(round((LON_MAX - LON_MIN) / GRID_RES_DEG))  # 2375
GRID_NY = int(round((LAT_MAX - LAT_MIN) / GRID_RES_DEG))  # 975
```

Then at `pipeline/config.py:58`, replace the `RESAMPLE_RADIUS_M` comment and value:

```python
# Nearest-neighbour radius for resampling AHI -> grid, metres. At 94.5 E the
# viewing zenith angle is about 60 degrees, so a 2 km nadir pixel is
# stretched past 5 km and the old 5000 m radius left holes along the western
# edge. Verified by counting the NaN fraction west of 97 E — see the QA step
# in Task 10.
RESAMPLE_RADIUS_M = 8000
```

And at `pipeline/config.py:73-74`:

```python
AHI_TOTAL_SEGMENTS = 10
AHI_FALLBACK_SEGMENTS = [4, 5, 6, 7, 8]
```

- [ ] **Step 4: Re-centre the opening view**

Replace `pipeline/config.py:425-431` (the `FOCUS_LON`/`FOCUS_LAT` block):

```python
# Where the map opens. On the old domain this was deliberately NOT the middle
# of the grid: FOCUS_LON pulled the view back onto Borneo so the westward
# extension to Singapore did not shove the subject off to the right. A
# full-country domain has no single subject to pull towards, so this is now
# simply the centre of the data and view_bounds() is the data bounds.
FOCUS_LON = 118.25
FOCUS_LAT = -1.75
```

Replace `common.view_bounds` at `pipeline/common.py:82-94`:

```python
def view_bounds() -> list[list[float]]:
    """Opening view. Identical to the data bounds.

    This used to mirror the data bounds about FOCUS_LON so that Borneo stayed
    centred after the domain was extended west to 100 E. Across the full
    country that mirroring produces an opening view close to 200 degrees
    wide. Kept as a function because meta.json publishes it and the page
    reads it; the indirection costs nothing and leaves room to re-centre
    later without another meta.json change.
    """
    return leaflet_bounds()
```

Also update the stale area definition at `pipeline/common.py:66-74`:

```python
    return AreaDefinition(
        area_id="indonesia",
        description="Indonesia smoke domain, plate carree",
        proj_id="eqc",
```

- [ ] **Step 5: Add the stale-state guard**

Add to `pipeline/smoke_mask.py`, immediately after `load_mask_npz` (after `:239`):

```python
def mask_shape_ok(path: Path) -> bool:
    """Is this cached mask on the current grid?

    The Actions cache restores state/ across runs, so a domain change hands
    the pipeline npz files of the wrong shape. Left unguarded they broadcast
    against the new grid and fail somewhere far from the cause. Rejecting
    them here means the run simply has no usable history, which every stage
    already knows how to survive.
    """
    try:
        with np.load(path, allow_pickle=True) as data:
            return tuple(data["smoke"].shape) == (C.GRID_NY, C.GRID_NX)
    except (OSError, ValueError, KeyError):
        return False
```

In `pipeline/advect.py`, change `mask_is_usable` (`:159-171`) to check shape first:

```python
def mask_is_usable(path: Path) -> bool:
    """Could this mask actually see anything, and is it on this grid?

    Night masks are 100% obscured. Pairing one with the first daylight frame
    would have Farneback match darkness against a lit scene, which is exactly
    the situation at dawn every morning.

    The shape test catches cached state left over from a different domain.
    """
    from .smoke_mask import mask_shape_ok

    if not mask_shape_ok(path):
        log.warning("%s is from a different grid — ignoring", path.name)
        return False
    try:
        with np.load(path, allow_pickle=True) as data:
            stats = dict(data["stats"][0])
    except (OSError, ValueError, KeyError, IndexError):
        return False
    return float(stats.get("clear_fraction", 0.0)) >= C.MIN_CLEAR_FRACTION
```

In `pipeline/publish.py`, add the same guard inside `newest_daylight_mask` (`:175-184`), right after the `domain_is_daylit` check:

```python
    for slot, path in reversed(masks):
        # The scene-level gate, not the per-pixel daylit fraction. A morning
        # scene is fully lit by that measure and still must not be published:
        # selecting on daylit_fraction alone put a 30.70% morning map on the
        # site with frozen=true, which labelled it without withholding it.
        if not common.domain_is_daylit(slot)[0]:
            continue
        if not mask_shape_ok(path):
            log.warning("%s is from a different grid — ignoring", path.name)
            continue
        mask = load_mask_npz(path)
```

and extend the import at `pipeline/publish.py:29`:

```python
from .smoke_mask import load_mask_npz, mask_shape_ok
```

- [ ] **Step 6: Fix the tests the widened domain invalidates**

Two existing tests assert the old geometry and must be updated, not deleted.

`test_opening_view_still_contains_all_the_data` (`tests/test_pipeline.py:68-73`) still passes unchanged — equality satisfies `<=` and `>=`. Leave it; it now asserts the weaker property, which is still worth holding.

`test_equatorial_domain_lands_mid_disk` (`:143-146`) is superseded by `test_the_new_extent_still_lands_mid_disk` from Step 1. Delete the old one — its docstring names Kalimantan and its assertion is duplicated exactly by the new test.

Check `test_view_zenith_is_larger_at_the_western_edge` near `:394-397`, which compares `rayleigh.view_zenith` at `C.LON_MIN` against `C.LON_MAX`. At the old bounds both edges were west of the 140.7°E sub-satellite point. At the new bounds `LON_MAX = 142.0` sits *east* of it, so the east edge zenith is now small and the assertion may flip meaning. Run it; if it passes, add this comment above it, and if it fails, replace the eastern comparison point with `C.AHI_SATELLITE_LON`:

```python
def test_view_zenith_is_larger_at_the_western_edge():
    """Sabang is ~60 degrees off the sub-satellite point; Papua is under it.
    This is the single largest new source of cross-domain inconsistency."""
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS, no failures. If a test fails on an unstated grid assumption, fix the test to assert the property rather than the old number.

- [ ] **Step 8: Commit**

```bash
git add pipeline/config.py pipeline/common.py pipeline/smoke_mask.py pipeline/advect.py pipeline/publish.py tests/test_pipeline.py
git commit -m "Widen the domain to all of Indonesia

94.5-142E, 11.5S-8N: Sabang to Merauke, Rote to Miangas, plus Singapore,
Malaysia, Brunei and Timor-Leste. 2375x975 cells against 1000x650.

segments_for_bbox goes from [4,5,6,7] to [4,5,6,7,8], so 35 files instead
of 28. The extra longitude is free; only the southward extension buys a
segment. RESAMPLE_RADIUS_M goes 5000 to 8000 because at 94.5E the viewing
zenith is about 60 degrees and a 2 km nadir pixel is stretched past 5 km.

view_bounds mirrored the data about FOCUS_LON to keep Borneo centred after
the extension west to 100E. Across the full country that gives a view close
to 200 degrees wide, so it is now the data bounds.

Cached npz from the old grid is rejected by shape rather than allowed to
broadcast against the new one and fail somewhere unrelated."
```

---

### Task 2: Per-pixel calibration mask

**Files:**
- Modify: `pipeline/config.py` (add `MIN_CALIBRATED_FRACTION` beside `MIN_SCENE_ELEVATION_DEG`, around `:320`)
- Modify: `pipeline/common.py:204-224` (`domain_is_daylit`), add `calibrated_mask` and `calibrated_fraction`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `C.GRID_NY`, `C.GRID_NX` at their Task 1 values.
- Produces:
  - `common.calibrated_mask(dt: datetime) -> np.ndarray` — bool, shape `(C.GRID_NY, C.GRID_NX)`, True where `solar_elevation >= C.MIN_SCENE_ELEVATION_DEG`.
  - `common.calibrated_fraction(dt: datetime) -> float` — share of the domain inside the calibrated range, computed on the same `[::40]` subsample the old gate used.
  - `common.domain_is_daylit(dt) -> tuple[bool, float]` — signature unchanged; the boolean now means "some usable part of the country is in window".

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` in the sun-geometry section:

```python
def test_calibrated_mask_is_the_grid_shape_and_agrees_with_elevation():
    m = common.calibrated_mask(NOON)
    assert m.shape == (C.GRID_NY, C.GRID_NX)
    assert m.dtype == bool
    lon2d, lat2d = common.grid_mesh()
    elev = common.solar_elevation(NOON, lat2d, lon2d)
    assert np.array_equal(m, elev >= C.MIN_SCENE_ELEVATION_DEG)


def test_the_calibrated_footprint_sweeps_east_to_west():
    """Papua enters the window while Sumatra is still dark, and leaves it
    while Sumatra is still high. This is the whole reason the gate had to
    stop being domain-wide."""
    papua_lat, papua_lon = np.array([-2.5]), np.array([140.7])
    sumatra_lat, sumatra_lon = np.array([0.5]), np.array([101.5])

    dawn_east = datetime(2026, 8, 21, 23, 30, tzinfo=UTC)   # 08:30 WIT
    assert common.solar_elevation(dawn_east, papua_lat, papua_lon)[0] >= 40
    assert common.solar_elevation(dawn_east, sumatra_lat, sumatra_lon)[0] < 40

    late_west = datetime(2026, 8, 21, 7, 30, tzinfo=UTC)    # 14:30 WIB
    assert common.solar_elevation(late_west, sumatra_lat, sumatra_lon)[0] >= 40
    assert common.solar_elevation(late_west, papua_lat, papua_lon)[0] < 40


def test_a_scene_publishes_when_any_usable_part_is_in_window():
    """The old gate needed half the domain above 40 degrees at once. Across
    47 degrees of longitude that is never true for long, and it would throw
    away both Papua's morning and Sumatra's afternoon."""
    dawn_east = datetime(2026, 8, 21, 23, 30, tzinfo=UTC)   # 08:30 WIT
    assert common.calibrated_fraction(dawn_east) < 0.5, "old gate would refuse"
    assert common.domain_is_daylit(dawn_east)[0] is True


def test_the_whole_country_dark_is_still_refused():
    """Widening the gate must not turn it off."""
    deep_night = datetime(2026, 8, 21, 17, 0, tzinfo=UTC)   # 00:00 WIB
    assert common.calibrated_fraction(deep_night) == 0.0
    assert common.domain_is_daylit(deep_night)[0] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests -q -k "calibrated or usable_part or country_dark"`
Expected: FAIL with `AttributeError: module 'pipeline.common' has no attribute 'calibrated_mask'`.

- [ ] **Step 3: Add the constant**

In `pipeline/config.py`, directly below the `MIN_SCENE_ELEVATION_DEG = 40.0` line and its comment block:

```python
# How much of the domain must be inside the calibrated range for the scene to
# be published at all.
#
# On the Kalimantan domain this was effectively 0.5, applied to the whole
# domain at once. Across 47 degrees of longitude — 3.1 hours of solar time —
# that gate leaves a publishing window of roughly four hours and discards
# both Papua's morning and Sumatra's afternoon. The sun test is now per pixel
# (see common.calibrated_mask), so this only has to answer "is there anything
# worth drawing", and the day runs from about 23:15 UTC to 09:00 UTC.
#
# Raise it to shorten the day at both ends without touching the physics.
MIN_CALIBRATED_FRACTION = 0.05
```

- [ ] **Step 4: Implement the mask**

Replace `pipeline/common.py:204-224` with:

```python
def calibrated_mask(dt: datetime) -> np.ndarray:
    """Per pixel: is this pixel inside the calibrated sun-angle range?

    Full grid resolution, because smoke_mask uses it to classify pixels. The
    scene-level questions below subsample instead — they only need a
    fraction, and the full mesh is 2.3 million cells.
    """
    lon2d, lat2d = grid_mesh()
    return solar_elevation(dt, lat2d, lon2d) >= C.MIN_SCENE_ELEVATION_DEG


def calibrated_fraction(dt: datetime) -> float:
    """Share of the domain inside the calibrated range, subsampled."""
    lats = grid_lats()[::40]
    lons = grid_lons()[::40]
    lon2d, lat2d = np.meshgrid(lons, lats)
    elev = solar_elevation(dt, lat2d, lon2d)
    return float(np.mean(elev >= C.MIN_SCENE_ELEVATION_DEG))


def domain_is_daylit(dt: datetime) -> tuple[bool, float]:
    """(any usable part of the country is in window, mean solar elevation).

    Still the SCENE-level test, and still not the per-pixel visibility one: a
    scene can be perfectly visible and outside the range the thresholds were
    tuned in. What changed with the domain is the meaning of "the scene". On
    the Kalimantan grid, asking whether half the domain was above 40 degrees
    was a fair proxy for the whole picture. Across Indonesia there is no such
    thing as one sun angle for the domain, so this asks only whether there is
    anything worth drawing and smoke_mask decides pixel by pixel.

    The second element is the mean elevation over the WHOLE domain, kept for
    logging. Do not gate on it: with half the country in darkness at any
    instant it sits far below the calibration threshold even at the best
    moment of the day.
    """
    frac_lit = calibrated_fraction(dt)

    # Morning scenes were once withheld regardless of sun height, because sun
    # and sensor share a side before local noon. The cause turned out to be
    # the water test rather than the geometry, so this is disabled and left
    # as a valve. Note it is now read in UTC, not WIB. See
    # MIN_SCENE_LOCAL_HOUR.
    local_hour = to_display_tz(dt).hour + to_display_tz(dt).minute / 60.0
    afternoon = local_hour >= C.MIN_SCENE_LOCAL_HOUR

    lats = grid_lats()[::40]
    lons = grid_lons()[::40]
    lon2d, lat2d = np.meshgrid(lons, lats)
    mean_elev = float(np.mean(solar_elevation(dt, lat2d, lon2d)))

    return (frac_lit >= C.MIN_CALIBRATED_FRACTION and afternoon), mean_elev
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests -q -k "calibrated or usable_part or country_dark"`
Expected: PASS.

- [ ] **Step 6: Fix the existing gate tests**

Three existing tests assert the old domain-wide behaviour and must be re-aimed at the per-pixel behaviour rather than deleted, because each pins a fix that cost real time.

Replace `test_low_sun_scenes_are_withheld_rather_than_guessed_at` (`tests/test_pipeline.py:316-329`):

```python
def test_low_sun_pixels_are_hatched_rather_than_guessed_at():
    """At 16:30 WIB the slant path is ~3 air masses and thin regional haze
    reads as thick smoke. Those pixels must not be published as smoke.

    On the Kalimantan domain the refusal was at scene level, because one sun
    angle described the whole grid. Across Indonesia it cannot be: at 16:30
    in Sumatra, Papua has been dark for hours and central Kalimantan is at a
    perfectly good angle. The refusal is now per pixel.
    """
    late = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)  # 16:30 WIB
    sumatra_lat, sumatra_lon = np.array([0.5]), np.array([101.5])
    assert common.solar_elevation(late, sumatra_lat, sumatra_lon)[0] < 40

    inside = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)  # 14:00 WIB
    kali_lat, kali_lon = np.array([-1.0]), np.array([114.0])
    assert common.solar_elevation(inside, kali_lat, kali_lon)[0] >= 40
```

The assertions in that test's tail about `classify` move to Task 3, where `classify` learns about calibration. Delete them here.

`test_a_scene_can_be_keepable_without_being_publishable` (`:581-592`) asserts `not common.domain_is_daylit(edge)[0]` for a 08:20 WIB scene. Under per-pixel gating that scene now *is* publishable, because Papua is well inside the window at 01:20 UTC. The distinction the test protects — keepable versus publishable — is still real, but the example must move to a time when the whole country is out of window. Replace its body:

```python
def test_a_scene_can_be_keepable_without_being_publishable():
    """The first run of every day fetches a flow partner below the publish
    gate. Pruning on that gate deleted it seconds later, so no pair could
    ever form at dawn.

    The example moved when gating went per pixel: 08:20 WIB is now
    publishable, because Papua is three hours further into its day. The
    keepable-but-unpublishable window is the country's own edges.
    """
    edge = common.parse_slot_id("20260821_2200")    # 05:00 WIT, visible east
    inside = common.parse_slot_id("20260821_0700")  # 14:00 WIB, afternoon
    night = common.parse_slot_id("20260821_1500")   # 00:00 WIT

    assert common.scene_is_visible(edge)
    assert not common.domain_is_daylit(edge)[0], "edge scene is not publishable"
    assert common.scene_is_visible(inside) and common.domain_is_daylit(inside)[0]
    assert not common.scene_is_visible(night)
```

Run this one on its own and adjust the two slot times if the assertions do not hold — the point is the three states existing, not these exact minutes:

Run: `python -m pytest tests/test_pipeline.py::test_a_scene_can_be_keepable_without_being_publishable -q`

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/config.py pipeline/common.py tests/test_pipeline.py
git commit -m "Gate the sun angle per pixel instead of per scene

domain_is_daylit needed half the domain above 40 degrees at once. That was
a fair proxy on a 20-degree grid. Across 47 degrees of longitude - 3.1 hours
of solar time - it leaves a four-hour publishing window and throws away both
Papua's morning and Sumatra's afternoon.

calibrated_mask answers the same question per pixel at full grid resolution.
The scene gate now only asks whether there is anything worth drawing, at
MIN_CALIBRATED_FRACTION, so the day runs from roughly 23:15 to 09:00 UTC.
That window is an outcome of the physics, not a clock written down anywhere.

The mean elevation domain_is_daylit returns is now for logging only. With
half the country dark at any instant it sits below the calibration threshold
even at the best moment of the day, so gating on it would refuse everything."
```

---

### Task 3: The mask learns about calibration

**Files:**
- Modify: `pipeline/smoke_mask.py:50-182` (`classify`), `:206-239` (`save_mask`, `load_mask_npz`), `:283-291` (QA caption)
- Modify: `pipeline/config.py` (add `UNCALIBRATED_*` render constants near `OBSCURED_*`, around `:415`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `common.calibrated_mask(dt)` from Task 2.
- Produces: `classify()` returns an extra key `"uncalibrated"` (uint8, grid shape); `stats` gains `"calibrated_fraction"`, `"uncalibrated_fraction"`, `"mean_calibrated_elevation"`, and `"smoke_fraction"` is **renamed** to `"smoke_fraction_of_visible"` with a new denominator. `save_mask`/`load_mask_npz` carry `"uncalibrated"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` in the mask section:

```python
def test_uncalibrated_pixels_are_obscured_and_never_smoke():
    """Nothing outside the calibrated sun range is advected, so it must land
    in obscured. PLAN.md non-negotiable #2."""
    late = datetime(2026, 8, 21, 9, 30, tzinfo=UTC)  # 16:30 WIB
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SMOKE_VALUES)), late)
    uncal = out["uncalibrated"].astype(bool)
    assert uncal.any(), "some of the country must be out of window at 16:30 WIB"
    assert not (out["smoke_bin"].astype(bool) & uncal).any()
    assert (out["obscured"].astype(bool) | ~uncal).all()


def test_smoke_fraction_is_measured_against_what_was_visible():
    """Over the whole country most pixels are out of window at any instant.
    Dividing by the full grid makes the headline number collapse toward zero
    and stop being comparable to anything."""
    inside = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)  # 14:00 WIB
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SMOKE_VALUES)), inside)
    s = out["stats"]
    assert "smoke_fraction" not in s, "renamed, so nobody compares old numbers"
    visible = (out["obscured"] == 0).sum()
    assert s["smoke_fraction_of_visible"] == pytest.approx(
        out["smoke_bin"].sum() / max(visible, 1)
    )


def test_mean_calibrated_elevation_ignores_the_dark_half_of_the_country():
    inside = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)  # 14:00 WIB
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SMOKE_VALUES)), inside)
    s = out["stats"]
    assert s["mean_calibrated_elevation"] >= C.MIN_SCENE_ELEVATION_DEG
    assert s["mean_calibrated_elevation"] > s["mean_solar_elevation"]


def test_a_calibrated_scene_is_not_hatched_by_the_sun_rule_where_it_matters():
    """Moved here from the old scene-gate test: a pixel inside the window
    still gets a clean answer."""
    inside = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)  # 14:00 WIB
    out = smoke_mask.classify(synthetic_scene(p=(PATCH, SMOKE_VALUES)), inside)
    assert out["smoke_bin"][PATCH].all()
```

Note for the implementer: `PATCH` and `SMOKE_VALUES` and `synthetic_scene` already exist in `tests/test_pipeline.py`. Check where `PATCH` sits on the grid — it is defined against `C.GRID_NY`/`C.GRID_NX`, so it moves with the domain. If `test_a_calibrated_scene_is_not_hatched...` fails because `PATCH` has landed somewhere out of window at 14:00 WIB, redefine `PATCH` over Kalimantan explicitly rather than weakening the assertion:

```python
# Kalimantan, the only region ever cross-checked against an independent
# sensor. Row/column of 0.5S, 114E on the current grid.
_PY = int((C.LAT_MAX - (-0.5)) / C.GRID_RES_DEG)
_PX = int((114.0 - C.LON_MIN) / C.GRID_RES_DEG)
PATCH = (slice(_PY, _PY + 40), slice(_PX, _PX + 40))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests -q -k "uncalibrated or measured_against or mean_calibrated or calibrated_scene"`
Expected: FAIL with `KeyError: 'uncalibrated'`.

- [ ] **Step 3: Add the render constants**

In `pipeline/config.py`, immediately after the `OBSCURED_HATCH_WIDTH` line:

```python
# "Outside the calibrated sun-angle range" is a different statement from
# "cloud in the way", and drawing them the same way would tell the reader
# they are the same problem. Cooler colour, wider spacing, so the two hatches
# are distinguishable at a glance without a legend lookup.
UNCALIBRATED_RGB = (120, 140, 175)
UNCALIBRATED_ALPHA = 80
UNCALIBRATED_HATCH_PERIOD = 16
UNCALIBRATED_HATCH_WIDTH = 2
```

- [ ] **Step 4: Classify against the calibration mask**

In `pipeline/smoke_mask.py`, inside `classify`, add after the `daylit` line (`:103`):

```python
    daylit = elev >= C.MIN_SOLAR_ELEVATION_DEG

    # Two different questions that must not share a threshold. `daylit` asks
    # whether the sensor can see this pixel at all. `calibrated` asks whether
    # the thresholds below mean anything at this sun angle. On the Kalimantan
    # domain the second was answered once for the whole scene; across
    # Indonesia the answer differs by three hours of solar time from end to
    # end, so it is answered per pixel here.
    calibrated = elev >= C.MIN_SCENE_ELEVATION_DEG
    uncalibrated = ~calibrated
```

Change the `obscured` line (`:116`):

```python
    # Never claim to see through cloud, missing data, darkness, or a sun
    # angle the thresholds were not tuned for.
    obscured = (~valid) | (~daylit) | uncalibrated | cloud
```

Change the `usable` line (`:123`):

```python
    usable = (
        valid
        & daylit
        & calibrated
        & ~cloud
        & (b13 >= C.SMOKE_B13_MIN_K)
        & (btd >= C.SMOKE_BTD_1114_MIN)
    )
```

Replace the `stats` block (`:157-174`):

```python
    n = float(smoke_bin.size)
    n_visible = float(max((~obscured).sum(), 1))
    stats = {
        "water_fraction": float(water.sum() / n),
        "valid_fraction": float(valid.sum() / n),
        "daylit_fraction": float(daylit.sum() / n),
        "calibrated_fraction": float(calibrated.sum() / n),
        "uncalibrated_fraction": float(uncalibrated.sum() / n),
        "cloud_fraction": float(cloud.sum() / n),
        "obscured_fraction": float(obscured.sum() / n),
        "clear_fraction": float((~obscured).sum() / n),
        # Against what was actually VISIBLE, not against the whole grid.
        # Over the full country most pixels are out of window at any instant,
        # so the old whole-grid denominator would drag this toward zero and
        # make it incomparable to the 5-11% reference values measured on the
        # Kalimantan domain. Renamed so nobody compares them by accident.
        "smoke_fraction_of_visible": float(smoke_bin.sum() / n_visible),
        # How much of the detection sits over water. Shallow, turbid coastal
        # water is spectrally close to thin smoke in these bands and passes
        # both branches, so this is the number that says how much of the map
        # to distrust rather than a fixed disclaimer.
        "smoke_over_water_fraction": (
            float((smoke_bin & water).sum() / max(smoke_bin.sum(), 1))
        ),
        "mean_solar_elevation": float(elev.mean()),
        # The mean that means something. The whole-domain mean above sits
        # below the calibration threshold even at the best moment of the day,
        # because half the country is dark.
        "mean_calibrated_elevation": (
            float(elev[calibrated].mean()) if calibrated.any() else 0.0
        ),
    }
```

Replace the return block (`:176-182`):

```python
    return {
        "smoke": density,
        "smoke_bin": smoke_bin.astype(np.uint8),
        "obscured": obscured.astype(np.uint8),
        "uncalibrated": uncalibrated.astype(np.uint8),
        "clear": ((~obscured) & (~smoke_bin)).astype(np.uint8),
        "stats": stats,
    }
```

- [ ] **Step 5: Carry the new layer through storage**

In `save_mask` (`:209-226`), add the array and fix the log line:

```python
    np.savez_compressed(
        path,
        slot=common.slot_id(slot),
        smoke=result["smoke"],
        smoke_bin=result["smoke_bin"],
        obscured=result["obscured"],
        uncalibrated=result["uncalibrated"],
        clear=result["clear"],
        stats=np.array([result["stats"]], dtype=object),
    )
    s = result["stats"]
    log.info(
        "mask %s: smoke %.2f%% of visible, obscured %.1f%% "
        "(%.1f%% out of sun-angle window), clear %.1f%%, calibrated sun %.0f deg",
        common.slot_id(slot),
        100 * s["smoke_fraction_of_visible"],
        100 * s["obscured_fraction"],
        100 * s["uncalibrated_fraction"],
        100 * s["clear_fraction"],
        s["mean_calibrated_elevation"],
    )
```

In `load_mask_npz` (`:230-239`), add the key with a fallback so a mask written before this task still loads:

```python
def load_mask_npz(path: Path) -> dict:
    with np.load(path, allow_pickle=True) as data:
        keys = set(data.files)
        return {
            "slot": str(data["slot"]),
            "smoke": data["smoke"],
            "smoke_bin": data["smoke_bin"],
            "obscured": data["obscured"],
            # Masks written before per-pixel gating have no such layer. They
            # folded nothing into obscured on this account, so an all-zero
            # stand-in is the honest reading of what they claimed.
            "uncalibrated": (
                data["uncalibrated"]
                if "uncalibrated" in keys
                else np.zeros_like(data["obscured"])
            ),
            "clear": data["clear"],
            "stats": dict(data["stats"][0]),
        }
```

- [ ] **Step 6: Fix the QA caption**

In `qa_frame` (`:284-291`), the caption reads `s['smoke_fraction']` which no longer exists:

```python
    d.text(
        (4, 6),
        f"{common.slot_id(slot)} UTC  |  "
        f"{common.to_display_tz(slot):%H:%M} {C.DISPLAY_TZ_LABEL}   "
        f"smoke {100 * s['smoke_fraction_of_visible']:.2f}% of visible  "
        f"obscured {100 * s['obscured_fraction']:.0f}%",
        fill=(230, 230, 235),
    )
```

- [ ] **Step 7: Fix the two remaining references to the old stat name**

`pipeline/publish.py:315` logs `mask["stats"]["smoke_fraction"]`. Change it to `mask["stats"]["smoke_fraction_of_visible"]` and the format string to `%.2f%% smoke of visible`.

`tests/test_pipeline.py:191` and `:351` assert `out["stats"]["smoke_fraction"] == 0.0`. Change both to `out["stats"]["smoke_fraction_of_visible"] == 0.0`.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add pipeline/smoke_mask.py pipeline/config.py pipeline/publish.py tests/test_pipeline.py
git commit -m "Fold uncalibrated pixels into obscured and re-base the smoke fraction

classify now tests the sun angle per pixel. Anything outside the calibrated
range joins cloud and darkness in obscured, so it is never advected, and it
is also kept as its own layer: 'we cannot judge this sun angle' and 'there
is cloud in the way' are different statements and should not be drawn alike.

smoke_fraction becomes smoke_fraction_of_visible, measured against what was
actually visible rather than the whole grid. Over the full country most
pixels are out of window at any instant, so the old denominator would drag
the number toward zero. Renamed rather than redefined so that nobody
compares it to the 5-11% measured on the Kalimantan domain by accident.

mean_solar_elevation is kept but is no longer meaningful as a gate; the mean
over calibrated pixels is added beside it."
```

---

### Task 4: Flow only where both frames were calibrated

**Files:**
- Modify: `pipeline/advect.py` (add `calibrated_pair_footprint`, use it in `forecast` at `:221-227`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `common.calibrated_mask(dt)` (Task 2); `C.FARNEBACK["winsize"]`.
- Produces: `advect.calibrated_pair_footprint(prev_slot: datetime, curr_slot: datetime) -> np.ndarray` — bool, grid shape, the intersection of the two frames' calibrated masks eroded by `winsize // 2` cells.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py` in the advection section:

```python
def test_pair_footprint_is_the_intersection_of_both_frames():
    prev = common.parse_slot_id("20260821_0600")
    curr = common.parse_slot_id("20260821_0630")
    fp = advect.calibrated_pair_footprint(prev, curr)
    both = common.calibrated_mask(prev) & common.calibrated_mask(curr)
    assert fp.shape == (C.GRID_NY, C.GRID_NX)
    assert fp.dtype == bool
    assert not (fp & ~both).any(), "footprint may never exceed the intersection"


def test_pair_footprint_is_eroded_away_from_the_moving_edge(monkeypatch):
    """The calibrated region sweeps east to west across the day. At its edges
    smoke appears and disappears for reasons that are nothing to do with
    wind, and Farneback reads that as motion. Everything within half a
    correlation window of the boundary is therefore dropped."""
    half = np.zeros((C.GRID_NY, C.GRID_NX), dtype=bool)
    half[:, 1000:] = True
    monkeypatch.setattr(common, "calibrated_mask", lambda dt: half)

    prev = common.parse_slot_id("20260821_0600")
    curr = common.parse_slot_id("20260821_0630")
    fp = advect.calibrated_pair_footprint(prev, curr)

    margin = C.FARNEBACK["winsize"] // 2
    row = fp[C.GRID_NY // 2]
    first_true = int(np.argmax(row))
    assert first_true >= 1000 + margin, "boundary was not eroded far enough"
    assert row[-1], "the interior must survive erosion"


def test_flow_is_not_trusted_across_the_footprint_edge(monkeypatch, tmp_path):
    """End to end: a cell just inside the calibrated intersection but within
    a correlation window of its edge contributes nothing to the flow."""
    monkeypatch.setattr(C, "STATE_DIR", tmp_path)
    half = np.zeros((C.GRID_NY, C.GRID_NX), dtype=bool)
    half[:, 1000:] = True
    monkeypatch.setattr(common, "calibrated_mask", lambda dt: half)

    prev = common.parse_slot_id("20260821_0600")
    curr = common.parse_slot_id("20260821_0630")
    fp = advect.calibrated_pair_footprint(prev, curr)
    edge_col = 1005  # inside the intersection, inside the erosion margin
    assert half[:, edge_col].all()
    assert not fp[:, edge_col].any()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests -q -k "pair_footprint or footprint_edge"`
Expected: FAIL with `AttributeError: module 'pipeline.advect' has no attribute 'calibrated_pair_footprint'`.

- [ ] **Step 3: Implement the footprint**

Add to `pipeline/advect.py`, after `condition_flow` (after `:72`):

```python
def calibrated_pair_footprint(prev_slot: datetime, curr_slot: datetime) -> np.ndarray:
    """Where flow between these two frames is worth believing.

    The calibrated region sweeps east to west across the day, so between two
    frames thirty minutes apart its edges have moved. Smoke entering or
    leaving through a moving boundary looks exactly like smoke moving, and
    Farneback has no way to tell the difference — it would report a confident
    westward drift at the edges of the map on every single cycle.

    smoke_mask already folds uncalibrated pixels into obscured, so the raw
    intersection is handled. What this adds is the margin: Farneback
    correlates over a winsize window, so a cell within half a window of the
    boundary has part of its correlation patch sitting in the region that
    just changed state. Those cells are dropped.
    """
    from scipy.ndimage import binary_erosion

    both = common.calibrated_mask(prev_slot) & common.calibrated_mask(curr_slot)
    margin = max(1, int(C.FARNEBACK["winsize"]) // 2)
    return binary_erosion(
        both,
        structure=np.ones((3, 3), dtype=bool),
        iterations=margin,
        border_value=0,
    )
```

- [ ] **Step 4: Use it in the forecast**

In `forecast()`, replace the `trusted` block (`:221-227`):

```python
    raw = compute_flow(prev, curr)
    footprint = calibrated_pair_footprint(t_prev, t_curr)
    trusted = (
        (prev["obscured"] == 0)
        & (curr["obscured"] == 0)
        & ((prev["smoke_bin"] > 0) | (curr["smoke_bin"] > 0))
        & footprint
    )
    quality["footprint_cells"] = int(footprint.sum())
    flow = condition_flow(raw, trusted)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests -q -k "pair_footprint or footprint_edge"`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pipeline/advect.py tests/test_pipeline.py
git commit -m "Restrict optical flow to the eroded calibrated intersection

The calibrated region sweeps east to west across the day, so between two
frames thirty minutes apart its edges have moved. Smoke entering or leaving
through a moving boundary is indistinguishable from smoke moving, and
Farneback would report a confident westward drift at the edges of the map on
every cycle.

The intersection itself is already covered, because smoke_mask folds
uncalibrated pixels into obscured. What this adds is the margin: Farneback
correlates over winsize, so a cell within half a window of the boundary has
part of its patch in the region that just changed state."
```

---

### Task 5: Publish the new layer and drop the scene-level caveat

**Files:**
- Modify: `pipeline/publish.py:56-68` (`obscured_png`), `:85-130` (`build_meta`), `:188-319` (`publish`)
- Modify: `pipeline/config.py` (delete `CAVEAT_LOW_SUN` and `CAVEAT_BELOW_ELEVATION_DEG`, around `:280-290`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `mask["uncalibrated"]` and the new `stats` keys (Task 3); `common.calibrated_fraction` (Task 2).
- Produces: `publish.hatch_png(mask, rgb, alpha, period, width) -> Image.Image`; `meta["layers"]["uncalibrated"]`; `meta["scene_stats"]` carrying the renamed keys.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pipeline.py`:

```python
def test_obscured_and_uncalibrated_are_drawn_as_separate_layers():
    """Cloud and 'we cannot judge this sun angle' are different statements.
    Drawing them with one layer tells the reader they are the same problem,
    and double-hatches every uncalibrated pixel."""
    from PIL import Image

    ny, nx = 40, 40
    obscured = np.zeros((ny, nx), dtype=np.uint8)
    uncal = np.zeros((ny, nx), dtype=np.uint8)
    obscured[:, :20] = 1          # cloud on the left
    obscured[:, 20:] = 1          # uncalibrated also lands in obscured
    uncal[:, 20:] = 1

    cloud_only = publish.hatch_png(
        (obscured.astype(bool) & ~uncal.astype(bool)),
        C.OBSCURED_RGB, C.OBSCURED_ALPHA,
        C.OBSCURED_HATCH_PERIOD, C.OBSCURED_HATCH_WIDTH,
    )
    a = np.array(cloud_only)[..., 3]
    assert a[:, :20].any(), "cloud must be hatched"
    assert not a[:, 20:].any(), "uncalibrated must not be hatched twice"


def test_low_sun_caveat_is_gone():
    """It asserted 'Kalimantan is the calibrated part of this map', which the
    per-pixel hatch now shows directly rather than claiming in prose."""
    assert not hasattr(C, "CAVEAT_LOW_SUN")
    assert not hasattr(C, "CAVEAT_BELOW_ELEVATION_DEG")
```

Add `from pipeline import publish` to the imports at `tests/test_pipeline.py:19`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests -q -k "separate_layers or caveat_is_gone"`
Expected: FAIL with `AttributeError: module 'pipeline.publish' has no attribute 'hatch_png'`.

- [ ] **Step 3: Generalise the hatch renderer**

Replace `obscured_png` in `pipeline/publish.py:56-68`:

```python
def hatch_png(mask: np.ndarray, rgb, alpha: int, period: int, width: int):
    """Diagonal hatching. Never a solid fill — it must not read as data.

    Parameterised because there are now two reasons a pixel is not judged,
    and they must be visually distinguishable: cloud in the way, and a sun
    angle these thresholds were never calibrated for.
    """
    from PIL import Image

    m = np.asarray(mask).astype(bool)
    ny, nx = m.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    hatch = ((xx + yy) % period) < width

    out_rgb = np.zeros((ny, nx, 3), dtype=np.uint8)
    out_rgb[..., :] = np.array(rgb, dtype=np.uint8)
    out_alpha = np.where(m & hatch, alpha, 0).astype(np.uint8)
    return Image.fromarray(np.dstack([out_rgb, out_alpha[..., None]]))
```

- [ ] **Step 4: Render both layers**

In `publish()`, replace the `layers` block (`:266-272`):

```python
    outdir.mkdir(parents=True, exist_ok=True)
    uncal = mask["uncalibrated"].astype(bool)
    # Uncalibrated pixels are inside `obscured` — that is what stops them
    # being advected — so the cloud layer must subtract them or every one of
    # them is hatched twice, in two colours, at two spacings.
    cloud_only = mask["obscured"].astype(bool) & ~uncal
    layers = {
        "now": save_png(smoke_png(mask["smoke"]), outdir / "smoke_now.png"),
        "obscured": save_png(
            hatch_png(
                cloud_only,
                C.OBSCURED_RGB, C.OBSCURED_ALPHA,
                C.OBSCURED_HATCH_PERIOD, C.OBSCURED_HATCH_WIDTH,
            ),
            outdir / "obscured.png",
        ),
        "uncalibrated": save_png(
            hatch_png(
                uncal,
                C.UNCALIBRATED_RGB, C.UNCALIBRATED_ALPHA,
                C.UNCALIBRATED_HATCH_PERIOD, C.UNCALIBRATED_HATCH_WIDTH,
            ),
            outdir / "uncalibrated.png",
        ),
        "forecast": [],
    }
```

In `build_meta`, add the layer to the index (`:121-125`):

```python
        "layers": {
            "now": layers["now"],
            "obscured": layers["obscured"],
            "uncalibrated": layers.get("uncalibrated"),
            "forecast": forecast_index,
        },
```

and in `freeze_existing`'s cold-start skeleton (`:150`):

```python
            "layers": {
                "now": None,
                "obscured": None,
                "uncalibrated": None,
                "forecast": [],
            },
```

- [ ] **Step 5: Delete the scene-level low-sun caveat**

Remove `CAVEAT_BELOW_ELEVATION_DEG` and `CAVEAT_LOW_SUN` from `pipeline/config.py` (around `:280-290`) and leave this note in their place:

```python
# CAVEAT_LOW_SUN and CAVEAT_BELOW_ELEVATION_DEG were removed when sun gating
# went per pixel. The caveat said "Kalimantan is the calibrated part of this
# map", which was true of a scene-level gate on a domain small enough to have
# one sun angle. The uncalibrated hatch now shows exactly which pixels are
# outside the range, on the map, which is better than a sentence saying that
# some of them are.
```

Remove the block that used them in `pipeline/publish.py:286-295`:

```python
    firms = common.read_json(outdir / "firms.geojson") or {}
    meta = build_meta(scene_slot, mask, fc, layers, firms.get("properties"))
    over_water = float(mask["stats"].get("smoke_over_water_fraction", 0.0))
```

- [ ] **Step 6: Fix the freeze reason, which gated on the wrong number**

In `publish()` (`:227-246`), `mean_elev` is the whole-domain mean and now sits below 40 even at the best moment of the day, so the first branch would claim "sun too low" whenever anything else froze the product. Replace the branch conditions:

```python
    frozen_reason = ""
    if not is_current:
        calibrated = common.calibrated_fraction(now)
        shown = (
            f"showing the last published scene, "
            f"{common.to_display_tz(scene_slot):%d %b %H:%M} {C.DISPLAY_TZ_LABEL}"
        )
        if calibrated < C.MIN_CALIBRATED_FRACTION:
            # Not "the sun is low over the domain" — across Indonesia there is
            # no such thing as one sun angle for the domain. This says the
            # country as a whole is outside the window, which at the extremes
            # of the day it genuinely is.
            frozen_reason = (
                f"no part of the country is inside the calibrated sun-angle "
                f"window ({100 * calibrated:.0f}% of the domain); {shown}"
            )
        elif not lit_now:
            frozen_reason = (
                f"scenes before {C.MIN_SCENE_LOCAL_HOUR:.0f}:00 "
                f"{C.DISPLAY_TZ_LABEL} are not published; {shown}"
            )
        else:
            frozen_reason = (
                f"no recent scene; showing "
                f"{common.to_display_tz(scene_slot):%d %b %H:%M} "
                f"{C.DISPLAY_TZ_LABEL}, {age_minutes / 60:.0f} h old"
            )
        log.warning("publishing frozen: %s", frozen_reason)
```

Also fix the cold-start freeze at `:196-203`, which reports a mean elevation for the same reason:

```python
    scene_slot, mask_file, mask = newest_daylight_mask(masks)
    if scene_slot is None:
        newest_slot = masks[-1][0]
        calibrated = common.calibrated_fraction(newest_slot)
        return freeze_existing(
            outdir,
            newest_slot,
            f"nothing publishable in state yet "
            f"({100 * calibrated:.0f}% of the domain inside the sun-angle window)",
        )
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add pipeline/publish.py pipeline/config.py tests/test_pipeline.py
git commit -m "Publish the uncalibrated hatch as its own layer

Cloud in the way and a sun angle the thresholds were never tuned for are
different statements about a pixel, and one hatch would say they are the
same problem. The cloud layer subtracts the uncalibrated pixels, which sit
inside obscured so that nothing advects them, and they get their own colour
and spacing.

CAVEAT_LOW_SUN is deleted rather than reworded. It asserted that Kalimantan
is the calibrated part of the map, which was true of a scene-level gate on a
domain small enough to have one sun angle. The hatch now shows which pixels
those are.

The frozen reason gated on the whole-domain mean elevation, which with half
the country dark now sits below 40 degrees even at the best moment of the
day and would have claimed 'sun too low' for every freeze. It asks the
calibrated fraction instead."
```

---

### Task 6: UTC display, national city list, national validation regions

**Files:**
- Modify: `pipeline/config.py:434-436` (`DISPLAY_TZ_*`), `:438-452` (`CITIES`)
- Modify: `pipeline/validate.py:32-36` (`REGIONS`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `C.DISPLAY_TZ_OFFSET_HOURS = 0`, `C.DISPLAY_TZ_LABEL = "UTC"`; `C.CITIES` covering all three Indonesian time zones; `validate.REGIONS` covering the four regions the acceptance gate scores.

- [ ] **Step 1: Write the failing tests**

Replace `test_display_tz_is_wib` (`tests/test_pipeline.py:110`):

```python
def test_display_tz_is_utc():
    """Indonesia spans WIB, WITA and WIT. One clock for the whole map, and
    the page renders the three local equivalents beneath it."""
    assert C.DISPLAY_TZ_LABEL == "UTC"
    assert C.DISPLAY_TZ_OFFSET_HOURS == 0
    assert common.to_display_tz(NOON).hour == 6


def test_cities_span_all_three_time_zones():
    lons = [c["lon"] for c in C.CITIES]
    assert min(lons) < 105, "no WIB city in the far west"
    assert any(115 <= lon < 130 for lon in lons), "no WITA city"
    assert max(lons) >= 130, "no WIT city"
    for c in C.CITIES:
        assert C.LON_MIN <= c["lon"] <= C.LON_MAX, c["name"]
        assert C.LAT_MIN <= c["lat"] <= C.LAT_MAX, c["name"]


def test_validation_regions_cover_the_acceptance_gate():
    """Cutover requires enrichment above 3x in each of these. They have to
    exist before anything can be scored against them."""
    from pipeline import validate

    for name in ("Sumatra", "Kalimantan", "Sulawesi", "Papua"):
        assert name in validate.REGIONS
    for name, (w, e, s, n) in validate.REGIONS.items():
        assert C.LON_MIN <= w < e <= C.LON_MAX, name
        assert C.LAT_MIN <= s < n <= C.LAT_MAX, name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests -q -k "display_tz or three_time_zones or acceptance_gate"`
Expected: FAIL on `assert 'WIB' == 'UTC'`.

- [ ] **Step 3: Switch the display clock**

Replace `pipeline/config.py:434-436`:

```python
# Displayed time. UTC, because the domain spans WIB (UTC+7), WITA (UTC+8)
# and WIT (UTC+9) and no single Indonesian zone describes it. The page shows
# all three local equivalents on a second line, so nobody has to do the
# arithmetic; this is the one clock everything is stamped in.
#
# to_display_tz is the single seam, so publish, smoke_mask and validate all
# follow from these two values.
DISPLAY_TZ_OFFSET_HOURS = 0
DISPLAY_TZ_LABEL = "UTC"
```

- [ ] **Step 4: Extend the city list**

Replace the `CITIES` block at `pipeline/config.py:438-452`:

```python
# Cities called out in the site legend. Chosen for coverage rather than
# population: at least one anchor per major island so a reader can locate
# themselves anywhere in the domain, plus the downwind capitals that made
# the transboundary case for the original grid.
CITIES = [
    # Sumatra
    {"name": "Medan", "lat": 3.59, "lon": 98.67},
    {"name": "Pekanbaru", "lat": 0.51, "lon": 101.45},
    {"name": "Palembang", "lat": -2.99, "lon": 104.76},
    # Java
    {"name": "Jakarta", "lat": -6.21, "lon": 106.85},
    {"name": "Surabaya", "lat": -7.25, "lon": 112.75},
    # Kalimantan
    {"name": "Pontianak", "lat": -0.02, "lon": 109.34},
    {"name": "Palangkaraya", "lat": -2.21, "lon": 113.92},
    {"name": "Banjarmasin", "lat": -3.32, "lon": 114.59},
    {"name": "Samarinda", "lat": -0.50, "lon": 117.15},
    {"name": "Balikpapan", "lat": -1.24, "lon": 116.85},
    # Sulawesi and the east
    {"name": "Makassar", "lat": -5.15, "lon": 119.43},
    {"name": "Manado", "lat": 1.47, "lon": 124.84},
    {"name": "Ambon", "lat": -3.65, "lon": 128.19},
    {"name": "Jayapura", "lat": -2.53, "lon": 140.72},
    {"name": "Kupang", "lat": -10.18, "lon": 123.61},
    # Downwind, and the reason the domain still reaches this far west
    {"name": "Singapore", "lat": 1.35, "lon": 103.82},
    {"name": "Kuala Lumpur", "lat": 3.14, "lon": 101.69},
    {"name": "Kuching", "lat": 1.55, "lon": 110.34},
    {"name": "Bandar Seri Begawan", "lat": 4.90, "lon": 114.94},
]
```

- [ ] **Step 5: Extend the validation regions**

Replace `pipeline/validate.py:32-36`:

```python
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
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS. If any test asserts a WIB-based hour that is not the ones listed here, update the expected value — do not change `DISPLAY_TZ_OFFSET_HOURS` back.

- [ ] **Step 7: Commit**

```bash
git add pipeline/config.py pipeline/validate.py tests/test_pipeline.py
git commit -m "Stamp everything in UTC and cover the whole country

The domain spans WIB, WITA and WIT, and no single Indonesian zone describes
it. to_display_tz is the one seam, so publish, smoke_mask and validate all
follow from two constants. The page renders the three local equivalents on a
second line so nobody has to do the arithmetic.

Cities are chosen for coverage rather than population: an anchor per major
island so a reader can locate themselves anywhere in the domain, keeping the
downwind capitals that made the case for the original grid.

Validation regions gain Java, Sulawesi, Maluku and Papua, which is what the
cutover gate scores against."
```

---

### Task 7: The page

**Files:**
- Modify: `site/index.html` — `:6-7` (title, description), `:187` (aria-label), `:190` (`#title`), `:201` (legend keys), `:206` (layer toggles), `:276` (state), `:285-295` (`el`), `:322-360` (`renderStatus`), `:405-415` (overlay wiring), `:444` (valid label), `:620` (toggle listener)
- Test: manual, in a browser against a `site/data/` produced by Task 10's first run. There is no JS test harness in this repo and adding one is out of scope.

**Interfaces:**
- Consumes: `meta.layers.uncalibrated`, `meta.tz_label` (`"UTC"`), `meta.tz_offset_hours` (`0`) from Task 5 and Task 6.
- Produces: no interface other tasks depend on.

- [ ] **Step 1: Rename the page**

`site/index.html:6-7`:

```html
<title>IndonesiaSmoke — Indonesian wildfire smoke, now and next 3 hours</title>
<meta name="description" content="Indicative short-range smoke movement over Indonesia from Himawari-9, with NASA FIRMS hotspots. Daytime only.">
```

`:187`:

```html
<div id="map" role="application" aria-label="Map of smoke over Indonesia"></div>
```

`:190`, and add the local-time line directly beneath the status line:

```html
  <div id="title">IndonesiaSmoke <small>· Indonesia</small></div>
  <div id="status" aria-live="polite"><span class="dot" aria-hidden="true"></span><span id="statustext">loading…</span></div>
  <div id="localtime"></div>
  <div id="notice"></div>
```

Add a style rule beside the `#title small` rule at `:69`:

```css
  #localtime { font-size: 12px; color: var(--ink-muted); margin-top: 2px; }
```

- [ ] **Step 2: Add the legend key and the toggle**

At `:201`, after the existing obscured key:

```html
  <div class="key"><span class="swatch obscured"></span> Obscured by cloud</div>
  <div class="key"><span class="swatch uncal"></span> Sun angle outside calibration</div>
```

At `:206`, after the existing cloud toggle:

```html
    <label><input type="checkbox" id="tg-obscured" checked> Cloud hatching</label>
    <label><input type="checkbox" id="tg-uncal" checked> Sun-angle hatching</label>
```

Add the swatch style next to `.swatch.obscured` at `:136`. Copy the existing `.swatch.obscured` rule and change only its colour to `rgb(120, 140, 175)`, matching `C.UNCALIBRATED_RGB`.

- [ ] **Step 3: Render UTC with the three local equivalents**

In `renderStatus` (`:328-341`), change the defaults from WIB to UTC and add the second line:

```js
    var tz = m.tz_label || "UTC";
    var clock = localTime(m.scene_utc, m.tz_offset_hours != null ? m.tz_offset_hours : 0);
```

and immediately after `el.statusText.textContent = ...`:

```js
    // Indonesia spans three zones, so the UTC stamp above is the honest one
    // and this saves everyone the arithmetic.
    el.localtime.textContent =
      "= " + localTime(m.scene_utc, 7) + " WIB · " +
      localTime(m.scene_utc, 8) + " WITA · " +
      localTime(m.scene_utc, 9) + " WIT";
```

Add `localtime` to the `el` cache at `:285-295`:

```js
    localtime: document.getElementById("localtime"),
```

At `:444`, change the fallback:

```js
    el.valid.textContent = "valid " + f.valid + " " + (state.meta.tz_label || "UTC");
```

- [ ] **Step 4: Wire the new overlay**

Add `uncal: null` to the `state` object at `:276`, beside `obscured: null`.

After the obscured overlay block at `:407-414`, add:

```js
    if (state.uncal) { map.removeLayer(state.uncal); state.uncal = null; }
    if (m.layers && m.layers.uncalibrated) {
      state.uncal = L.imageOverlay(bust(m.layers.uncalibrated), bounds, {
        opacity: document.getElementById("tg-uncal").checked ? 1 : 0,
        interactive: false,
        alt: "Areas where the sun angle is outside the calibrated range"
      }).addTo(map);
    }
```

After the obscured toggle listener at `:620`, add:

```js
  document.getElementById("tg-uncal").addEventListener("change", function (e) {
    if (state.uncal) state.uncal.setOpacity(e.target.checked ? 1 : 0);
  });
```

- [ ] **Step 5: Sanity check the markup**

Run: `python -c "import html.parser,sys; p=html.parser.HTMLParser(); p.feed(open('site/index.html',encoding='utf-8').read()); print('parsed ok')"`
Expected: `parsed ok`. This catches an unclosed tag; it does not check the JavaScript. The real check is Task 10's first published run.

- [ ] **Step 6: Commit**

```bash
git add site/index.html
git commit -m "Rename the page and give the sun-angle hatch its own controls

UTC is the primary stamp, with WIB, WITA and WIT on a second line so nobody
has to do the arithmetic across three zones. The uncalibrated layer gets a
legend key, a toggle and a colour distinct from cloud hatching, because the
two say different things about a pixel."
```

---

### Task 8: Workflow and repository identity

**Files:**
- Modify: `.github/workflows/pipeline.yml:10` (cron), `:27` (concurrency), `:55-56` (cache keys), `:99-102` (bot identity)
- Modify: `pipeline/config.py:20-33` (env var names)
- Modify: `pipeline/common.py:37` (logger name), `pipeline/__init__.py:1` (docstring)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: env vars `INDOSMOKE_WORK`, `INDOSMOKE_STATE`, `INDOSMOKE_OUT`; logger name `indosmoke`.

- [ ] **Step 1: Write the failing test**

```python
def test_state_dir_reads_the_new_env_prefix(monkeypatch, tmp_path):
    """OPERATIONS.md tells a cold session to point STATE elsewhere when it
    wants to keep a scene the pruner would drop. The name has to be right or
    that instruction silently does nothing."""
    import importlib

    monkeypatch.setenv("INDOSMOKE_STATE", str(tmp_path))
    reloaded = importlib.reload(C)
    try:
        assert reloaded.STATE_DIR == tmp_path
    finally:
        monkeypatch.delenv("INDOSMOKE_STATE")
        importlib.reload(C)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests -q -k "new_env_prefix"`
Expected: FAIL — `STATE_DIR` still points at the repo default because the old `KALIMSMOKE_STATE` name is what is read.

- [ ] **Step 3: Rename the env vars**

`pipeline/config.py:20-33`, three occurrences:

```python
WORK_DIR = Path(os.environ.get("INDOSMOKE_WORK", REPO_ROOT / "work"))
STATE_DIR = Path(os.environ.get("INDOSMOKE_STATE", REPO_ROOT / "state"))
SITE_DATA_DIR = Path(os.environ.get("INDOSMOKE_OUT", REPO_ROOT / "site" / "data"))
```

`pipeline/common.py:37`:

```python
    return logging.getLogger("indosmoke")
```

`pipeline/common.py:300` — the docstring in `prune_state` names the old env var:

```python
    That is right for the pipeline and surprising for diagnostics: set
    INDOSMOKE_STATE elsewhere and stub this out when you want to keep one.
```

`pipeline/__init__.py:1`:

```python
"""IndonesiaSmoke pipeline. Run each stage with `python -m pipeline.<stage>`."""
```

- [ ] **Step 4: Update the workflow**

`.github/workflows/pipeline.yml`, replacing the cron at `:10` and its comment:

```yaml
    # Every 30 minutes, deliberately at :17 and :47 rather than :00 and :30.
    # GitHub delays or silently DROPS scheduled runs under load, and load peaks
    # at the top of the hour, which is exactly where */30 fires. Measured on
    # 2026-08-22: gaps of 27-34 min through the quiet hours, then 52, 91 and 64
    # min once the busy period started, with nothing queued — the runs were
    # never created. Odd minutes sit in a much shorter queue.
    #
    # :17/:47 rather than the KalimantanWildfires repo's :07/:37 so the two
    # do not hit the FIRMS API in the same minute on the same key while both
    # are live.
    - cron: "17,47 * * * *"
```

`:27`:

```yaml
  group: indosmoke-pipeline
```

`:55-56`:

```yaml
          key: indosmoke-state-${{ github.run_id }}
          restore-keys: indosmoke-state-
```

`:99-102`:

```yaml
          GIT_AUTHOR_NAME: indosmoke-bot
          GIT_AUTHOR_EMAIL: indosmoke-bot@users.noreply.github.com
          GIT_COMMITTER_NAME: indosmoke-bot
          GIT_COMMITTER_EMAIL: indosmoke-bot@users.noreply.github.com
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/config.py pipeline/common.py pipeline/__init__.py .github/workflows/pipeline.yml tests/test_pipeline.py
git commit -m "Rename the pipeline's identity to IndonesiaSmoke

Env prefixes, logger, bot identity, concurrency group and cache keys. Only
used in CI and in the diagnostic instructions in OPERATIONS.md, so no
compatibility shim.

The cron moves to :17 and :47. The odd-minute reasoning still holds -
GitHub silently drops runs queued at the top of the hour - and the offset
from the old repo's :07/:37 keeps the two off the FIRMS API in the same
minute while both are live."
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md` — the header, the "Domain" section, the BMKG/ASMC comparison table row for Coverage, and "Known limits"
- Modify: `OPERATIONS.md` — title, live URL, schedule, health check, both checkpoints, the log-line table, reference values
- Modify: `PLAN.md` — one note at the top
- Test: none. Prose.

**Interfaces:**
- Consumes: everything above.
- Produces: nothing code depends on.

- [ ] **Step 1: Update the operations health check**

`OPERATIONS.md`'s 60-second check reads `m['scene_stats']['smoke_fraction']`, which no longer exists, and `m['scene_local']`/`m['tz_label']`, which are now UTC. Replace the command:

```bash
gh run list --workflow=pipeline.yml --limit 8
curl -s "https://achmadrpahlevi.github.io/IndonesiaSmoke/data/meta.json?cb=$(date +%s)" \
  | python -c "import sys,json;m=json.load(sys.stdin);s=m['scene_stats'];print(m['scene_utc'],'| smoke %.2f%% of visible'%(s['smoke_fraction_of_visible']*100),'| in window %.0f%%'%(s['calibrated_fraction']*100),'| frozen',m['frozen'],'|',len(m['layers']['forecast']),'steps')"
```

- [ ] **Step 2: Rewrite the two checkpoints**

The 15:00 WIB and 08:30 WIB checkpoints describe a domain with one sun angle. Replace them with two that describe the new day, keeping the same shape — what to expect, and what to be suspicious of:

```markdown
### 00:00 UTC — Papua and the east

07:00 WIB, 09:00 WIT. The eastern third of the country is inside the window
and Sumatra is not yet. Expect the sun-angle hatch to cover most of the map
west of about 120°E, and expect that to look alarming the first few times.

| expect | value |
|---|---|
| scene | within ~45 min, `frozen False` |
| in window | roughly 20–35% of the domain |
| smoke | measured against visible area, not the grid |
| hatch | most of Sumatra, Java and west Kalimantan |

### 06:00 UTC — the widest moment

13:00 WIB, 15:00 WIT. The largest share of the country is in window at once.
This is the closest the new domain gets to the old product's midday.

| expect | value |
|---|---|
| in window | the day's maximum, roughly 45–60% |
| forecast | 6 steps |

Numbers to be suspicious of, unchanged in spirit: detection over the Malacca
Strait and Peninsular Malaysia at once has meant a broken water test every
time it has appeared. New to this domain: detection over the Banda and
Arafura seas, which is where sun glint near the sub-satellite point is
expected to bite and has never been checked.
```

- [ ] **Step 3: Update the rest of OPERATIONS.md**

- Title: `# Operations — checking IndonesiaSmoke`
- Live and repo URLs to `IndonesiaSmoke`
- Schedule: `:17 and :47`, and keep the whole explanation of why odd minutes
- Log-line table: `kalimsmoke |` becomes `indosmoke |`
- Safety valves table: add `MIN_CALIBRATED_FRACTION` (0.05; raise to shorten the day at both ends)
- "Expected behaviour that looks like breakage": add an entry

```markdown
- **Most of the map hatched.** Correct, and the single biggest change from
  the Kalimantan product. Across 47 degrees of longitude only part of the
  country is ever inside the calibrated sun-angle window at once, and the
  rest is hatched rather than guessed at. At the extremes of the day the
  hatched share is most of the map.
```

- Reference values: the 08:50/14:00 WIB enrichment table was measured on the old domain and denominator. Keep it, under a heading saying so:

```markdown
Reference values measured 2026-08-21/22 **on the Kalimantan domain**, before
the smoke fraction was re-based on visible area. Kept because the enrichment
figures are still the benchmark — they share no physics with the mask — but
the smoke percentages are not comparable to what the current product reports.
```

- Test count: change "85 tests" to whatever `python -m pytest tests -q` reports after Task 8.

- [ ] **Step 4: Update README.md**

- Header: `# IndonesiaSmoke`, and the opening paragraph names Indonesia rather than Kalimantan
- Domain section: replace with the new extent, the segment/download figures from Task 1, and the per-pixel gating explanation. Keep the western-edge caveat and extend it: at 94.5°E the viewing zenith is ~60°, and Papua sits under the sub-satellite point at ~0°, so the *range* of geometry is now the problem rather than one bad edge.
- Comparison table: the Coverage row for this product becomes `94.5-142E, 11.5S-8N`, and the Hours row becomes `daytime only, ~23:15-09:00 UTC`. BMKG's "all Indonesia" is no longer a point of difference — say so plainly rather than quietly dropping it.
- Known limits: add the three new ones verbatim from spec §7, and keep all four existing ones.

- [ ] **Step 5: Note it in PLAN.md**

At the top of `PLAN.md`, under the title:

```markdown
> **Superseded in part, 2026-08-25.** The domain and the sun-angle gate were
> reworked for all of Indonesia — see
> `docs/superpowers/specs/2026-08-25-indonesia-smoke-domain-design.md`. The
> scope decisions in §1 and the non-negotiables in §5 still stand.
```

- [ ] **Step 6: Commit**

```bash
git add README.md OPERATIONS.md PLAN.md
git commit -m "Document the Indonesia domain

The health check read scene_stats.smoke_fraction and a WIB clock, neither of
which exists now. Both checkpoints described a domain with one sun angle;
they are replaced by the two moments that matter on this one - the eastern
morning, and the widest point of the day.

The old reference values are kept but labelled as measured on the Kalimantan
domain against the old denominator, because the enrichment figures are still
the benchmark and the smoke percentages are no longer comparable."
```

---

### Task 10: Create the repository, run it, and prove it before cutover

**Files:**
- No files in this repository. This task creates `IndonesiaSmoke` and operates it.

**Interfaces:**
- Consumes: branch `indonesia-domain` complete and green.
- Produces: a live second site. **Cutover of the original site is explicitly NOT part of this task** — it happens only after the gate passes and the user says so.

- [ ] **Step 1: Merge the branch locally**

```bash
git checkout main
git merge --no-ff indonesia-domain -m "Merge the Indonesia domain work"
python -m pytest tests -q
```

Expected: PASS. **Do not push this to `origin`.** The live pipeline runs from `origin/main`; pushing would swap the live map's domain with no validation behind it. The merge is local, so that the new repository gets a linear history with the work on it.

- [ ] **Step 2: Create the repository and push**

```bash
gh repo create IndonesiaSmoke --public \
  --description "Live Indonesian wildfire smoke: Himawari-9 extent, FIRMS hotspots, 0-3 h advection"
git remote add indonesia https://github.com/achmadrpahlevi/IndonesiaSmoke.git
git push indonesia main
```

The full history goes with it, which is the point: OPERATIONS.md refers back to reasoning that only exists in commit messages.

- [ ] **Step 3: Configure the repository**

```bash
gh secret set FIRMS_MAP_KEY --repo achmadrpahlevi/IndonesiaSmoke
```

Paste the same key the original repo uses. Without it the hotspot layer falls back to cache and looks plausible while being wrong.

Then enable Pages on `gh-pages`. The branch does not exist until the first successful run, so this is done after Step 4.

- [ ] **Step 4: First run, publish suppressed**

```bash
gh workflow run pipeline.yml --repo achmadrpahlevi/IndonesiaSmoke -f skip_publish=true
gh run watch --repo achmadrpahlevi/IndonesiaSmoke
```

Check three things in the log before letting it publish anything:

1. `segments_for_bbox` fetched five segments, 35 files. If it fetched four, `AHI_FALLBACK_SEGMENTS` was used, which means pyproj failed — investigate rather than accepting it.
2. The job finished inside `timeout-minutes: 25`. Expect roughly 3 minutes. If it is near the limit, say so before proceeding.
3. No shape errors. A fresh repository has an empty cache, so there should be none.

- [ ] **Step 5: Verify the western edge, which is the one guessed number**

`RESAMPLE_RADIUS_M = 8000` was calculated, not measured. Check it:

```bash
python -c "
import numpy as np
from pipeline import common, config as C
from pipeline.fetch_ahi import load_scene_npz
p = sorted(__import__('pathlib').Path(C.STATE_DIR).glob('scene_*.npz'))[-1]
g = load_scene_npz(p)
lons = common.grid_lons()
west = lons < 97.0
b03 = g['B03']
print('NaN west of 97E: %.1f%%' % (100 * np.isnan(b03[:, west]).mean()))
print('NaN whole grid:  %.1f%%' % (100 * np.isnan(b03).mean()))
"
```

A western NaN fraction close to the whole-grid figure means 8000 is enough. If the west is several points worse, raise `RESAMPLE_RADIUS_M` to 10000 and re-run, and record the measured numbers in the README's Domain section rather than leaving the constant unexplained.

- [ ] **Step 6: Let it publish and enable Pages**

```bash
gh workflow run pipeline.yml --repo achmadrpahlevi/IndonesiaSmoke
gh run watch --repo achmadrpahlevi/IndonesiaSmoke
```

Then enable Pages on the `gh-pages` branch, root, and open the site. Check by eye: the UTC header and its three-zone line, the two hatches visibly different from each other, the toggles, the city labels across all three zones, and the opening view containing the whole country.

- [ ] **Step 7: The acceptance gate**

Over at least one full day of runs, score each region:

```bash
python -m pipeline.validate --date <YYYYMMDD_HHMM>
```

Requirement from the spec: **FIRMS hotspot enrichment above 3× in Sumatra, Kalimantan, Sulawesi and Papua.** Enrichment near 1× means the map is no better than chance there.

Record the numbers. If a region fails, that is a finding, not a blocker to work around — report which region, at what sun angle and viewing geometry, and stop. Papua under the sub-satellite point and the Banda Sea glint case are the two most likely to fail, and both were called out in the spec as untested.

- [ ] **Step 8: Report, and stop**

Report to the user: the four enrichment figures, the measured western NaN fraction, the job runtime, the published page size, and the FIRMS point count at `FIRMS_MIN_FRP_MW = 50`.

**Do not cut over.** Replacing `KalimantanWildfires`'s `index.html` with a redirect and disabling its schedule is a separate, explicitly authorised step. Two maps running in parallel costs nothing but a little Actions time, and that is the entire point of having built it this way.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 domain, grid, segments, resample radius | 1 |
| §1 CITIES | 6 |
| §1 validate.REGIONS | 6 |
| §1 view_bounds deletion | 1 |
| §2 calibrated_mask, MIN_CALIBRATED_FRACTION | 2 |
| §2 uncalibrated folded into obscured, own layer | 3, 5 |
| §2 smoke_fraction_of_visible | 3 |
| §2 footprint erosion before flow | 4 |
| §3 UTC display | 6 |
| §3 index.html, three-zone line | 7 |
| §3 CAVEAT_LOW_SUN deleted | 5 |
| §4 repo, env prefixes, bot, cron, secret, Pages | 8, 10 |
| §5 FIRMS density measured not pre-empted | 10 step 8 |
| §6 tests updated not deleted | 1, 2, 3 |
| §6 acceptance gate | 10 step 7 |
| §7 new known limits | 9 |

No gaps.

**Type consistency:** `calibrated_mask` returns `np.ndarray[bool]` at grid shape in Tasks 2, 3 and 4. `calibrated_fraction` returns `float` in Tasks 2 and 5. `hatch_png` has one signature, used twice in Task 5 and once in the Task 5 test. `mask_shape_ok` returns `bool`, used in Task 1 in two call sites. `smoke_fraction_of_visible` is introduced in Task 3 and every reader is updated in the same task.

**Known soft spot:** Task 3's `PATCH` may land outside the calibrated window on the new grid, which is why Step 1 carries an explicit replacement rather than leaving it to be discovered. Task 1 Step 6's `view_zenith` test may pass or fail depending on how it is written, which is why that step says to run it first and gives both branches.
