"""Reproduce the one-time before/after cleanup audit from saved baseline outputs."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import duckdb

from build_stations import write_csv
from profile_data import fetch_dicts


def main() -> None:
    root = Path(__file__).resolve().parent
    baseline = root / "data/interim/baseline_stations.csv"
    if not baseline.exists():
        raise FileNotFoundError(
            "Save baseline_stations.csv and baseline_station_observations.csv from the original pipeline first; see reports/station_cleanup.md"
        )
    with duckdb.connect(str(root / "data/stations.duckdb"), read_only=True) as connection:
        changes = fetch_dicts(connection, (root / "sql/cleanup_comparison.sql").read_text())
        current = fetch_dicts(connection, "SELECT * FROM stations")
        previous = fetch_dicts(
            connection, f"SELECT * FROM read_csv('{baseline}',all_varchar=true, sample_size=-1)"
        )
        # Validate the observation-grain join independently of entity changes.
        accounting = fetch_dicts(
            connection,
            """
            SELECT count(*) AS old_observation_rows, sum(valid_endpoint_count::BIGINT) AS old_endpoints
            FROM read_csv('data/interim/baseline_station_observations.csv',all_varchar=true, sample_size=-1)
        """,
        )[0]
        joined = fetch_dicts(
            connection,
            """
            SELECT count(*) AS joined_rows, sum(old.valid_endpoint_count::BIGINT) AS joined_endpoints,
                count(*) FILTER(WHERE old.valid_endpoint_count::BIGINT != new.valid_endpoint_count) AS changed_counts
            FROM read_csv('data/interim/baseline_station_observations.csv',all_varchar=true, sample_size=-1) old
            JOIN station_observations new
              ON old.archive=new.archive AND old.member=new.member AND old.role=new.role
             AND old.station_id IS NOT DISTINCT FROM new.station_id
             AND old.original_station_name IS NOT DISTINCT FROM new.original_station_name
        """,
        )[0]
        date_risks = fetch_dicts(
            connection,
            """
            SELECT s.station_key,s.station_name,s.station_type,s.station_first_trip_at,
                   min(a.station_first_trip_at) AS earlier_unlocated_same_name,
                   s.current_feed_match_status
            FROM stations s JOIN stations_all a ON s.station_name=a.station_name
             AND a.station_lat IS NULL AND a.station_first_trip_at<s.station_first_trip_at
            GROUP BY ALL ORDER BY station_first_trip_at,station_name
        """,
        )
        coarse = fetch_dicts(
            connection,
            """
            SELECT sum(coarse_coordinate_count) AS coarse_named_endpoints,
                count(*) FILTER(WHERE coarse_coordinate_count>0) AS affected_observation_groups
            FROM station_observations
        """,
        )[0]
    checks = {
        "every_old_observation_joined_once": accounting["old_observation_rows"]
        == joined["joined_rows"],
        "every_old_valid_endpoint_preserved": accounting["old_endpoints"]
        == joined["joined_endpoints"],
        "no_observation_endpoint_counts_changed": joined["changed_counts"] == 0,
        "every_old_chicago_candidate_traced": len({r["old_station_key"] for r in changes})
        == len(previous),
    }
    summary = {
        "baseline_artifact_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
        "current_artifact_sha256": hashlib.sha256(
            (root / "data/processed/stations.csv").read_bytes()
        ).hexdigest(),
        "baseline_rows": len(previous),
        "cleaned_rows": len(current),
        "baseline_types": dict(Counter(s["station_type"] for s in previous)),
        "cleaned_types": dict(Counter(s["station_type"] for s in current)),
        "baseline_coarse_grid_rows": sum(
            all(
                abs(float(s[k]) - round(float(s[k]), 2)) < 1e-9
                for k in ("station_lat", "station_lon")
            )
            for s in previous
        ),
        "baseline_low_evidence_rows": sum(int(s["endpoint_count"]) < 10 for s in previous),
        "cleaned_low_evidence_rows": sum(s["endpoint_count"] < 10 for s in current),
        "trace_dispositions": dict(Counter(r["disposition"] for r in changes)),
        "earlier_unlocated_same_name_count": len(date_risks),
        "earlier_same_name_history_rows": sum(
            s["earlier_same_name_trip_at"] is not None for s in current
        ),
        "earlier_same_name_history_crosses_2023": sum(
            s["earlier_same_name_trip_at"] is not None
            and s["station_first_trip_at"].year >= 2023
            and s["earlier_same_name_trip_at"].year < 2023
            for s in current
        ),
        "service_date_review_rows": sum(bool(s["service_date_review_reasons"]) for s in current),
        "earlier_unlocated_same_name_crosses_2023": sum(
            r["station_first_trip_at"].year >= 2023 and r["earlier_unlocated_same_name"].year < 2023
            for r in date_risks
        ),
        **coarse,
        "observation_accounting": {**accounting, **joined},
        "checks": checks,
    }
    write_csv(root / "reports/cleanup_changes.csv", changes, list(changes[0]))
    unlocated_by_key = {r["station_key"]: r["earlier_unlocated_same_name"] for r in date_risks}
    write_csv(
        root / "reports/service_date_review.csv",
        [
            {**s, "earlier_unlocated_same_name": unlocated_by_key.get(s["station_key"])}
            for s in current
            if s["service_date_review_reasons"]
        ],
        [
            "station_key",
            "station_name",
            "station_type",
            "station_first_trip_at",
            "earlier_same_name_trip_at",
            "earlier_unlocated_same_name",
            "current_feed_match_status",
            "service_date_review_reasons",
            "first_trip_archive",
            "first_trip_id",
        ],
    )
    (root / "reports/cleanup_comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    if not all(checks.values()):
        raise ValueError("Before/after observation accounting failed")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
