"""Profile every CSV member and reduce trips to auditable station observations using DuckDB."""

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

PROFILE_VERSION = 3


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def column(headers: list[str], *candidates: str) -> str:
    normalized = {re.sub(r"[^a-z0-9]", "", h.lower()): h for h in headers}
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        if key in normalized:
            return identifier(normalized[key])
    raise ValueError(f"Missing column {candidates} in {headers}")


def clean(expression: str) -> str:
    return f"nullif(trim(regexp_replace({expression}, '\\s+', ' ', 'g')), '')"


def timestamp(expression: str) -> str:
    return f"""coalesce(try_cast({expression} AS TIMESTAMP),
        try_strptime({expression}, ['%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M',
                                   '%m/%d/%Y', '%m/%d/%y %H:%M', '%m/%d/%y %H:%M:%S']))"""


def fetch_dicts(connection: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    cursor = connection.execute(query)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def normalize_trips(connection: duckdb.DuckDBPyConnection, headers: list[str]) -> str:
    modern = "ride_id" in headers
    trip_id = column(headers, "ride_id", "trip_id", "01 - Rental Details Rental ID")
    start = column(
        headers, "started_at", "starttime", "start_time", "01 - Rental Details Local Start Time"
    )
    end = column(headers, "ended_at", "stoptime", "end_time", "01 - Rental Details Local End Time")
    sid = column(headers, "start_station_id", "from_station_id", "03 - Rental Start Station ID")
    sname = column(
        headers, "start_station_name", "from_station_name", "03 - Rental Start Station Name"
    )
    eid = column(headers, "end_station_id", "to_station_id", "02 - Rental End Station ID")
    ename = column(headers, "end_station_name", "to_station_name", "02 - Rental End Station Name")
    coordinates = (
        "try_cast(start_lat AS DOUBLE) AS start_lat, try_cast(start_lng AS DOUBLE) AS start_lon, "
        "try_cast(end_lat AS DOUBLE) AS end_lat, try_cast(end_lng AS DOUBLE) AS end_lon"
        if modern
        else "NULL::DOUBLE AS start_lat, NULL::DOUBLE AS start_lon, "
        "NULL::DOUBLE AS end_lat, NULL::DOUBLE AS end_lon"
    )
    connection.execute(f"""
        CREATE OR REPLACE TEMP TABLE trips AS SELECT
        {clean(trip_id)} AS trip_id, {timestamp(start)} AS started_at,
        {timestamp(end)} AS ended_at, {clean(sid)} AS start_station_id,
        {clean(sname)} AS start_station_name, {clean(eid)} AS end_station_id,
        {clean(ename)} AS end_station_name, {coordinates} FROM raw
    """)
    return "modern" if modern else "legacy"


def summarize_trips(connection: duckdb.DuckDBPyConnection, output: Path) -> dict:
    profile = fetch_dicts(
        connection,
        """
        SELECT count(*) AS rows, min(started_at) AS earliest_start, max(started_at) AS latest_start,
        min(ended_at) AS earliest_end, max(ended_at) AS latest_end,
        count(*) - count(DISTINCT trip_id) AS duplicate_or_missing_trip_ids,
        count(*) FILTER (WHERE trip_id IS NULL) AS missing_trip_ids,
        count(*) FILTER (WHERE started_at IS NULL) AS invalid_start_time,
        count(*) FILTER (WHERE ended_at IS NULL) AS invalid_end_time,
        count(*) FILTER (WHERE ended_at < started_at) AS reversed_trips,
        count(*) FILTER (WHERE ended_at = started_at) AS zero_duration_trips,
        count(*) FILTER (WHERE start_station_id IS NULL) AS missing_start_id,
        count(*) FILTER (WHERE end_station_id IS NULL) AS missing_end_id,
        count(*) FILTER (WHERE start_station_name IS NULL) AS missing_start_name,
        count(*) FILTER (WHERE end_station_name IS NULL) AS missing_end_name,
        count(*) FILTER (WHERE start_lat IS NULL OR start_lon IS NULL) AS missing_start_coordinates,
        count(*) FILTER (WHERE end_lat IS NULL OR end_lon IS NULL) AS missing_end_coordinates,
        count(*) FILTER (WHERE start_lat IS NOT NULL AND start_lon IS NOT NULL AND
            NOT (start_lat BETWEEN 41.4 AND 42.3 AND start_lon BETWEEN -88.1 AND -87.3)) AS invalid_start_coordinates,
        count(*) FILTER (WHERE end_lat IS NOT NULL AND end_lon IS NOT NULL AND
            NOT (end_lat BETWEEN 41.4 AND 42.3 AND end_lon BETWEEN -88.1 AND -87.3)) AS invalid_end_coordinates,
        count(DISTINCT start_station_id) AS distinct_start_ids,
        count(DISTINCT end_station_id) AS distinct_end_ids FROM trips
    """,
    )[0]
    profile["rows_by_month"] = fetch_dicts(
        connection,
        """
        SELECT strftime(started_at, '%Y-%m') AS month, count(*) AS rows
        FROM trips GROUP BY month ORDER BY month
    """,
    )
    connection.execute("""
        CREATE OR REPLACE TEMP TABLE endpoints AS
        SELECT trip_id, started_at AS event_at, start_station_id AS station_id,
               start_station_name AS station_name, start_lat AS lat, start_lon AS lon, 'start' AS role,
               started_at IS NOT NULL AND ended_at IS NOT NULL AND ended_at >= started_at AS valid_trip
        FROM trips
        UNION ALL
        SELECT trip_id, ended_at, end_station_id, end_station_name, end_lat, end_lon, 'end',
               started_at IS NOT NULL AND ended_at IS NOT NULL AND ended_at >= started_at FROM trips
    """)
    # Both ends matter: an arrival can precede a station's first recorded departure.
    # Keep missing IDs/names as separate groups for coverage accounting and later resolution.
    # Grid-rounded coordinates describe an area roughly 1 km across, not a dock.
    # Retain those trips and their dates, but use only finer coordinates for locations.
    connection.execute("""
        CREATE OR REPLACE TEMP VIEW located_endpoints AS SELECT *,
            lat BETWEEN 41.4 AND 42.3 AND lon BETWEEN -88.1 AND -87.3 AS in_region,
            abs(lat - round(lat, 2)) < 1e-9 AND abs(lon - round(lon, 2)) < 1e-9 AS coarse
        FROM endpoints
    """)
    connection.execute(f"""
        COPY (
          SELECT station_id, station_name, role,
            count(*) AS endpoint_count,
            count(*) FILTER (WHERE valid_trip) AS valid_endpoint_count,
            min(event_at) FILTER (WHERE valid_trip) AS first_at,
            max(event_at) FILTER (WHERE valid_trip) AS last_at,
            arg_min(trip_id, struct_pack(t := event_at, id := trip_id)) FILTER (WHERE valid_trip) AS first_trip_id,
            count(*) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS coordinate_count,
            count(*) FILTER (WHERE in_region AND coarse AND valid_trip) AS coarse_coordinate_count,
            median(lat) FILTER (WHERE in_region AND coarse AND valid_trip) AS coarse_lat,
            median(lon) FILTER (WHERE in_region AND coarse AND valid_trip) AS coarse_lon,
            median(lat) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS lat,
            median(lon) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS lon,
            quantile_cont(lat, 0.05) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS lat_p05,
            quantile_cont(lat, 0.95) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS lat_p95,
            quantile_cont(lon, 0.05) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS lon_p05,
            quantile_cont(lon, 0.95) FILTER (WHERE in_region AND NOT coarse AND valid_trip) AS lon_p95
          FROM located_endpoints GROUP BY station_id, station_name, role
        ) TO {literal(str(output / "observations.parquet"))} (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    return profile


def summarize_lookup(
    connection: duckdb.DuckDBPyConnection, headers: list[str], output: Path
) -> dict:
    sid = column(headers, "id", "station_id")
    name = column(headers, "name", "station_name")
    lat = column(headers, "latitude", "lat")
    lon = column(headers, "longitude", "lon", "long")
    normalized = {re.sub(r"[^a-z0-9]", "", h.lower()): h for h in headers}
    online = (
        timestamp(identifier(normalized["onlinedate"]))
        if "onlinedate" in normalized
        else "NULL::TIMESTAMP"
    )
    created = (
        timestamp(identifier(normalized["datecreated"]))
        if "datecreated" in normalized
        else "NULL::TIMESTAMP"
    )
    connection.execute(f"""
        COPY (SELECT {clean(sid)} AS station_id, {clean(name)} AS station_name,
            try_cast({lat} AS DOUBLE) AS lat, try_cast({lon} AS DOUBLE) AS lon,
            {online} AS reported_online_at, {created} AS reported_created_at
            FROM raw) TO {literal(str(output / "lookup.parquet"))} (FORMAT PARQUET)
    """)
    result = connection.execute("SELECT count(*) FROM raw").fetchone()
    assert result is not None
    return {"rows": result[0]}


def catalog_xlsx_to_csv(source: Path, destination: Path) -> None:
    """Read the single-sheet 2014 catalog, including native Excel dates, without Excel.

    This deliberately supports only this observed input format and fails on formulas or new
    number formats rather than silently interpreting an unfamiliar workbook.
    """
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(source) as workbook:
        sheets = [n for n in workbook.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)]
        if len(sheets) != 1:
            raise ValueError("Expected a single-sheet station catalog")
        properties = ET.fromstring(workbook.read("xl/workbook.xml")).find("s:workbookPr", ns)
        if properties is not None and properties.get("date1904") in ("1", "true"):
            raise ValueError("Unexpected Excel 1904 date system")
        strings = [
            "".join(item.itertext())
            for item in ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        ]
        styles = ET.fromstring(workbook.read("xl/styles.xml")).find("s:cellXfs", ns)
        assert styles is not None
        formats = [int(style.attrib["numFmtId"]) for style in styles]
        root = ET.fromstring(workbook.read(sheets[0]))
        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            for row in root.findall(".//s:sheetData/s:row", ns):
                values = {}
                for cell in row:
                    if cell.find("s:f", ns) is not None:
                        raise ValueError("Unexpected formula in station catalog")
                    index = 0
                    for char in re.sub(r"\d", "", cell.attrib["r"]):
                        index = index * 26 + ord(char) - ord("A") + 1
                    value = cell.findtext("s:v", default="", namespaces=ns)
                    if cell.get("t") == "s":
                        value = strings[int(value)]
                    elif value:
                        fmt = formats[int(cell.get("s", "0"))]
                        if fmt == 14:
                            value = (
                                datetime(1899, 12, 30) + timedelta(days=float(value))
                            ).isoformat(sep=" ")
                        elif fmt != 0:
                            raise ValueError(f"Unexpected catalog number format {fmt}")
                    values[index] = value
                writer.writerow([values.get(i, "") for i in range(1, 7)])


def profile_member(archive_path: Path, member: zipfile.ZipInfo, output: Path) -> dict:
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="divvy-", dir=output.parent) as temp:
        csv_path = Path(temp) / "member.csv"
        with (
            zipfile.ZipFile(archive_path) as archive,
            archive.open(member) as stream,
            csv_path.open("wb") as target,
        ):
            shutil.copyfileobj(stream, target, length=1024 * 1024)
        with csv_path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if member.filename.lower().endswith(".xlsx"):
            xlsx_path = csv_path.with_suffix(".xlsx")
            csv_path.rename(xlsx_path)
            catalog_xlsx_to_csv(xlsx_path, csv_path)
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            headers = next(csv.reader(stream))
        with duckdb.connect(config={"threads": 4, "memory_limit": "6GB"}) as connection:
            connection.execute(f"""CREATE TEMP TABLE raw AS
                SELECT * FROM read_csv({literal(str(csv_path))}, header=true, all_varchar=true,
                                      delim=',', quote='"', escape='"', sample_size=-1)
            """)
            is_trip = any(
                x in headers for x in ("ride_id", "trip_id", "01 - Rental Details Rental ID")
            )
            if is_trip:
                schema = normalize_trips(connection, headers)
                profile = summarize_trips(connection, output)
            else:
                schema = "station_lookup"
                profile = summarize_lookup(connection, headers, output)
    result = {
        "archive": archive_path.name,
        "member": member.filename,
        "uncompressed_bytes": member.file_size,
        "crc": member.CRC,
        "sha256": digest,
        "headers": headers,
        "kind": schema,
        "seconds": round(time.monotonic() - started, 3),
        **profile,
    }
    (output / "profile.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--available", action="store_true", help="Profile downloaded files while download continues"
    )
    args = parser.parse_args()
    manifest = json.loads((args.data_dir / "archive_manifest.json").read_text(encoding="utf-8"))
    profiles, inventory, seen_hashes = [], [], {}
    missing = []
    for item in manifest["objects"]:
        archive_path = args.data_dir / "raw" / Path(item["key"]).name
        if not archive_path.exists():
            missing.append(item["key"])
            continue
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                is_data_table = (
                    member.filename.lower().endswith((".csv", ".xlsx"))
                    and "__MACOSX" not in member.filename
                    and not Path(member.filename).name.startswith(".")
                )
                inventory.append(
                    {
                        "archive": archive_path.name,
                        "member": member.filename,
                        "bytes": member.file_size,
                        "is_data_table": is_data_table,
                    }
                )
                if not is_data_table:
                    continue
                key = hashlib.sha256(
                    f"{PROFILE_VERSION}|{item['etag']}|{member.filename}|{member.CRC}".encode()
                ).hexdigest()[:24]
                output = args.data_dir / "interim" / key
                if (output / "profile.json").exists():
                    result = json.loads((output / "profile.json").read_text(encoding="utf-8"))
                else:
                    print(f"Profiling {archive_path.name} :: {member.filename}", flush=True)
                    result = profile_member(archive_path, member, output)
                result["cache_dir"] = str(output)
                result["duplicate_of"] = seen_hashes.get(result["sha256"])
                seen_hashes[result["sha256"]] = archive_path.name + "/" + member.filename
                profiles.append(result)
    reports = args.data_dir.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = {
        "complete": not missing,
        "missing_archives": missing,
        "members": profiles,
        "zip_inventory": inventory,
    }
    (reports / "profile.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(
        f"Profiled {len(profiles)} data tables; {len(missing)} archives still missing", flush=True
    )
    if missing and not args.available:
        raise RuntimeError(
            "Incomplete download. Run download.py first (or use --available for exploration)."
        )


if __name__ == "__main__":
    main()
