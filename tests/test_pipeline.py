"""Small fixtures for failure modes that can change a station's first-service estimate."""

from datetime import datetime
from pathlib import Path

import duckdb
from shapely.geometry import box

from build_stations import (
    cluster_name,
    merge_nearby_aliases,
    normalize_name,
    recover_names,
    summarize_cluster,
)
from profile_data import normalize_trips, summarize_trips


def observation(station_id: str, name: str | None, lat: float | None, lon: float | None) -> dict:
    return {
        "station_id": station_id,
        "station_name": name,
        "name_key": normalize_name(name),
        "lat": lat,
        "lon": lon,
        "role": "start",
        "valid_endpoint_count": 20,
        "coordinate_count": 20 if lat is not None else 0,
        "first_at": datetime(2020, 1, 1),
        "last_at": datetime(2020, 1, 2),
        "first_trip_id": "000123",
        "archive": "a.zip",
        "member": "a.csv",
        "coordinate_source": "trip_endpoint_median",
        "coordinate_inferred": False,
        "coordinate_rejected": False,
        "name_recovered": False,
    }


def test_same_name_new_id_and_small_coordinate_jitter_merge() -> None:
    rows = [
        observation("12", "Main St", 41.9, -87.6),
        observation("CHI12", "Main St", 41.9001, -87.6),
    ]
    assert len(cluster_name(rows)) == 1


def test_same_name_far_locations_do_not_merge_or_chain() -> None:
    rows = [observation(str(i), "Main St", 41.9 + i * 0.001, -87.6) for i in range(3)]
    assert len(cluster_name(rows)) == 2


def test_missing_location_with_reused_id_stays_ambiguous() -> None:
    rows = [
        observation("12", "Main St", 41.9, -87.6),
        observation("12", "Main St", 41.7, -87.6),
        observation("12", "Main St", None, None),
    ]
    clusters = cluster_name(rows)
    assert len(clusters) == 3
    assert clusters[-1]["ambiguous_missing_location"] is True


def test_name_recovery_requires_unambiguous_id_in_same_archive() -> None:
    missing = observation("12", None, 41.9, -87.6)
    named = observation("12", "Main St", 41.9, -87.6)
    recover_names([missing, named])
    assert missing["station_name"] == "Main St"
    conflict = observation("12", None, 41.9, -87.6)
    recover_names([conflict, named, observation("12", "Other St", 41.7, -87.6)])
    assert conflict["station_name"] is None


def test_arrival_can_be_earlier_than_first_departure() -> None:
    start = observation("0012", "Main St", 41.9, -87.6)
    end = {**start, "role": "end", "first_at": datetime(2019, 12, 31, 23, 59)}
    cluster = {"rows": [start, end], "anchor": (41.9, -87.6), "ambiguous_missing_location": False}
    station = summarize_cluster(cluster, "main st", box(-88, 41, -87, 43), [])
    assert station["station_id"] == "0012"
    assert station["station_first_trip_at"] == end["first_at"]
    assert station["first_trip_role"] == "end"
    assert station["station_first_departure_at"] == start["first_at"]


def test_mixed_legacy_dates_and_reversed_trips(tmp_path: Path) -> None:
    with duckdb.connect() as connection:
        connection.execute("""CREATE TABLE raw AS SELECT * FROM (VALUES
            ('0001','6/27/2013 10:00','6/27/2013 10:05','001','A','002','B'),
            ('0002','2013-06-26 10:00:00','2013-06-26 09:59:00','001','A','002','B'),
            ('0003','broken','2013-06-27 11:00:00','001','A','002','B')
        ) t(trip_id,starttime,stoptime,from_station_id,from_station_name,to_station_id,to_station_name)""")
        headers = [row[0] for row in connection.execute("DESCRIBE raw").fetchall()]
        normalize_trips(connection, headers)
        profile = summarize_trips(connection, tmp_path)
        assert profile["reversed_trips"] == 1
        assert profile["invalid_start_time"] == 1
        result = connection.execute(
            "SELECT station_id, first_at FROM read_parquet(?) ORDER BY station_id",
            [str(tmp_path / "observations.parquet")],
        ).fetchall()
        assert result == [("001", datetime(2013, 6, 27, 10)), ("002", datetime(2013, 6, 27, 10, 5))]


def test_suffixes_do_not_remove_street_direction_or_rack_type() -> None:
    assert normalize_name("  Main St   & First Ave (*) ") == "main st & first ave"
    assert normalize_name("Main St & First Ave (Temp)") == "main st & first ave"
    assert normalize_name("Public Rack - Main St N") == "public rack - main st n"


def test_name_change_requires_shared_id_and_nearby_coordinates() -> None:
    boundary = box(-88, 41, -87, 43)
    records = [
        observation("12", "Old name", 41.9, -87.6),
        observation("12", "New name", 41.9001, -87.6),
        observation("12", "Reused ID far away", 41.7, -87.6),
        observation("13", "Other station nearby", 41.9001, -87.6),
    ]
    records[1]["first_at"] = datetime(2021, 1, 1)
    records[1]["last_at"] = datetime(2021, 1, 2)
    stations = [
        summarize_cluster(
            {
                "rows": [row],
                "anchor": (row["lat"], row["lon"]),
                "ambiguous_missing_location": False,
            },
            row["name_key"],
            boundary,
            [],
        )
        for row in records
    ]
    result, accepted = merge_nearby_aliases(stations, boundary, [])
    assert len(result) == 3
    assert len(accepted) == 1
    merged = next(row for row in result if row["station_name"] == "New name")
    assert merged["station_first_trip_at"] == datetime(2020, 1, 1)
    assert merged["station_key"] == accepted[0]["merged_station_key"]
