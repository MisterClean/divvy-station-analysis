# Divvy station analysis

Estimate the first recorded service of Chicago's Divvy stations from the complete
[official trip archive](https://divvy-tripdata.s3.amazonaws.com/index.html), with a
reproducible Python and DuckDB pipeline.

**[Download the station reference CSV](data/processed/stations.csv)** ·
[Profiling report](reports/profile.md) · [SQL query](sql/first_trip.sql) ·
[Validation results](reports/validation.json)

The table includes `station_id`, `station_name`, `station_lon`, `station_lat` and
`station_first_trip_at`, plus identity, provenance and quality fields. Use
`station_key` as the entity key: Divvy has changed and reused source IDs.
First trip means the earliest qualifying **departure or arrival**, not a
certified opening date. Public racks and historical locations are included.
Uncertain identities and relocations can remain separate candidates.

## Reproduce the analysis

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
5. Build a local DuckDB database, reconcile every endpoint, and verify selected
   first-trip events directly against the original ZIP contents.
6. Regenerate the profiling report and its embedded copy in this README.

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
| `report.py`, `readme.py` | Report generation and README embedding | Yes |
| `verify_sources.py` | Independent source spot checks | Yes |
| `data/archive_manifest.json` | Pinned source URLs and hashes | Yes |
| `data/reference/` | Small pinned Chicago boundary and its provenance | Yes |
| `data/processed/stations.csv` | Derived station reference artifact | Yes |
| `reports/` | Profiling and validation evidence | Yes |
| `data/raw/`, `data/interim/` | Trip ZIPs and cached summaries | No |
| `data/stations.duckdb` | Queryable local analysis database | No |
| Other files in `data/processed/` | Detailed catalogs, observations and reviews | No |

`.gitignore` allows only the station CSV from generated data into Git. The trip
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

## Query or run individual stages

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
`excluded_observations`. For matching-only changes, run:

```bash
uv run --locked python build_stations.py
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

The complete archive scan produced **3,389 candidate public station/location entities within Chicago**, including **1,422 public racks**. Download the main table at [stations.csv](data/processed/stations.csv). It contains the requested station ID, name, longitude, latitude and first-trip timestamp, plus identity, provenance and quality fields.

This is a reproducible, conservative reference, not a certified opening-date register. A station may open before its first published trip. Name changes with weak evidence and moves over 150 metres can remain separate entities. **569 Chicago rows have fewer than 10 qualifying endpoints.** Use the quality flags and review table before treating the row count as a count of distinct physical installations.

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

Trips with end time before start time are excluded from first-date estimates; zero-duration trips are retained as published observations. Both arrival and departure events count. In **2,215 Chicago entities**, the first arrival precedes the first departure. Source timestamps are preserved as timezone-naive local clock readings; early README files call them CST, but the archives do not encode offsets. No UTC or daylight-saving interpretation is invented.

### Identity and coordinate rules

1. Normalize whitespace and case for matching; remove only trailing `(Temp)` and `(*)` display suffixes. Preserve direction, street numbers, rack prefixes and other meaningful name content.
2. Recover legacy coordinates from same-ID/name station catalogs whose coordinates agree within 150 m. A legacy-only ID match is also allowed when all catalog coordinates for that ID agree within 150 m; it is flagged.
3. Modern coordinates are per-file, per-name/ID/endpoint medians. Reject coordinate summaries whose 5th-to-95th percentile diagonal exceeds 300 m. Reject points outside a broad Chicago-region sanity box (41.4–42.3 latitude, −88.1–−87.3 longitude).
4. When available, use a consistent same-file arrival median with at least three coordinates to locate the corresponding departure observations. Departure GPS can be widely scattered even when a station name is present.
5. Cluster exact normalized names around fixed anchors within 150 m. Fixed anchors prevent transitive chains from joining far-apart locations. Missing historical coordinates attach only to an unambiguous ID/name cluster, prioritizing the same archive; ambiguous groups remain unlocated.
6. Merge different names only when they share a source ID, have the same station/rack classification, and all representative locations in the merged group are within 35 m. 131 links were accepted; every link is saved in `accepted_alias_merges.csv`. This is a heuristic, not an official ID crosswalk.
7. Choose the latest observed named source ID and display name. `station_key` identifies the resolved entity; raw `station_id` is **not a unique historical key**. There are 2,867 distinct selected source IDs among 3,389 Chicago rows. Keys are deterministic for the same snapshot and rules but can change after future data or matching changes.
8. Choose coordinates from a well-supported arrival summary in the latest eligible month, falling back to other usable observations or catalog coordinates. Coordinates describe that representative observed location, not necessarily the exact location at the first trip.
9. Filter public locations using the [City of Chicago boundary](https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-City/qqq8-j68g). Operational/test/depot names and IDs are retained in the all-entities table and excluded from the city reference. Public racks and temporary public stations remain included.

Examples of accepted name histories:

| Current name | First recorded event | Historical source IDs |
|---|---|---|
| Field Museum | 2013-06-28 14:57:00 | 13029 , 97 , CHI01749 |
| Michigan Ave & Ida B Wells Dr | 2013-06-27 13:00:00 | 45 , CHI00250 , TA1305000010 |

### Online dates and remaining uncertainty

Historical catalogs contain `online date` / `online_date` and, separately, `dateCreated`. The 2013 README describes online date as when a station went live, but later catalogs disagree in some cases. The output therefore retains the earliest and latest reported online dates separately from first trip, and keeps creation dates in the catalog audit. **605 Chicago entities have a reported online date; 529 have conflicting calendar dates across snapshots.** Catalog records without an associated trip remain in `station_catalog.csv`; no first-trip date is invented for them.

The result retains 3,676 entities across the entire system; 172 cannot be placed reliably and are also exported to `stations_unlocated.csv`. There are 81 entities outside the current city boundary and 39 operational entities. These categories can overlap. Missing coordinates prevent a definitive city/suburb assignment, so those entities are absent from `stations.csv`.

Potential identity links still needing review:

| Reason | Pairs |
|---|---:|
| possible_rename_or_rack_conversion | 418 |
| reused_id_or_relocation | 2,293 |
| shared_id_location_unknown | 499 |

These pairs are diagnostics, not recommended automatic merges. A raw numeric ID can refer to unrelated stations over time. Rack conversions, larger relocations, retired names and low-volume GPS anomalies need additional evidence. The uncertain identities mean first-trip estimates for some split entities may be later than the underlying station's actual first service.

Divvy's [system-data description](https://divvybikes.com/system-data) says staff trips and short trips are filtered before publication. Even a complete scan cannot recover unpublished trips or establish a certified opening date. First-day observations are flagged as limited by the start of available history.

### Validation and reproducibility

All 12 full-dataset checks passed: all archives present and verified; all 114,878,536 endpoints reconcile between mapped and excluded groups; entity keys are unique; all reported first dates equal observation minima; all mapping and merge keys resolve; the city table has coordinates and dates; and the SQL query reproduces its row count. Unit tests separately cover ID changes, spatial separation, ambiguous missing locations, name recovery, arrival-first logic, mixed timestamp formats and reversed trips.

See [README](#reproduce-the-analysis) for commands and the [machine-readable validation](reports/validation.json). The [DuckDB query](sql/first_trip.sql) recomputes the reference from the compact observation table. Source ZIPs are retained; extracted trip CSVs are temporary, and cached member summaries make reruns fast.

<!-- END GENERATED PROFILE -->
