"""Generate the full-scan report and validate the published station tables."""

import json
from collections import defaultdict
from pathlib import Path

import duckdb

from profile_data import fetch_dicts, literal


def scalar(connection: duckdb.DuckDBPyConnection, query: str) -> int | str:
    row = connection.execute(query).fetchone()
    assert row is not None
    return row[0]


def main() -> None:
    root = Path(__file__).resolve().parent
    profile = json.loads((root / "reports/profile.json").read_text(encoding="utf-8"))
    summary = json.loads((root / "reports/build_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "data/archive_manifest.json").read_text(encoding="utf-8"))
    trips = [
        m for m in profile["members"] if m["kind"] != "station_lookup" and m["duplicate_of"] is None
    ]
    catalogs = [m for m in profile["members"] if m["kind"] == "station_lookup"]
    totals = {
        k: sum(m.get(k, 0) for m in trips)
        for k in [
            "rows",
            "missing_start_id",
            "missing_end_id",
            "missing_start_name",
            "missing_end_name",
            "missing_start_coordinates",
            "missing_end_coordinates",
            "invalid_start_coordinates",
            "invalid_end_coordinates",
            "invalid_start_time",
            "invalid_end_time",
            "reversed_trips",
            "zero_duration_trips",
            "duplicate_or_missing_trip_ids",
        ]
    }
    by_month = defaultdict(int)
    for member in trips:
        for month in member["rows_by_month"]:
            by_month[month["month"]] += month["rows"]
    by_year = defaultdict(int)
    for month, rows in by_month.items():
        by_year[month[:4]] += rows
    with duckdb.connect(str(root / "data/stations.duckdb"), read_only=True) as connection:
        metrics = fetch_dicts(
            connection,
            """
            SELECT count(*) AS chicago_rows, count(DISTINCT station_key) AS unique_keys,
                count(DISTINCT station_id) AS unique_current_source_ids,
                count(*) FILTER (WHERE station_type='public_rack') AS public_racks,
                count(*) FILTER (WHERE endpoint_count < 10) AS low_evidence_rows,
                count(*) FILTER (WHERE reported_online_at IS NOT NULL) AS with_online_dates,
                count(*) FILTER (WHERE contains(quality_flags,'conflicting_reported_online_dates')) AS conflicting_online_dates,
                count(*) FILTER (WHERE station_first_arrival_at < station_first_departure_at) AS arrival_precedes_departure,
                min(station_first_trip_at) AS earliest_station_trip,
                max(station_last_trip_at) AS latest_station_trip FROM stations
        """,
        )[0]
        first_trip_query = (
            (root / "sql/first_trip.sql").read_text(encoding="utf-8").rstrip().rstrip(";")
        )
        checks = {
            "all_archives_downloaded_and_profiled": profile["complete"]
            and len({m["archive"] for m in profile["members"]}) == len(manifest["objects"]),
            "all_downloads_crc_verified": all(m.get("crc_verified") for m in manifest["objects"]),
            "all_trip_rows_accounted_for": summary["resolved_endpoints"]
            + summary["excluded_endpoints"]
            == totals["rows"] * 2,
            "station_keys_unique": metrics["chicago_rows"] == metrics["unique_keys"],
            "coordinates_and_dates_present_in_chicago_output": scalar(
                connection,
                "SELECT count(*) FROM stations WHERE station_lat IS NULL OR station_lon IS NULL OR station_first_trip_at IS NULL",
            )
            == 0,
            "every_first_date_matches_observation_minimum": scalar(
                connection,
                "SELECT count(*) FROM stations_all s JOIN (SELECT station_key,min(first_at) first_at FROM station_observations GROUP BY station_key) o USING(station_key) WHERE s.station_first_trip_at != o.first_at",
            )
            == 0,
            "observation_mapping_has_no_orphans": scalar(
                connection,
                "SELECT count(*) FROM station_observations o ANTI JOIN stations_all s USING(station_key)",
            )
            == 0,
            "accepted_merge_keys_exist": scalar(
                connection,
                "SELECT count(*) FROM accepted_alias_merges m ANTI JOIN stations_all s ON m.merged_station_key=s.station_key",
            )
            == 0,
            "chicago_output_contains_only_public_city_locations": scalar(
                connection,
                "SELECT count(*) FROM stations WHERE in_chicago IS NOT TRUE OR station_type='operational'",
            )
            == 0,
            "source_ids_are_text": scalar(
                connection, "SELECT typeof(station_id) FROM stations LIMIT 1"
            )
            == "VARCHAR",
            "sql_reproduces_reference_dates": scalar(
                connection,
                "WITH reproduced AS ("
                + first_trip_query
                + ") SELECT count(*) FROM stations s FULL OUTER JOIN reproduced r USING(station_key) WHERE s.station_first_trip_at IS DISTINCT FROM r.station_first_trip_at",
            )
            == 0,
        }
        review = fetch_dicts(
            connection,
            "SELECT reason,count(*) AS pairs FROM identity_review GROUP BY reason ORDER BY reason",
        )
        examples = fetch_dicts(
            connection,
            "SELECT station_name,station_first_trip_at,source_ids FROM stations WHERE station_name IN ('Michigan Ave & Ida B Wells Dr','Field Museum','MLK Jr Dr & Oakwood Blvd') ORDER BY station_name",
        )
        connection.execute(
            f"COPY (SELECT * FROM stations_all WHERE station_lat IS NULL) TO {literal(str(root / 'data/processed/stations_unlocated.csv'))} (HEADER, FORMAT CSV)"
        )
    expected_months = [
        f"{year:04d}-{month:02d}"
        for year in range(int(min(by_month)[:4]), int(max(by_month)[:4]) + 1)
        for month in range(1, 13)
        if min(by_month) <= f"{year:04d}-{month:02d}" <= max(by_month)
    ]
    checks["every_month_in_observed_coverage_present"] = all(
        month in by_month for month in expected_months
    )
    payload = {
        "checks": checks,
        "metrics": metrics,
        "trip_totals": totals,
        "rows_by_month": dict(sorted(by_month.items())),
    }
    (root / "reports/validation.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )
    if not all(checks.values()):
        raise ValueError(f"Validation failed: {[k for k, v in checks.items() if not v]}")
    year_table = "\n".join(f"| {year} | {rows:,} |" for year, rows in sorted(by_year.items()))
    quality_table = "\n".join(
        f"| {key.replace('_', ' ')} | {value:,} |" for key, value in totals.items() if key != "rows"
    )
    review_table = "\n".join(f"| {row['reason']} | {row['pairs']:,} |" for row in review)
    example_table = "\n".join(
        f"| {row['station_name']} | {row['station_first_trip_at']} | {row['source_ids'].replace('|', ',')} |"
        for row in examples
    )
    schema_count = len({tuple(m["headers"]) for m in trips})
    text = f"""# Divvy archive profile and station reference

## Result

The complete archive scan produced **{metrics["chicago_rows"]:,} candidate public station/location entities within Chicago**, including **{metrics["public_racks"]:,} public racks**. Download the main table at [stations.csv](../data/processed/stations.csv). It contains the requested station ID, name, longitude, latitude and first-trip timestamp, plus identity, provenance and quality fields.

This is a reproducible, conservative reference, not a certified opening-date register. A station may open before its first published trip. Name changes with weak evidence and moves over 150 metres can remain separate entities. **{metrics["low_evidence_rows"]:,} Chicago rows have fewer than 10 qualifying endpoints.** Use the quality flags and review table before treating the row count as a count of distinct physical installations.

## Coverage and download

- Source: [official Divvy trip bucket](https://divvy-tripdata.s3.amazonaws.com/index.html).
- Snapshot retrieved: {manifest["retrieved_at"]}.
- {len(manifest["objects"])} ZIPs, {sum(m["size"] for m in manifest["objects"]) / 1e9:.3f} GB compressed; eight download workers; {manifest["download_seconds"]:.1f} seconds for downloading and verification.
- Every ZIP passed byte-count, available single-part S3 ETag and ZIP CRC checks; a SHA-256 fingerprint was also recorded. The manifest records hashes and source URLs. Multipart ETags are not treated as MD5 checksums.
- {len(trips)} trip CSVs; {len(catalogs)} station catalogs (including the Excel catalog in 2014 Q1/Q2); {totals["rows"]:,} source trip rows.
- Trips start from {min(m["earliest_start"] for m in trips)} through {max(m["latest_start"] for m in trips)}. Some arrivals extend beyond the final starting month.
- All {len(expected_months)} calendar months from {min(by_month)} through {max(by_month)} are represented. This checks monthly presence, not completeness of every day's operations.
- {schema_count} distinct trip headers: historical station-from/to schemas, the verbose rental-details schema, and the modern ride schema introduced in April 2020. Modern timestamp precision also varies.
- Nested shapefile copies, README files, and macOS metadata are inventoried; the station CSV/XLSX catalogs supply the tabular station metadata. Nested shapefiles are not used as an additional station source.

| Start year | Trip rows |
|---|---:|
{year_table}

## Data quality: full scan

Counts below are source rows. Start and end issues can occur on the same trip, so do not sum them as a count of bad trips.

| Issue | Rows |
|---|---:|
{quality_table}

No CSV parse errors were silently skipped: parsing is strict and all columns are initially text. Station IDs stay text, including alphanumeric IDs and leading zeros. Missing IDs are not assigned from nearby GPS points. Unnamed observations are recovered only when the same ID has exactly one named identity in that archive; unresolved records are retained in the exclusion audit.

The duplicate-ID check is within each source file. The pipeline also detects byte-identical CSV/XLSX members by SHA-256. It does not globally deduplicate individual trips across files; counts are **source-record counts**, while duplicates do not change minimum dates. There are {sum(m["duplicate_of"] is not None for m in profile["members"])} byte-identical member copies excluded from aggregation.

Trips with end time before start time are excluded from first-date estimates; zero-duration trips are retained as published observations. Both arrival and departure events count. In **{metrics["arrival_precedes_departure"]:,} Chicago entities**, the first arrival precedes the first departure. Source timestamps are preserved as timezone-naive local clock readings; early README files call them CST, but the archives do not encode offsets. No UTC or daylight-saving interpretation is invented.

## Identity and coordinate rules

1. Normalize whitespace and case for matching; remove only trailing `(Temp)` and `(*)` display suffixes. Preserve direction, street numbers, rack prefixes and other meaningful name content.
2. Recover legacy coordinates from same-ID/name station catalogs whose coordinates agree within 150 m. A legacy-only ID match is also allowed when all catalog coordinates for that ID agree within 150 m; it is flagged.
3. Modern coordinates are per-file, per-name/ID/endpoint medians. Reject coordinate summaries whose 5th-to-95th percentile diagonal exceeds 300 m. Reject points outside a broad Chicago-region sanity box (41.4–42.3 latitude, −88.1–−87.3 longitude).
4. When available, use a consistent same-file arrival median with at least three coordinates to locate the corresponding departure observations. Departure GPS can be widely scattered even when a station name is present.
5. Cluster exact normalized names around fixed anchors within 150 m. Fixed anchors prevent transitive chains from joining far-apart locations. Missing historical coordinates attach only to an unambiguous ID/name cluster, prioritizing the same archive; ambiguous groups remain unlocated.
6. Merge different names only when they share a source ID, have the same station/rack classification, and all representative locations in the merged group are within 35 m. {summary["accepted_alias_merges"]} links were accepted; every link is saved in `accepted_alias_merges.csv`. This is a heuristic, not an official ID crosswalk.
7. Choose the latest observed named source ID and display name. `station_key` identifies the resolved entity; raw `station_id` is **not a unique historical key**. There are {metrics["unique_current_source_ids"]:,} distinct selected source IDs among {metrics["chicago_rows"]:,} Chicago rows. Keys are deterministic for the same snapshot and rules but can change after future data or matching changes.
8. Choose coordinates from a well-supported arrival summary in the latest eligible month, falling back to other usable observations or catalog coordinates. Coordinates describe that representative observed location, not necessarily the exact location at the first trip.
9. Filter public locations using the [City of Chicago boundary](https://data.cityofchicago.org/Facilities-Geographic-Boundaries/Boundaries-City/qqq8-j68g). Operational/test/depot names and IDs are retained in the all-entities table and excluded from the city reference. Public racks and temporary public stations remain included.

Examples of accepted name histories:

| Current name | First recorded event | Historical source IDs |
|---|---|---|
{example_table}

## Online dates and remaining uncertainty

Historical catalogs contain `online date` / `online_date` and, separately, `dateCreated`. The 2013 README describes online date as when a station went live, but later catalogs disagree in some cases. The output therefore retains the earliest and latest reported online dates separately from first trip, and keeps creation dates in the catalog audit. **{metrics["with_online_dates"]:,} Chicago entities have a reported online date; {metrics["conflicting_online_dates"]:,} have conflicting calendar dates across snapshots.** Catalog records without an associated trip remain in `station_catalog.csv`; no first-trip date is invented for them.

The result retains {summary["all_station_entities"]:,} entities across the entire system; {summary["missing_location_entities"]:,} cannot be placed reliably and are also exported to `stations_unlocated.csv`. There are {summary["outside_chicago_entities"]:,} entities outside the current city boundary and {summary["operational_entities"]:,} operational entities. These categories can overlap. Missing coordinates prevent a definitive city/suburb assignment, so those entities are absent from `stations.csv`.

Potential identity links still needing review:

| Reason | Pairs |
|---|---:|
{review_table}

These pairs are diagnostics, not recommended automatic merges. A raw numeric ID can refer to unrelated stations over time. Rack conversions, larger relocations, retired names and low-volume GPS anomalies need additional evidence. The uncertain identities mean first-trip estimates for some split entities may be later than the underlying station's actual first service.

Divvy's [system-data description](https://divvybikes.com/system-data) says staff trips and short trips are filtered before publication. Even a complete scan cannot recover unpublished trips or establish a certified opening date. First-day observations are flagged as limited by the start of available history.

## Validation and reproducibility

All {len(checks)} full-dataset checks passed: all archives present and verified; all {totals["rows"] * 2:,} endpoints reconcile between mapped and excluded groups; entity keys are unique; all reported first dates equal observation minima; all mapping and merge keys resolve; the city table has coordinates and dates; and the SQL query reproduces its row count. Unit tests separately cover ID changes, spatial separation, ambiguous missing locations, name recovery, arrival-first logic, mixed timestamp formats and reversed trips.

See [README](../README.md) for commands and the [machine-readable validation](validation.json). The [DuckDB query](../sql/first_trip.sql) recomputes the reference from the compact observation table. Source ZIPs are retained; extracted trip CSVs are temporary, and cached member summaries make reruns fast.
"""
    (root / "reports/profile.md").write_text(text, encoding="utf-8")
    print(json.dumps({"checks_passed": len(checks), "metrics": metrics}, indent=2, default=str))


if __name__ == "__main__":
    main()
