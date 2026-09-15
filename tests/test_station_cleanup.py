"""Regressions for coarse GPS inflation, ambiguous feed matches, and date preservation."""

import json
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from build_stations import cluster_name, coarse_cell_contains, recover_feed_coordinates
from profile_data import fetch_dicts, normalize_trips, summarize_trips
from reconcile_stations import resolve_matches
from station_feeds import load_feeds
from tests.test_pipeline import observation


def test_coarse_coordinates_keep_first_trip_but_do_not_move_station(tmp_path: Path) -> None:
    with duckdb.connect() as connection:
        connection.execute("""CREATE TABLE raw AS SELECT * FROM (VALUES
            ('early','2022-01-01 10:00','2022-01-01 10:05','001','A','001','A',41.90,-87.65,41.90,-87.65),
            ('fine','2023-01-01 10:00','2023-01-01 10:05','001','A','001','A',41.904,-87.646,41.904,-87.646),
            ('reversed','2020-01-01 10:00','2020-01-01 09:00','001','A','001','A',41.72,-87.61,41.72,-87.61)
        ) t(ride_id,started_at,ended_at,start_station_id,start_station_name,end_station_id,end_station_name,start_lat,start_lng,end_lat,end_lng)""")
        normalize_trips(connection, [r[0] for r in connection.sql("DESCRIBE raw").fetchall()])
        summarize_trips(connection, tmp_path)
        rows = fetch_dicts(
            connection, f"SELECT * FROM read_parquet('{tmp_path / 'observations.parquet'}')"
        )
    for row in rows:
        assert row["lat"] == 41.904
        assert row["lon"] == -87.646
        assert row["coordinate_count"] == 1
        assert row["coarse_coordinate_count"] == 1
        assert row["first_at"].year == 2022
        assert row["endpoint_count"] == 3
        assert row["valid_endpoint_count"] == 2


def test_coarse_only_summary_has_no_precise_location(tmp_path: Path) -> None:
    with duckdb.connect() as connection:
        connection.execute("""CREATE TABLE raw AS SELECT 'x' ride_id,
            '2022-01-01 10:00' started_at, '2022-01-01 10:05' ended_at,
            '001' start_station_id, 'A' start_station_name,
            '001' end_station_id, 'A' end_station_name,
            41.90 start_lat,-87.65 start_lng,41.90 end_lat,-87.65 end_lng""")
        normalize_trips(connection, [r[0] for r in connection.sql("DESCRIBE raw").fetchall()])
        summarize_trips(connection, tmp_path)
        rows = fetch_dicts(
            connection, f"SELECT * FROM read_parquet('{tmp_path / 'observations.parquet'}')"
        )
    assert all(r["lat"] is None and r["lon"] is None for r in rows)
    assert all(r["first_trip_id"] == "x" and r["coarse_coordinate_count"] == 1 for r in rows)


def test_rounding_cell_attaches_only_to_unambiguous_nearby_history() -> None:
    coarse = {**observation("001", "A", None, None), "coarse_lat": 41.90, "coarse_lon": -87.65}
    close = observation("001", "A", 41.904, -87.646)
    assert coarse_cell_contains(coarse, (41.904, -87.646))
    assert len(cluster_name([coarse.copy(), close])) == 1
    far = observation("001", "A", 41.8, -87.65)
    clusters = cluster_name([coarse.copy(), far])
    assert len(clusters) == 2
    assert clusters[-1]["anchor"] is None
    second = observation("001", "A", 41.897, -87.646)
    assert cluster_name([coarse.copy(), close, second])[-1]["anchor"] is None


def test_current_location_inference_never_invents_first_service() -> None:
    row = {**observation("001", "A", None, None), "coarse_lat": 41.90, "coarse_lon": -87.65}
    feeds = {
        "city": [],
        "information": {
            "data": {
                "stations": [{"station_id": "uuid", "name": "A", "lat": 41.904, "lon": -87.646}]
            }
        },
    }
    original_date = row["first_at"]
    recover_feed_coordinates([row], feeds)
    assert row["lat"] == 41.904
    assert row["coordinate_inferred"] is True
    assert row["first_at"] == original_date


def reference(key: str, name: str, source_ids: str) -> dict:
    return {
        "station_key": key,
        "station_name": name,
        "name_variants": name,
        "source_ids": source_ids,
        "station_type": "station",
        "station_lat": 41.9001,
        "station_lon": -87.6501,
        "station_first_trip_at": datetime(2013, 6, 27),
        "station_last_trip_at": datetime(2026, 8, 31),
        "endpoint_count": 100,
    }


def current() -> dict:
    return {
        "source": "city",
        "feed_station_id": "uuid",
        "short_name": "CHI001",
        "station_name": "Main St & First Ave",
        "name_key": "main st & first ave",
        "name_class": "station",
        "station_lat": 41.9001,
        "station_lon": -87.6501,
    }


def test_reused_id_does_not_match_far_station() -> None:
    station = {**reference("a", "Old location", "CHI001"), "station_lat": 41.7}
    candidates, accepted = resolve_matches([station], [current()])
    assert len(candidates) == 1
    assert not accepted


def test_fuzzy_or_proximity_only_match_is_review_only() -> None:
    station = reference("a", "Main Street & First Avenue", "old-id")
    candidates, accepted = resolve_matches([station], [current()])
    assert candidates[0]["name_similarity"] > 0.8
    assert not accepted


def test_two_reference_candidates_cannot_both_claim_current_station() -> None:
    stations = [reference(key, "Main St & First Ave", "CHI001") for key in ("a", "b")]
    candidates, accepted = resolve_matches(stations, [current()])
    assert len(candidates) == 2
    assert not accepted


def test_rack_conversion_does_not_inherit_station_opening_date() -> None:
    station = {
        **reference("a", "Public Rack - Main St & First Ave", "CHI001"),
        "station_type": "public_rack",
    }
    _, accepted = resolve_matches([station], [current()])
    assert not accepted


def test_pinned_feed_hash_mismatch_fails_before_use(tmp_path: Path) -> None:
    (tmp_path / "city.json").write_text("[]")
    (tmp_path / "station_feeds.json").write_text(
        json.dumps({"sources": {"city": {"path": "city.json", "sha256": "not-the-content-hash"}}})
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        load_feeds(tmp_path)
