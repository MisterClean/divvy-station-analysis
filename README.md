# Divvy station analysis

Estimate the first recorded service of Chicago's Divvy stations from the complete
[official trip archive](https://divvy-tripdata.s3.amazonaws.com/index.html), with a
reproducible Python and DuckDB pipeline.

**[Download the station reference CSV](data/processed/stations.csv)** ·
**[Download current city stations with history](data/processed/current_stations.csv)** ·
[Profiling report](reports/profile.md) · [SQL query](sql/first_trip.sql) ·
[Validation results](reports/validation.json)

**[Station cleanup findings](reports/station_cleanup.md)** ·
[Current-feed coverage](data/processed/current_feed_coverage.csv) ·
[Unresolved matching candidates](data/processed/current_feed_candidates.csv)

The table includes `station_id`, `station_name`, `station_lon`, `station_lat` and
`station_first_trip_at`, plus identity, provenance and quality fields. Use
`station_key` as the entity key: Divvy has changed and reused source IDs.
First trip means the earliest qualifying **departure or arrival**, not a
certified opening date. Public racks and historical locations are included.
Uncertain identities and relocations can remain separate candidates.
`opening_date_status`, `earlier_same_name_trip_at` and
`service_date_review_reasons` make the limitations explicit for future deployment
counts. Current feed fields describe the pinned snapshot, not historical status.

## Reproduce the analysis

### Current stations artifact

`data/processed/current_stations.csv` is the city inventory left-joined to the
published Chicago reference (`stations.csv`). It preserves **every city row and
all source columns**, including status, docks, coordinates, `location`, and the
computed-region columns. Missing source values remain blank. The `location` cell
contains valid GeoJSON serialized as JSON text.

Columns named `:@computed_region_*` are Socrata's automatically generated
geographic lookup fields. They refer to historical/current ward polygons, ZIP
areas, community areas, and census tracts. Their values are polygon feature IDs,
not necessarily ward numbers or ZIP codes. `city_column_labels` and
`city_column_populated_rows` in the reconciliation report describe each field.
The downloader explicitly selects these fields: the city's ordered API query
otherwise omits them even though they appear in its schema. A blank geographic
field can still mean that the source has no matching polygon for that location.

It adds these fields:

| Added column | Meaning |
|---|---|
| `reference_matched` | `True` only when an accepted city-ID link resolves to a row in `stations.csv`; otherwise `False` |
| `station_key` | The joined reference key |
| `station_first_trip_at` | Earliest qualifying trip event from the joined reference |
| `source_ids` | Historical source station IDs, separated by ` \| ` |
| `name_variants` | Historical name variants, separated by ` \| ` |
| `first_trip_archive` | ZIP filename containing the first-trip evidence |
| `opening_date_status` | Whether the first-trip date has been verified as an opening date |
| `earlier_same_name_trip_at` | Earlier same-name history that may indicate a move or identity issue |
| `service_date_review_reasons` | Known reasons to review the date before using it for deployment counts |

All reference fields are blank for unmatched rows. “Current” means present in the
pinned city inventory, regardless of status; use the original `status` column to
select stations in service. Rows outside Chicago remain in the inventory but do
not match the Chicago-only published reference. The first-trip date remains a
service proxy, not a certified installation date.

The reconciliation stage generates this CSV and a `current_stations` DuckDB table
on every pipeline run. Its counts, source snapshot timestamps and validation are
recorded in [feed_reconciliation.json](reports/feed_reconciliation.json).

### Run the pipeline

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git, then:

```bash
git clone https://github.com/MisterClean/divvy-station-analysis.git
cd divvy-station-analysis
uv run --locked python pipeline.py
```

That single command installs the locked dependencies and runs every stage:

1. Retrieve the ZIPs in the checked-in manifest, with eight parallel workers.
2. Check byte counts, S3 ETags where applicable, ZIP CRCs and pinned SHA-256 hashes.
3. Profile all trip CSVs and station CSV/XLSX catalogs with strict parsing.
4. Resolve station identities and geographic locations; export the reference CSV.
5. Reconcile identities with pinned city and operator station feeds, retaining
   unmatched historical candidates and unresolved links for review.
6. Build a local DuckDB database, reconcile every endpoint, and verify selected
   first-trip events directly against the original ZIP contents.
7. Regenerate the profiling report and its embedded copy in this README.

The default reproduces the checked-in source snapshot. If an upstream file has
changed, verification fails instead of silently building from different data.
The snapshot records source metadata and hashes; long-term availability of the
upstream archives is outside this project's control.

To discover and process the latest published ZIPs:

```bash
uv run --locked python pipeline.py --refresh
```

`--refresh` replaces the archive manifest only after all downloads succeed. It
updates the data artifact, reports and README for review before committing them.
The geographic boundary remains pinned to keep city filtering comparable.
Station feeds also remain pinned. Refresh them explicitly, then rebuild:

```bash
uv run --locked python station_feeds.py --refresh
uv run --locked python pipeline.py --offline
```

The feed downloader validates complete, unique inventories and matching operator
information/status IDs before publishing its manifest. Raw JSON responses are
content-addressed with SHA-256 hashes. Default and offline runs verify those
snapshots without contacting the live feeds. Keep previous feed snapshots if
historical status comparisons are needed; an absent record alone is not proof of
retirement.

To rebuild from already-downloaded ZIPs with no pipeline network requests:

```bash
uv run --locked python pipeline.py --offline
```

Offline runs require the Python environment to be installed already; `uv` itself
may otherwise need network access to install dependencies. Set download
concurrency with `--workers 4` (default: eight).

### Requirements and performance

- Python 3.12 or newer; CI checks 3.12 and 3.13. `uv` can install Python if needed.
- Allow at least 6 GB of free disk space for the current source snapshot, temporary
  extraction and outputs. Raw ZIPs currently occupy about 1.9 GB.
- DuckDB uses four threads and up to 6 GB of memory for each table scan. An 8 GB
  machine is a reasonable starting point; more memory can help.
- The initial full download took about 10½ minutes on the development machine.
  Full table profiling took about 101 seconds of accumulated processing time.
  Network and hardware determine your timings.

Verified existing ZIPs are reused. Interrupted individual downloads restart;
completed files are retained. Extracted trip tables are temporary. Per-member
summaries are cached by archive ETag, member name, member CRC and
`PROFILE_VERSION`, so subsequent profiling runs are fast. Increment
`PROFILE_VERSION` after changing normalization or summary logic.

## Project layout

| Path | Purpose | Published in Git |
|---|---|---|
| `pipeline.py` | One-command retrieval, build and validation | Yes |
| `download.py` | Parallel downloads and integrity checks | Yes |
| `profile_data.py` | Full-row schema and quality profiling | Yes |
| `build_stations.py` | Identity resolution and reference exports | Yes |
| `station_feeds.py`, `reconcile_stations.py` | Pin and compare city/operator inventories | Yes |
| `audit_cleanup.py`, `sql/cleanup_comparison.sql` | One-time before/after observation audit | Yes |
| `report.py`, `readme.py` | Report generation and README embedding | Yes |
| `verify_sources.py` | Independent source spot checks | Yes |
| `data/archive_manifest.json` | Pinned source URLs and hashes | Yes |
| `data/reference/` | Pinned boundary, station feed snapshots and provenance | Yes |
| `data/processed/stations.csv` | Derived station reference artifact | Yes |
| `data/processed/current_stations.csv` | Complete city inventory enriched from the published reference | Yes |
| `reports/` | Profiling and validation evidence | Yes |
| `data/raw/`, `data/interim/` | Trip ZIPs and cached summaries | No |
| `data/stations.duckdb` | Queryable local analysis database | No |
| `current_feed_coverage.csv`, `current_feed_candidates.csv`, `stations_unlocated.csv` | Inspectable reconciliation and location review | Yes |
| Other files in `data/processed/` | Detailed catalogs and observations | No |

`.gitignore` publishes the station reference and compact review exports. The trip
archives, full station catalogs and large intermediate tables stay local.

The build also produces these local audit files:

- `stations_all.csv` and `stations_unlocated.csv`: other system locations and
  candidates whose city membership cannot be determined.
- `identity_review.csv` and `accepted_alias_merges.csv`: unresolved identity
  links and accepted name changes.
- `station_observations.csv` / `.parquet`: source-file, ID, name and endpoint
  summaries, including the first trip ID and source filename.
- `station_catalog.csv`: historical lookup coordinates and reported online dates.
- `excluded_observations.csv`: accounting for unassigned endpoints.
- `current_feed_coverage.csv`: every city/operator feed row, including unmatched
  stations and those outside Chicago, with snapshot status and any linked history.
- `current_feed_candidates.csv`: exact name, short name, fuzzy name and proximity
  evidence. `accepted` marks the unambiguous one-to-one matches; other rows are
  suggestions for review, not recommended merges.

## Query or run individual stages

### Reading the reference fields

| Field | Meaning |
|---|---|
| `station_key` | Reproducible candidate identity for these rules and snapshots; may change after cleanup |
| `station_first_trip_at` | Earliest qualifying arrival or departure assigned to this location candidate |
| `station_type` | Historical name-based category; a name without “Public Rack” does not prove conventional docks |
| `gbfs_station_type` | Operator's current `classic` or `lightweight` category, where confidently matched |
| `city_station_id`, `gbfs_station_id` | Separate current-feed identifiers; never substitute raw historical IDs |
| `city_status`, `gbfs_status` | Status at the pinned snapshot time, not an opening or closure date |
| `current_feed_match_status` | `matched`, `needs_review`, or `no_confirmed_match`; unmatched does not mean retired |
| `opening_date_status` | Currently `unverified_first_trip_proxy` for every row |
| `earlier_same_name_trip_at` | Earlier history under the same normalized name/alias, possibly at another or unknown location |
| `service_date_review_reasons` | Specific identity/location/evidence issues to resolve before using the date in deployment counts |
| `coarse_coordinate_endpoint_count` | Valid endpoints retained for identity/date evidence but excluded from precise GPS summaries |

Blank review reasons do not certify an opening date. A `lightweight` category
also cannot establish the date when a location acquired conventional docks.

### Queries and rebuilds

After running the pipeline:

```bash
uv run --locked python - <<'PY'
from pathlib import Path
import duckdb

with duckdb.connect('data/stations.duckdb', read_only=True) as connection:
    connection.sql(Path('sql/first_trip.sql').read_text()).show()
PY
```

The database contains `stations`, `stations_all`, `station_observations`,
`station_catalog`, `identity_review`, `accepted_alias_merges` and
`excluded_observations`, plus `current_feed_coverage` and
`current_feed_candidates`, and the city-based `current_stations` table. For matching-only changes, run:

```bash
uv run --locked python build_stations.py
uv run --locked python reconcile_stations.py
uv run --locked python report.py
uv run --locked python verify_sources.py
uv run --locked python readme.py
```

Run individual scripts from the repository root. `pipeline.py` selects its own
project directory and also works when invoked by absolute path. For exploratory
profiling during a download, `profile_data.py --available` accepts missing ZIPs;
normal builds reject incomplete profiles. `build_stations.py --allow-partial`
explicitly generates a partial snapshot and overwrites the local outputs.

## Development

```bash
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ty check
```

The tests and GitHub Actions CI use small fixtures and the checked-in station
artifact; they do not download trip archives. Full-data checks run as part of
`pipeline.py`. Reproduce the current artifact before changing matching rules,
then inspect how the dates, identity links and quality flags change.

## License and attribution

The original code and documentation are available under the [MIT license](LICENSE).
The station CSV is a derived research artifact accompanying this analysis.
The MIT license does not relicense the source data; see [data sources and
terms](DATA_LICENSE.md), including the [Divvy Data License Agreement](https://divvybikes.com/data-license-agreement).

Source data is provided by the City of Chicago and Lyft Bikes and Scooters, LLC.
The boundary comes from the City of Chicago Data Portal. This project is an
independent analysis and is not affiliated with or endorsed by those organizations.

## Full profiling report

The following section is generated from [reports/profile.md](reports/profile.md)
at the end of each successful pipeline run.

<!-- BEGIN GENERATED PROFILE -->

## Divvy archive profile and station reference

### Result

The complete archive scan produced **2,261 candidate public station/location entities within Chicago**, including **867 public racks**. Download the main table at [stations.csv](data/processed/stations.csv). It contains the requested station ID, name, longitude, latitude and first-trip timestamp, plus identity, provenance and quality fields.

This is a reproducible, conservative reference, not a certified opening-date register. A station may open before its first published trip. Name changes with weak evidence and moves over 150 metres can remain separate entities. **89 Chicago rows have fewer than 10 qualifying endpoints.** Use the quality flags and review table before treating the row count as a count of distinct physical installations.

### Current-feed comparison

Pinned feed snapshot: 2026-09-15. The city inventory includes stations outside Chicago and stations not currently in service. The operator feed additionally covers many public racks. These are current inventories, not complete lifetime station registers.

| Source | Feed rows | Inside Chicago | Inside Chicago with matched history |
|---|---:|---:|---:|
| city | 1,205 | 1,177 | 1,168 |
| gbfs | 2,053 | 2,014 | 2,004 |

See the [row-level coverage](data/processed/current_feed_coverage.csv), [matching evidence](data/processed/current_feed_candidates.csv) and [snapshot provenance and checks](feed_reconciliation.json). Current service status is separate from a historical opening date. `station_type` is a name-based historical classification; `gbfs_station_type` retains the operator's current category where matched.

The [current city stations artifact](data/processed/current_stations.csv) preserves all 1,205 city rows and every source column. It adds `reference_matched` and the requested first-trip/history fields, plus the reference key and date-review context. 1,168 rows join to the published Chicago reference; 37 remain unmatched with blank history fields. Unlike the broader system coverage above, this artifact only joins to records in `stations.csv`.

### Coverage and download

- Source: [official Divvy trip bucket](https://divvy-tripdata.s3.amazonaws.com/index.html).
- Snapshot retrieved: 2026-09-15T18:11:01.613695+00:00.
- 95 ZIPs, 1.881 GB compressed; eight download workers; 628.4 seconds for downloading and verification.
- Every ZIP passed byte-count, available single-part S3 ETag and ZIP CRC checks; a SHA-256 fingerprint was also recorded. The manifest records hashes and source URLs. Multipart ETags are not treated as MD5 checksums.
- 107 trip CSVs; 9 station catalogs (including the Excel catalog in 2014 Q1/Q2); 57,439,268 source trip rows.
- Trips start from 2013-06-27 01:06:00 through 2026-08-31 23:55:36.979000. Some arrivals extend beyond the final starting month.
- All 159 calendar months from 2013-06 through 2026-08 are represented. This checks monthly presence, not completeness of every day's operations.
- 5 distinct trip headers: historical station-from/to schemas, the verbose rental-details schema, and the modern ride schema introduced in April 2020. Modern timestamp precision also varies.
- Nested shapefile copies, README files, and macOS metadata are inventoried; the station CSV/XLSX catalogs supply the tabular station metadata. Nested shapefiles are not used as an additional station source.

| Start year | Trip rows |
|---|---:|
| 2013 | 759,788 |
| 2014 | 2,454,634 |
| 2015 | 3,183,439 |
| 2016 | 3,595,383 |
| 2017 | 3,829,014 |
| 2018 | 3,603,082 |
| 2019 | 3,818,004 |
| 2020 | 3,541,683 |
| 2021 | 5,595,063 |
| 2022 | 5,667,717 |
| 2023 | 5,719,877 |
| 2024 | 5,860,621 |
| 2025 | 5,552,964 |
| 2026 | 4,257,999 |

### Data quality: full scan

Counts below are source rows. Start and end issues can occur on the same trip, so do not sum them as a count of bad trips.

| Issue | Rows |
|---|---:|
| missing start id | 5,634,584 |
| missing end id | 5,940,602 |
| missing start name | 5,633,829 |
| missing end name | 5,940,000 |
| missing start coordinates | 21,243,344 |
| missing end coordinates | 21,281,727 |
| invalid start coordinates | 1 |
| invalid end coordinates | 79 |
| invalid start time | 0 |
| invalid end time | 0 |
| reversed trips | 11,471 |
| zero duration trips | 2,891 |
| duplicate or missing trip ids | 61 |

No CSV parse errors were silently skipped: parsing is strict and all columns are initially text. Station IDs stay text, including alphanumeric IDs and leading zeros. Missing IDs are not assigned from nearby GPS points. Unnamed observations are recovered only when the same ID has exactly one named identity in that archive; unresolved records are retained in the exclusion audit.

The duplicate-ID check is within each source file. The pipeline also detects byte-identical CSV/XLSX members by SHA-256. It does not globally deduplicate individual trips across files; counts are **source-record counts**, while duplicates do not change minimum dates. There are 0 byte-identical member copies excluded from aggregation.

Trips with end time before start time are excluded from first-date estimates; zero-duration trips are retained as published observations. Both arrival and departure events count. In **1,437 Chicago entities**, the first arrival precedes the first departure. Source timestamps are preserved as timezone-naive local clock readings; early README files call them CST, but the archives do not encode offsets. No UTC or daylight-saving interpretation is invented.

### Identity and coordinate rules

1. Normalize whitespace and case for matching; remove only trailing `(Temp)` and `(*)` display suffixes. Preserve direction, street numbers, rack prefixes and other meaningful name content.
2. Recover legacy coordinates from same-ID/name station catalogs whose coordinates agree within 150 m. A legacy-only ID match is also allowed when all catalog coordinates for that ID agree within 150 m; it is flagged.
3. Modern coordinates are per-file, per-name/ID/endpoint medians of valid trips. Points with both latitude and longitude on a two-decimal grid are retained as trip/date evidence but excluded from precise location summaries. Reject coordinate summaries whose 5th-to-95th percentile diagonal exceeds 300 m and points outside the Chicago-region sanity box (41.4–42.3 latitude, −88.1–−87.3 longitude).
4. When available, use a consistent same-file arrival median with at least three coordinates to locate the corresponding departure observations. Departure GPS can be widely scattered even when a station name is present.
5. Cluster exact normalized names around fixed anchors within 150 m. Fixed anchors prevent transitive chains from joining far-apart locations. Missing coordinates attach only to an unambiguous ID/name cluster, prioritizing the same archive. Coarse-only observations must agree with the rounding cell plus the same 150 m matching tolerance. Ambiguous groups remain unlocated. A name with no usable historical coordinates can use a unique exact current-feed name within that area; such inference is explicitly flagged.
6. Merge different names only when they share a source ID, have the same station/rack classification, and all representative locations in the merged group are within 35 m. 118 links were accepted; every link is saved in `accepted_alias_merges.csv`. This is a heuristic, not an official ID crosswalk.
7. Choose the latest observed named source ID and display name. `station_key` identifies the resolved entity; raw `station_id` is **not a unique historical key**. There are 2,217 distinct selected source IDs among 2,261 Chicago rows. Keys are deterministic for the same snapshot and rules but can change after future data or matching changes.
8. Choose coordinates from a well-supported arrival summary in the latest eligible month, falling back to other usable observations or catalog coordinates. Coordinates describe that representative observed location, not necessarily the exact location at the first trip.
9. Filter public locations using the [City of Chicago boundary](https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-City/qqq8-j68g). Operational/test/depot/private-rack names and IDs are retained in the all-entities table and excluded from the city reference. Public racks and temporary public stations remain included.
10. Reconcile against separately pinned city and operator feeds using name/short-name agreement and distance. Accept only unambiguous one-to-one links per source; fuzzy and proximity-only candidates stay in the review file. Current-feed absence does not establish retirement. Current-feed presence does not certify the station's historical opening date. All rows carry `opening_date_status=unverified_first_trip_proxy`; `earlier_same_name_trip_at` and `service_date_review_reasons` expose prior history and date risks without silently merging relocations or rack conversions.

Examples of accepted name histories:

| Current name | First recorded event | Historical source IDs |
|---|---|---|
| Field Museum | 2013-06-28 14:57:00 | 13029 , 97 , CHI01749 |
| Michigan Ave & Ida B Wells Dr | 2013-06-27 13:00:00 | 45 , CHI00250 , TA1305000010 |

### Online dates and remaining uncertainty

Historical catalogs contain `online date` / `online_date` and, separately, `dateCreated`. The 2013 README describes online date as when a station went live, but later catalogs disagree in some cases. The output therefore retains the earliest and latest reported online dates separately from first trip, and keeps creation dates in the catalog audit. **605 Chicago entities have a reported online date; 529 have conflicting calendar dates across snapshots.** Catalog records without an associated trip remain in `station_catalog.csv`; no first-trip date is invented for them.

The result retains 2,748 entities across the entire system; 401 cannot be placed reliably and are also exported to `stations_unlocated.csv`. There are 54 entities outside the current city boundary and 39 operational entities. These categories can overlap. Missing coordinates prevent a definitive city/suburb assignment, so those entities are absent from `stations.csv`.

Potential identity links still needing review:

| Reason | Pairs |
|---|---:|
| possible_rename_or_rack_conversion | 96 |
| reused_id_or_relocation | 538 |
| shared_id_location_unknown | 629 |

These pairs are diagnostics, not recommended automatic merges. A raw numeric ID can refer to unrelated stations over time. Rack conversions, larger relocations, retired names and low-volume GPS anomalies need additional evidence. The uncertain identities mean first-trip estimates for some split entities may be later than the underlying station's actual first service.

Divvy's [system-data description](https://divvybikes.com/system-data) says staff trips and short trips are filtered before publication. Even a complete scan cannot recover unpublished trips or establish a certified opening date. First-day observations are flagged as limited by the start of available history.

### Validation and reproducibility

All 18 full-dataset checks passed: all archives present and verified; all 114,878,536 endpoints reconcile between mapped and excluded groups; entity keys are unique; all reported first dates equal observation minima; all mapping and merge keys resolve; the city table has coordinates and dates; and the SQL query reproduces its row count. Unit tests separately cover ID changes, spatial separation, ambiguous missing locations, name recovery, arrival-first logic, mixed timestamp formats and reversed trips.

See [README](#reproduce-the-analysis) for commands and the [machine-readable validation](reports/validation.json). The [DuckDB query](sql/first_trip.sql) recomputes the reference from the compact observation table. Source ZIPs are retained; extracted trip CSVs are temporary, and cached member summaries make reruns fast.

<!-- END GENERATED PROFILE -->
