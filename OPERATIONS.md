# Operations — checking KalimSmoke

Read this before diagnosing anything. Most "it's broken" cases are documented
expected behaviour, and several plausible-looking bugs here were already found
and fixed the hard way.

**Live:** <https://achmadrpahlevi.github.io/KalimantanWildfires/>
**Repo:** <https://github.com/achmadrpahlevi/KalimantanWildfires>
**Schedule:** every 30 min at **:07 and :37**, not `*/30`. GitHub delays or
silently drops scheduled runs under load, and load peaks at the top of the
hour. Measured on 2026-08-22 with `*/30`: 27–34 min gaps through quiet hours,
then 52, 91 and 64 min once the busy period began, with nothing queued — the
runs were never created. Odd minutes queue far shorter. Some drift is still
normal, which is why staleness is displayed rather than assumed.

---

## 60-second health check

```bash
gh run list --workflow=pipeline.yml --limit 8
curl -s "https://achmadrpahlevi.github.io/KalimantanWildfires/data/meta.json?cb=$(date +%s)" \
  | python -c "import sys,json;m=json.load(sys.stdin);print(m['scene_local'],m['tz_label'],'| smoke %.2f%%'%(m['scene_stats']['smoke_fraction']*100),'| frozen',m['frozen'],'|',len(m['layers']['forecast']),'steps')"
```

Healthy looks like a scene timestamp within the last ~45 min during the window,
`frozen False`, and `6 steps`.

The page is self-diagnosing: the banner goes red past 90 minutes and the freeze
reason states *why* (night / sun too low / no recent scene). Read that before
opening logs.

---

## The two checkpoints

### 15:00 WIB — last scene of the day

The window is **08:30–15:00 WIB**. 15:00 is the last publishable scene; its
forecast runs to 18:00 WIB.

| expect | value |
|---|---|
| scene | `15:00 WIB`, `frozen False` |
| smoke | roughly 5–11% of domain |
| forecast | 6 steps, last valid 18:00 WIB |
| low-sun caveat | **present** — 15:00 is ~40°, below the 50° caveat line |

**After ~15:30** it freezes on the 15:00 scene with
*"sun too low over the domain"*. That is correct, not a failure. The banner
will redden as the evening goes on; that is the design.

Numbers to be suspicious of: **smoke above ~15%**, or the map painting Sumatra,
the Malacca Strait and Peninsular Malaysia at once. That combination has meant
a broken water test every time it has appeared.

### 08:30 WIB — dawn, the least-proven moment

This is the one path never exercised without intervention. Every morning run on
2026-08-22 happened with a human triggering it after a fix.

What should happen:

1. **08:30** — first run in the window. State holds yesterday's scenes, so
   `--ensure-pair` fetches **two** scenes (~160 s instead of ~80 s). Publishes
   the smoke field, and may show **no forecast** — one frame cannot give flow.
2. **09:00** — two daylight masks exist → first forecast, slider appears.

So *no slider at 08:30 is expected*; no slider at **09:30** is a problem.

| expect | value |
|---|---|
| scene | today's date, 08:30–09:00 WIB |
| smoke | roughly 5–10% |
| over-water note | may appear (~17% seen at 08:50) |

If the map is stale from yesterday, check the run log for
`no usable pair of masks` — see *Fixed already* below, because that exact
failure has occurred and its fix is in place.

---

## Diagnosing

```bash
gh run list --workflow=pipeline.yml --limit 10          # any failures?
gh run view <id> --log-failed | tail -30                # why
gh run view <id> --log | grep "kalimsmoke |"            # pipeline's own account
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

Reference values measured 2026-08-21/22:

| scene | smoke | enrichment |
|---|---|---|
| 08:50 WIB | 7.73% | 6.0× |
| 14:00 WIB | 5.19% | 4.2× |

**Enrichment near 1× means the map is no better than chance** and something is
wrong. Above ~3× is healthy.

---

## Expected behaviour that looks like breakage

- **Frozen all evening and night.** Correct. Mask needs visible bands.
- **Banner red on an old scene.** Correct and deliberate — a confident stale
  map is worse than an honest one.
- **Hotspots marked stale.** FIRMS was unreachable; the cached layer is shown.
  Degrades on its own.
- **No slider right after dawn.** One frame, no flow. Resolves next cycle.
- **Cron gaps.** Some drift is normal. Repeated gaps over ~60 min mean GitHub
  is dropping runs; check the cron is still on odd minutes (`7,37`), not
  `*/30`. Nothing will appear queued — dropped runs are never created.
- **North Kalimantan smoke.** Measured as genuine (B05 ≈ 16.8, warm at 290 K),
  and BMKG's own analysis names Kalimantan Utara.

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
| `MIN_SCENE_ELEVATION_DEG` | 40.0; raise to shorten the day |
| `FIRMS_MIN_FRP_MW` | 50.0; hotspot density |
| `WATER_NOTE_ABOVE_FRACTION` | 0.10; when the water note appears |

After changing any of them, run `python -m pytest tests -q` (85 tests) — several
pin the reasoning above and will fail loudly if a fix is undone.

## Housekeeping

GitHub disables scheduled workflows after **60 days of repository inactivity**.
Any commit resets the clock. If this stops silently in a couple of months, that
is the first thing to check.
