# Operations — checking IndonesiaSmoke

Read this before diagnosing anything. Most "it's broken" cases are documented
expected behaviour, and several plausible-looking bugs here were already found
and fixed the hard way.

**Not deployed yet.** This describes the pipeline on the `indonesia-domain`
branch of `KalimantanWildfires` — code-complete, not yet live anywhere. The
migration plan (`docs/superpowers/specs/2026-08-25-indonesia-smoke-domain-design.md`
§4) is a **new** repository, `IndonesiaSmoke`, running in parallel until an
acceptance gate (FIRMS enrichment above 3x over Sumatra, Kalimantan, Sulawesi
and Papua) passes; verified 2026-08-25 that repo does not exist yet
(`gh repo view achmadrpahlevi/IndonesiaSmoke` 404s) and `main` on this repo is
still the unmodified old Kalimantan-domain code (`LON_MIN, LON_MAX = 100.0,
120.0`). `KalimantanWildfires` keeps serving that old product, untouched,
until cutover is called. The URLs, log grep and health check below are what
apply once `IndonesiaSmoke` exists — substitute its real address.

**Live (once cut over):** <https://achmadrpahlevi.github.io/IndonesiaSmoke/>
**Repo (once created):** <https://github.com/achmadrpahlevi/IndonesiaSmoke>
**Old product, unaffected until cutover:** <https://achmadrpahlevi.github.io/KalimantanWildfires/>
**Schedule:** every 30 min at **:17 and :47**, not `*/30` — and not this
repo's own `:07`/`:37` either. GitHub delays or silently drops scheduled runs
under load, and load peaks at the top of the hour. Measured on 2026-08-22
with `*/30`: 27–34 min gaps through quiet hours, then 52, 91 and 64 min once
the busy period began, with nothing queued — the runs were never created.
Odd minutes queue far shorter; that reasoning is unchanged and still applies.
The offset from `:07`/`:37` is new: `main` (the live `KalimantanWildfires`
product) already runs on `:07`/`:37`, and once `IndonesiaSmoke` is live too
the two would otherwise fetch FIRMS in the same minute on the same key —
`:17`/`:47` avoids that collision (see `.github/workflows/pipeline.yml`'s
cron comment). Some drift is still normal, which is why staleness is
displayed rather than assumed.

---

## 60-second health check

```bash
gh run list --workflow=pipeline.yml --limit 8
curl -s "https://achmadrpahlevi.github.io/IndonesiaSmoke/data/meta.json?cb=$(date +%s)" \
  | python -c "import sys,json;m=json.load(sys.stdin);s=m['scene_stats'];print(m['scene_utc'],'| smoke %.2f%% of visible'%(s['smoke_fraction_of_visible']*100),'| in window %.0f%%'%(s['calibrated_fraction']*100),'| frozen',m['frozen'],'|',len(m['layers']['forecast']),'steps')"
```

