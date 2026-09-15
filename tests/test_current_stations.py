"""Keep the city inventory intact while enriching only accepted reference joins."""

import csv
import json
from datetime import datetime
from pathlib import Path

import pytest

from build_stations import write_csv
from reconcile_stations import CURRENT_REFERENCE_FIELDS, build_current_stations
from station_feeds import load_feeds


def reference() -> dict:
    return {
        "station_key": "divvy_example",
        "city_station_id": "0001",
        "station_first_trip_at": datetime(2021, 8, 25, 14, 29, 6),
        "source_ids": "007 | CHI001",
        "name_variants": "Old name | New name",
        "first_trip_archive": "202108-divvy-tripdata.zip",
        "opening_date_status": "unverified_first_trip_proxy",
        "earlier_same_name_trip_at": None,
        "service_date_review_reasons": "possible_rename",
    }


def test_city_left_join_preserves_optional_columns_names_status_and_geojson(tmp_path: Path) -> None:
    location = {"type": "Point", "coordinates": [-87.650123, 41.900123]}
    city = [
        {
            "id": "0001",
            "station_name": "New name (Temp)",
            "status": "In Service",
            "location": location,
            "short_name": "CHI001",
            "total_docks": "15",
        },
        {
            "id": "0002",
            "station_name": "New name (Temp)",
            "status": "Not Installed",
            "new_city_column": "3.00",
        },
    ]
    columns = [
        {"fieldName": name}
        for name in (
            "id",
            "station_name",
            "short_name",
            "total_docks",
            "status",
            "location",
            ":@computed_region_example",
            "entirely_null_column",
        )
    ]
    rows, fields = build_current_stations(city, columns, [reference()])
    assert [r["id"] for r in rows] == ["0001", "0002"]
    assert rows[0]["station_name"] == "New name (Temp)"
    assert rows[0]["reference_matched"] is True
    assert all(rows[0][field] == reference()[field] for field in CURRENT_REFERENCE_FIELDS)
    assert rows[1]["reference_matched"] is False
    assert all(rows[1][field] is None for field in CURRENT_REFERENCE_FIELDS)
    assert rows[1]["status"] == "Not Installed"
    assert "entirely_null_column" in fields and ":@computed_region_example" in fields
    assert rows[1]["new_city_column"] == "3.00"
    assert city[0]["location"] == location  # Source rows are not mutated.
    output = tmp_path / "current_stations.csv"
    write_csv(output, rows, fields)
    with output.open(newline="", encoding="utf-8") as stream:
        actual = list(csv.DictReader(stream))
    assert actual[0]["id"] == "0001"
    assert json.loads(actual[0]["location"]) == location
    assert actual[0]["station_first_trip_at"] == "2021-08-25 14:29:06"
    assert actual[1]["station_first_trip_at"] == ""


def test_duplicate_reference_city_link_fails_instead_of_expanding_inventory() -> None:
    with pytest.raises(ValueError, match="Multiple reference rows"):
        build_current_stations([{"id": "0001"}], [], [reference(), reference()])


def test_city_schema_collision_fails_instead_of_overwriting_source_column() -> None:
    with pytest.raises(ValueError, match="schema conflicts"):
        build_current_stations([{"id": "0001", "source_ids": "city-owned-value"}], [], [])


def test_published_current_inventory_matches_city_snapshot_and_reference() -> None:
    root = Path(__file__).resolve().parents[1]
    feeds, _ = load_feeds(root / "data/reference")
    with (root / "data/processed/stations.csv").open(encoding="utf-8", newline="") as stream:
        reference_by_id = {
            r["city_station_id"]: r for r in csv.DictReader(stream) if r["city_station_id"]
        }
    with (root / "data/processed/current_stations.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        reader = csv.DictReader(stream)
        actual = list(reader)
        assert reader.fieldnames is not None
        assert {c["fieldName"] for c in feeds["city_metadata"]["columns"]} <= set(reader.fieldnames)
    assert len(actual) == len(feeds["city"])
    assert len({r["id"] for r in actual}) == len(actual)
    for raw, row in zip(feeds["city"], actual, strict=True):
        for field, value in raw.items():
            assert (
                json.loads(row[field]) if isinstance(value, (dict, list)) else row[field]
            ) == value
        match = reference_by_id.get(raw["id"])
        assert row["reference_matched"] == ("True" if match is not None else "False")
        for field in CURRENT_REFERENCE_FIELDS:
            assert row[field] == (match[field] if match is not None else "")
