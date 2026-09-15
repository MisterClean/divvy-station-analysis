# Station reference cleanup — September 15, 2026

## Result

The initial reference overstated the number of distinct station/location candidates.
After correcting how coarse trip coordinates are used, the Chicago reference has
**2,261 candidates, down from 3,389 (33.3%)**. All original observation groups and
trip counts are preserved. This is a cleaner historical reference; its row count
is still not a certified count of physical station installations.

| Chicago reference measure | Before | After |
|---|---:|---:|
| Located public candidates | 3,389 | 2,261 |
| Candidates named as stations | 1,967 | 1,394 |
| Candidates named as public racks | 1,422 | 867 |
| Candidates with fewer than 10 valid trip endpoints | 569 | 89 |

**Use the [cleaned reference](../data/processed/stations.csv).** See the
[before/after accounting](cleanup_comparison.json) and
[record-level trace](cleanup_changes.csv) for evidence. This assessment uses trips
through August 2026 and station feeds retrieved September 15, 2026. No ward
assignment or scorecard deployment count was produced.

## What inflated the original list

### High: rounded trip coordinates became separate station locations

**1,122 original rows had both latitude and longitude exactly on a two-decimal
grid.** At Chicago's latitude, a 0.01-degree cell spans roughly 1.1 km north–south
and 0.8 km east–west. Treating its centre as an exact dock location is incompatible
with the pipeline's 150 m station-matching radius. Even a single rounded trip
could create a new entity beside a well-supported station with the same name.

The scan finds **287,091 valid named endpoints** with this coarse precision,
spread across 13,544 source-file/name/ID/endpoint groups. The correction:

- Retains those trips, first-event timestamps and source IDs.
- Computes precise coordinate medians only from valid trips with finer positions.
- Uses the whole rounding cell, plus the existing 150 m tolerance, when attaching
  a coarse observation to an unambiguous same-name/ID location.
- Retains ambiguous observations as unlocated. It does not force a nearest match.
- Allows an explicitly flagged current-feed coordinate only for a coarse-only
  name with a unique exact name match inside the same spatial allowance. No rows
  in this snapshot ultimately require this fallback.

For example, **Public Rack - Emerald Ave & 45th St** originally appeared three
times. It now has one candidate at 41.812381, −87.644633, retains all 112 qualifying
endpoints, and keeps its October 6, 2022 first event. **Oketo Ave & Addison St**
also had coarse-coordinate splits; its resolved history still starts August 25,
2021. The raw first events are independently checked in
[source_spot_checks.json](source_spot_checks.json).

The net reduction of 1,128 rows is not a claim that 1,128 installations never
existed. It combines identity consolidation with removal of unsupported precise
locations from the Chicago export. **401 system-wide candidates remain
unlocated**, including historical ambiguities; they are retained in
[stations_unlocated.csv](../data/processed/stations_unlocated.csv).

### High: a “station” name does not establish station hardware

The original table already included public racks. It also used a name-based
station/rack classification. Among the cleaned Chicago rows matched to the
operator feed, **1,121 have operator type `classic` and 883 have type
`lightweight`**. Of those lightweight locations, 27 do not have “Public Rack” in
their reference name.

The reference therefore preserves both the historical name-based `station_type`
and the current `gbfs_station_type`. Neither today's name nor today's hardware
category certifies when conventional docks were first installed. Rack conversions
remain separate identity/date questions. An explicit private-rack name is also
now classified as operational rather than public.

## Reconciliation with current inventories

