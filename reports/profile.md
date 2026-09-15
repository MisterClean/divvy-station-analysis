# Divvy archive profile and station reference

## Result

The complete archive scan produced **3,389 candidate public station/location entities within Chicago**, including **1,422 public racks**. Download the main table at [stations.csv](../data/processed/stations.csv). It contains the requested station ID, name, longitude, latitude and first-trip timestamp, plus identity, provenance and quality fields.

This is a reproducible, conservative reference, not a certified opening-date register. A station may open before its first published trip. Name changes with weak evidence and moves over 150 metres can remain separate entities. **569 Chicago rows have fewer than 10 qualifying endpoints.** Use the quality flags and review table before treating the row count as a count of distinct physical installations.

## Coverage and download

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

## Data quality: full scan

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

## Identity and coordinate rules

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

## Online dates and remaining uncertainty

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

## Validation and reproducibility

All 12 full-dataset checks passed: all archives present and verified; all 114,878,536 endpoints reconcile between mapped and excluded groups; entity keys are unique; all reported first dates equal observation minima; all mapping and merge keys resolve; the city table has coordinates and dates; and the SQL query reproduces its row count. Unit tests separately cover ID changes, spatial separation, ambiguous missing locations, name recovery, arrival-first logic, mixed timestamp formats and reversed trips.

See [README](../README.md) for commands and the [machine-readable validation](validation.json). The [DuckDB query](../sql/first_trip.sql) recomputes the reference from the compact observation table. Source ZIPs are retained; extracted trip CSVs are temporary, and cached member summaries make reruns fast.
