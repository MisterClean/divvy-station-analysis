"""Independently check representative first-trip evidence with Python's CSV reader."""

import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import duckdb

from profile_data import fetch_dicts


def parse_time(value: str) -> datetime:
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported source timestamp: {value}")


def main() -> None:
    root = Path(__file__).resolve().parent
    with duckdb.connect(str(root / "data/stations.duckdb"), read_only=True) as connection:
        samples = []
        for selection in (
            "ORDER BY station_first_trip_at LIMIT 2",
            "WHERE first_trip_role='end' ORDER BY station_first_trip_at LIMIT 1",
            "WHERE station_name='Field Museum' ORDER BY station_first_trip_at LIMIT 1",
            "ORDER BY station_first_trip_at DESC LIMIT 2",
        ):
            samples.extend(fetch_dicts(connection, "SELECT * FROM stations " + selection))
    grouped = defaultdict(list)
    for sample in {row["station_key"]: row for row in samples}.values():
        grouped[(sample["first_trip_archive"], sample["first_trip_member"])].append(sample)
    evidence = []
    for (archive, member), candidates in grouped.items():
        targets = {(row["first_trip_id"], row["first_trip_role"]): row for row in candidates}
        found = set()
        with zipfile.ZipFile(root / "data/raw" / archive) as source, source.open(member) as stream:
            reader = csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8-sig", newline=""))
            for row in reader:
                trip_id = row.get(
                    "ride_id", row.get("trip_id", row.get("01 - Rental Details Rental ID"))
                )
                matching = [key for key in targets if key[0] == trip_id and key not in found]
                for match in matching:
                    verify_row(row, targets[match], archive, member, evidence)
                    found.add(match)
                if found == set(targets):
                    break
        if found != set(targets):
            raise ValueError(f"Missing source evidence in {archive}: {set(targets) - found}")
    (root / "reports/source_spot_checks.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Verified {len(evidence)} first-trip events directly in {len(grouped)} original ZIP members"
    )


def verify_row(row: dict, target: dict, archive: str, member: str, evidence: list[dict]) -> None:
    trip_id = target["first_trip_id"]
    modern = "ride_id" in row
    is_start = target["first_trip_role"] == "start"
    if modern:
        time_column = "started_at" if is_start else "ended_at"
        id_column = "start_station_id" if is_start else "end_station_id"
        name_column = "start_station_name" if is_start else "end_station_name"
    else:
        time_column = "starttime" if is_start else "stoptime"
        id_column = "from_station_id" if is_start else "to_station_id"
        name_column = "from_station_name" if is_start else "to_station_name"
    timestamp = parse_time(row[time_column])
    if timestamp != target["station_first_trip_at"]:
        raise ValueError(f"First-event timestamp disagrees with raw source for {trip_id}")
    evidence.append(
        {
            "station_key": target["station_key"],
            "station_name": target["station_name"],
            "source_station_id": row[id_column],
            "source_station_name": row[name_column],
            "trip_id": trip_id,
            "role": target["first_trip_role"],
            "event_at": str(timestamp),
            "archive": archive,
            "member": member,
            "verified": True,
        }
    )


if __name__ == "__main__":
    main()