The [city inventory](https://data.cityofchicago.org/Transportation/Divvy-Bicycle-Stations/bbyy-e7gq/about_data)
describes all stations in its current list, including temporarily unavailable
ones. Its September 15 snapshot contains **1,177 In Service, 27 Not In Service,
and one Not Installed** record. It includes 28 locations outside Chicago.
“All stations” does not establish that every removed historical location remains
in the file.

Divvy's [system-data page](https://divvybikes.com/system-data) also links the
[operator GBFS feed](https://gbfs.divvybikes.com/gbfs/gbfs.json), which supplies many
additional public racks. It is useful independent corroboration of location and
current classification.

| Source | All feed rows | Inside Chicago | History matches, all locations | History matches inside Chicago |
|---|---:|---:|---:|---:|
| City inventory | 1,205 | 1,177 | 1,196 | 1,168 |
| Operator inventory | 2,053 | 2,014 | 2,043 | 2,004 |

Across the cleaned Chicago reference, **2,004 candidates match at least one
current inventory**. Another 54 have exact-name or short-name links that need
review, and 203 have no confirmed match. These groups may include removed
locations, older names, relocations or unresolved identities. None is labelled
retired solely because it failed to match today's feed.

Accepted links require an exact normalized name within 150 m, or a shared short
name within 35 m, with compatible name-based station/rack classification.
Name-plus-short-name agreement receives priority. Links must be unambiguous and
one-to-one within each feed. Short names remain text. Fuzzy-name and proximity
evidence produces review suggestions only; it never silently merges records.

The nine unmatched city records are explainable and individually inspectable:

- **Four current stations lack an unambiguous trip-history match through August:**
  Damen Ave & Rodgers St, Glenwood Ave & Ardmore Ave, Leamington Ave & 63rd Pl,
  and Ravenswood Ave & Foster Ave. A September feed can legitimately be newer than
  the latest published trip month. Their opening dates remain unknown.
- **Damen Ave & Ogden Ave** is marked Not Installed.
- **Hubbard St Depot Public Rack** and **Hastings St Depot Public Rack** are depot
  records, excluded from public station matching.
- **Canal St & Monroe St** and **Eberhart Ave & 91st St** each have competing
  historical location candidates. Their feed links remain unresolved.

The operator feed has one additional unmatched Chicago record:
**Public Rack - Lawndale Ave & 111th St**, with service restricted in the snapshot.

See [every feed row and its match](../data/processed/current_feed_coverage.csv),
[all candidate matching evidence](../data/processed/current_feed_candidates.csv),
and [feed hashes and retrieval times](../data/reference/station_feeds.json).
`city_status` and `gbfs_status` describe their respective snapshot times; the feeds
were not observed atomically and need not agree in every operational detail.

## Service dates: what is usable and what remains unresolved

**First recorded trip is evidence of service, not an official installation date.**
It is generally an upper bound on first service for a correctly identified
location. It cannot date a later equipment upgrade. Unpublished trips, uncertain
identity, relocations and the start of available archive history limit that
interpretation.

The main artifact now includes:

- `opening_date_status=unverified_first_trip_proxy` on every row.
- `earlier_same_name_trip_at`, exposing earlier history under the same normalized
  name or accepted alias without merging distinct locations.
- `service_date_review_reasons`, exposing specific identity, location and evidence
  concerns. Blank reasons do not certify a date.

**52 Chicago candidates have earlier same-name history; 17 first appear at their
candidate location in 2023 or later but have pre-2023 name history.** For example,
the later Canal St & Monroe St candidate first appears August 1, 2024, while the
name's history begins August 7, 2013. Counting the later row as a new station
deployment would require additional evidence. Eight candidates also have earlier
same-name evidence in an unlocated entity. Review these with the
[service-date review export](service_date_review.csv), which contains 384 rows
with specific review reasons.

The nine historical station catalogs provide online dates for 605 Chicago
candidates, but 529 have conflicting reported calendar dates. Those dates remain
separate from first trip. The current city and operator station inventories have
no historical in-service-date field in the retrieved responses.

I also checked the city's [historical station-status dataset](https://data.cityofchicago.org/Transportation/Divvy-Bicycle-Stations-Historical/eq45-8inv).
It is explicitly historical-only; the latest returned observation is
**September 10, 2022, 18:25:43**. Its metadata also documents a gap between July
and December 2019. It can help corroborate older service, but cannot directly
establish 2023–present openings. The metadata and latest-timestamp query are
recorded in [historical_feed_check.json](historical_feed_check.json); this cleanup
did not scan that entire status-history table.

The next service-date work should resolve the flagged relocations and rack
conversions, establish the desired installation unit, and obtain dated
installation/commissioning evidence for 2023–present. The current cleanup supplies
the reference and review trail needed for that work.

## Validation and reproduction

- Reprofiled all 57,439,268 trips with the revised coordinate rules, using strict
  parsing and a new cache version.
- Preserved all 195,101 mapped observation groups and all 103,282,528 qualifying
  mapped endpoints in the before/after join. Every original Chicago candidate is
  traced to its new disposition. The full pipeline also reconciles all
  114,878,536 raw endpoints, including excluded records.
- All 16 full-data checks passed, including unique station keys, exact first-date
  minima, one-to-one feed links and complete feed accounting.
- Regression tests cover coarse GPS/date preservation, ambiguous locations,
  fuzzy suggestions, ID reuse, rack conversions and pinned-feed integrity.
- The complete offline pipeline and independent raw-ZIP first-event spot checks
  pass. See [reproduction evidence](reproduction.json).

Normal rebuilding remains `uv run --locked python pipeline.py --offline` from
the repository root. Refresh current inventories only with
`uv run --locked python station_feeds.py --refresh`, then rebuild. Trip archive
refresh and station-feed refresh are separate choices.

The before/after audit compares against baseline commit
`9b9f03c9527454486d1c49bbcb297b12fad57181`, whose station CSV SHA-256 is recorded in
`cleanup_comparison.json`. To regenerate that one-time comparison, reproduce the
baseline in a separate checkout, copy its `stations.csv` and
`station_observations.csv` to this checkout's `data/interim/` as
`baseline_stations.csv` and `baseline_station_observations.csv`, then run
`uv run --locked python audit_cleanup.py`. The exact observation-grain join is in
[cleanup_comparison.sql](../sql/cleanup_comparison.sql).