`scene_local`/`tz_label` still exist in `meta.json` but now just restate
`scene_utc` under `DISPLAY_TZ_LABEL = "UTC"` — read `scene_utc` directly
instead. Healthy looks like a scene timestamp within the last ~45 min during
the window, `frozen False`, and `6 steps`. `in window` is `calibrated_fraction`
— the share of the *whole domain* inside the 40° sun-angle range right now,
not the share that reads as smoke; it ranges from ~5% (the gate's own floor)
at the edges of the day up to 100% around 03:00-05:30 UTC. At 0% the page is
frozen with reason *"no part of the country is inside the calibrated
sun-angle window"* — the whole-country equivalent of night.

The page is self-diagnosing: the banner goes red past 90 minutes and the freeze
reason states *why* (night / sun too low / no recent scene). Read that before
opening logs.

---

## The two checkpoints

These describe one sun angle across one domain. The domain now has 47.5
degrees of longitude and no single sun angle describes it — different parts
of the country enter and leave the calibrated window at different UTC
instants, so the checkpoints below are two moments that matter for a
different reason each: the dawn edge, and the moment the country is most
inside the window at once. Both are measured directly from
`common.calibrated_fraction`, the function `MIN_CALIBRATED_FRACTION` gates
on, run on 2026-08-21/22 — not estimated.

### 00:00 UTC — Papua alone

07:00 WIB, 08:00 WITA, 09:00 WIT. `calibrated_fraction` measures **~19%**
here. That is *not* "the eastern third" the way you might guess from the
clock — measured longitude by longitude at this instant, the calibrated
cutoff sits around **133-134°E**, deep into Papua/western Maluku. Sulawesi
(elev ~26-33° across it), Ambon (35°), even Manado at the top of Sulawesi
(33°) are all still hatched; only Papua and the Maluku islands closest to it
are in window. Expect the hatch to cover *most* of the visible map — Sumatra,
Java, Kalimantan, Sulawesi and most of Maluku — and expect that to look
alarming the first few times.

| expect | value |
|---|---|
| scene | within ~45 min, `frozen False` |
| in window (`calibrated_fraction`) | ~19% |
| hatch | everything except Papua and eastern Maluku |
| smoke | measured against visible area, not the whole grid |

### 04:00 UTC — the widest moment

11:00 WIB, 12:00 WITA, 13:00 WIT. Measured `calibrated_fraction` is
**exactly 1.0 (100%)** from about 02:50 to 05:30 UTC on 2026-08-21 — the only
stretch of the day where the entire country is inside the calibrated window
at once. This is the moment closest to the old product's single-domain
midday, and it is not a range like the brief first estimated ("45-60%") — it
was measured directly and it is the country's actual maximum, not a rough
band around it.

| expect | value |
|---|---|
| in window (`calibrated_fraction`) | the day's maximum, ~100% (02:50-05:30 UTC) |
| forecast | 6 steps |
| hatch | little to none, domain-wide |

Numbers to be suspicious of, unchanged in spirit: detection over the Malacca
Strait and Peninsular Malaysia at once has meant a broken water test every
time it has appeared. New to this domain: detection over the Banda and
Arafura seas — both inside the domain and both well within window by this
hour — is where sun glint near the sub-satellite point is expected to bite
and has never been checked (see "Known limits, deliberately not fixed").

If the map is stale, check the run log for `no usable pair of masks` — see
*Fixed already* below, because that exact failure has occurred and its fix is
in place.

---

## Diagnosing

```bash
gh run list --workflow=pipeline.yml --limit 10          # any failures?
gh run view <id> --log-failed | tail -30                # why
gh run view <id> --log | grep "indosmoke |"             # pipeline's own account
```

The pipeline narrates itself. Useful lines:

| log line | meaning |
|---|---|
| `domain is dark ... skipping the fetch` | night, correct, no download |
| `publishing frozen: <reason>` | withheld and labelled, usually correct |
| `no usable pair of masks` | no forecast this run; publishes without slider |
| `kept N cached hotspots` | FIRMS unreachable, fell back — correct |
| `flow implies X m/s, over the limit — discarding` | motion rejected, field frozen |

### Is the map actually right?

```bash
python -m pipeline.validate --date <YYYYMMDD_HHMM>
```

Scores the mask against FIRMS hotspots, which share no physics with it. It
counts **only fires acquired at or before the scene**, because scoring against
the full 24 h list charges a morning scene for fires that had not started.

Reference values measured 2026-08-21/22 **on the Kalimantan domain**, before
the smoke fraction was re-based on visible area. Kept because the enrichment
figures are still the benchmark — they share no physics with the mask — but
the smoke percentages are not comparable to what the current product reports
(`smoke_fraction_of_visible` is measured against visible area, not the whole
grid, and the grid itself is now 3.6× larger):

| scene | smoke (old denominator) | enrichment |
|---|---|---|
| 08:50 WIB | 7.73% | 6.0× |
| 14:00 WIB | 5.19% | 4.2× |

**Enrichment near 1× means the map is no better than chance** and something is
wrong. Above ~3× is healthy — that part of the benchmark still holds.

---

## Expected behaviour that looks like breakage

- **Frozen all evening and night.** Correct. Mask needs visible bands.
- **Banner red on an old scene.** Correct and deliberate — a confident stale
  map is worse than an honest one.
- **Hotspots marked stale.** FIRMS was unreachable; the cached layer is shown.
  Degrades on its own.
- **No slider right after dawn.** One frame, no flow. Resolves next cycle.
- **Cron gaps.** Some drift is normal. Repeated gaps over ~60 min mean GitHub
  is dropping runs; check the cron is still on odd minutes (`17,47`), not
  `*/30`. Nothing will appear queued — dropped runs are never created.
- **North Kalimantan smoke.** Measured as genuine (B05 ≈ 16.8, warm at 290 K),
  and BMKG's own analysis names Kalimantan Utara.
- **Most of the map hatched.** Correct, and the single biggest change from
  the Kalimantan product. Across 47 degrees of longitude only part of the
  country is ever inside the calibrated sun-angle window at once, and the
  rest is hatched rather than guessed at. At the extremes of the day the
  hatched share is most of the map.

## Known limits, deliberately not fixed

- **Shallow coastal water** — Java Sea shelf east of Lampung, Sunda and
  Karimata Straits — is spectrally indistinguishable from thin smoke in these
  seven bands. Those pixels pass *both* branches; no threshold separates them.
  The page reports what share of detection is over water instead. Fixing it
  needs bathymetry or temporal persistence (sediment does not move, smoke does).
- **West Kalimantan extent** reads high (≈50% of unobscured land at 14:00).
  Some is real given the fire count; the fraction has never been verified.
- **Sun-angle drift inside the window** (1.9% at noon → 5.2% at 14:00). Partly
  real afternoon fires, partly residual aerosol path radiance.
- **Wind arrows** inactive — needs `pip install cfgrib eccodes`.

New to the Indonesia-wide domain, from `docs/superpowers/specs/2026-08-25-indonesia-smoke-domain-design.md`
§7:

- **Scattering geometry varies far more than before.** Papua sits at the
  sub-satellite point (viewing zenith ~0°), Borneo at ~26-31°, Sabang at
  **53°, measured** (an earlier estimate of ~60° was wrong — see
  `README.md`'s "Domain"). Every threshold in `config.py` was tuned at
  Borneo's geometry.
- **Sun glint near the sub-satellite point.** The morning water-test fix
  (`WATER_SMOKE_B03_MIN = 14`) was derived for Borneo morning geometry, where
  sun and sensor share a side at ~26-31° off nadir. It has never been tested
  over the Banda and Arafura seas with the satellite almost directly
  overhead. Expect this to need work — it is what to be most suspicious of
  during the 04:00 UTC checkpoint above.
- **Java, Bali and Nusa Tenggara.** Dry-season bare soil and volcanic terrain
  are false-positive sources never tested by this pipeline.

## Fixed already — do not re-diagnose

Each of these cost real time; the fix and a regression test are in place.

| symptom | cause |
|---|---|
| smoke everywhere, ~30%, Sumatra + Malaysia orange | water test read "bright blue, dark red" = clear tropical water. Fixed by requiring red band ≥ 14 |
| mask shrinks every afternoon | no solar-zenith correction |
| smoke explodes at low sun (54%) | Rayleigh path term; B03 carries 175× B06's optical depth |
| hotspots vanish 07:00–13:00 WIB daily | FIRMS `day_range` counts UTC days *including today*; fetch 2 days, filter to 24 h |
| dawn run fails, `no usable pair` | pruning deleted the flow partner it had just fetched — keepable (12°) and publishable (40°) are different questions |
| threshold change has no effect | cached masks skipped; workflow now passes `--force` |
| map blank / stale after dusk | publish picked newest mask, not newest *publishable* one |
| coastal streaks called smoke | mixed land/water pixels at 3–4 km; `WATER_B05_MAX` raised 5 → 8 |

## Safety valves

Single constants in `pipeline/config.py`, no code change needed:

| constant | effect |
|---|---|
| `MIN_SCENE_LOCAL_HOUR` | raise to withhold early scenes (0.0 = off) |
| `MIN_SCENE_ELEVATION_DEG` | 40.0; raise to shorten the day, now per pixel |
| `MIN_CALIBRATED_FRACTION` | 0.05; raise to shorten the day at both ends |
| `FIRMS_MIN_FRP_MW` | 50.0; hotspot density |
| `WATER_NOTE_ABOVE_FRACTION` | 0.10; when the water note appears |

After changing any of them, run `python -m pytest tests -q` (104 tests) — several
pin the reasoning above and will fail loudly if a fix is undone.

## Housekeeping

GitHub disables scheduled workflows after **60 days of repository inactivity**.
Any commit resets the clock. If this stops silently in a couple of months, that
is the first thing to check.
