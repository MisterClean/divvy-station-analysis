"""Reconcile historical station candidates with pinned feeds without deleting retired history."""

import json
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import duckdb
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from build_stations import distance, normalize_name, write_csv
from profile_data import fetch_dicts, literal
from station_feeds import load_feeds

CURRENT_REFERENCE_FIELDS = (
    "station_key",
    "station_first_trip_at",
    "source_ids",
    "name_variants",
    "first_trip_archive",
    "opening_date_status",
    "earlier_same_name_trip_at",
    "service_date_review_reasons",
)


def city_csv_value(value: object) -> object:
    """Keep nested city fields (notably GeoJSON location) as valid JSON in CSV cells."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def build_current_stations(
    city: list[dict], columns: list[dict], reference: list[dict]
) -> tuple[list[dict], list[str]]:
    """Left-join every city row to the published reference's accepted city ID link."""
    city_fields = list(
        dict.fromkeys(
            [column["fieldName"] for column in columns if column.get("position", 0) >= 0]
            + [field for row in city for field in row]
        )
    )
    added_fields = ["reference_matched", *CURRENT_REFERENCE_FIELDS]
    if set(city_fields) & set(added_fields):
        raise ValueError("City schema conflicts with added reference fields")
    if len({row["id"] for row in city}) != len(city):
        raise ValueError("Duplicate city IDs would violate the current-stations grain")
    reference_by_city_id = {}
    for station in reference:
        city_id = station.get("city_station_id")
        if city_id is None or city_id == "":
            continue
        if city_id in reference_by_city_id:
            raise ValueError(f"Multiple reference rows claim city ID {city_id}")
        reference_by_city_id[city_id] = station
    rows = []
    for raw in city:
        station = reference_by_city_id.get(raw["id"])
        rows.append(
            {
                **{field: city_csv_value(raw.get(field)) for field in city_fields},
                "reference_matched": station is not None,
                **{
                    field: station[field] if station is not None else None
                    for field in CURRENT_REFERENCE_FIELDS
                },
            }
        )
    return rows, [*city_fields, *added_fields]


def feed_rows(feeds: dict, boundary) -> list[dict]:
    statuses = {r["station_id"]: r for r in feeds["status"]["data"]["stations"]}
    rows = []
    for source in ("city", "gbfs"):
        records = feeds["city"] if source == "city" else feeds["information"]["data"]["stations"]
        for raw in records:
            city = source == "city"
            source_id = raw["id"] if city else raw["station_id"]
            name = raw["station_name"] if city else raw["name"]
            lat, lon = (
                float(raw["latitude"] if city else raw["lat"]),
                float(raw["longitude"] if city else raw["lon"]),
            )
            status = statuses.get(source_id, {})
            rows.append(
                {
                    "source": source,
                    "feed_station_id": source_id,
                    "short_name": raw.get("short_name", ""),
                    "station_name": name,
                    "station_lat": lat,
                    "station_lon": lon,
                    "in_chicago": boundary.covers(Point(lon, lat)),
                    "status": raw["status"]
                    if city
                    else (
                        "not_installed"
                        if status.get("is_installed") == 0
                        else (
                            "renting_and_returning"
                            if status.get("is_renting") == 1 and status.get("is_returning") == 1
                            else "service_restricted"
                        )
                    ),
                    "feed_station_type": "" if city else raw.get("station_type", ""),
                    "capacity": int(raw["total_docks"] if city else raw["capacity"]),
                    "name_key": normalize_name(name),
                    "name_class": "public_rack" if "public rack" in name.casefold() else "station",
                }
            )
    return rows


def matching_candidates(station: dict, current: list[dict]) -> list[dict]:
    """Fuzzy and proximity-only links are review suggestions, never automatic merges."""
    names = {normalize_name(n) for n in station["name_variants"].split(" | ")}
    ids = set((station["source_ids"] or "").split(" | ")) - {""}
    candidates = []
    for feed in current:
        separation = (
            None
            if station["station_lat"] is None
            else distance(
                (station["station_lat"], station["station_lon"]),
                (feed["station_lat"], feed["station_lon"]),
            )
        )
        exact_name = feed["name_key"] in names
        shared_id = bool(feed["short_name"] and feed["short_name"] in ids)
        if not exact_name and not shared_id and (separation is None or separation > 150):
            continue
        score = max(SequenceMatcher(None, n, feed["name_key"]).ratio() for n in names)
        same_class = station["station_type"] == feed["name_class"]
        method, rank = "review_only", 99
        if separation is not None and same_class:
            if exact_name and separation <= 150:
                method = "name_and_short_name_within_150m" if shared_id else "name_within_150m"
                rank = 0 if shared_id else 1
            elif shared_id and separation <= 35:
                method, rank = "short_name_within_35m", 2
        if (
            not exact_name
            and not shared_id
            and score < 0.65
            and separation is not None
            and separation > 35
        ):
            continue
        candidates.append(
            {
                "station_key": station["station_key"],
                "reference_name": station["station_name"],
                "reference_first_trip_at": station["station_first_trip_at"],
                "reference_last_trip_at": station["station_last_trip_at"],
                "reference_endpoint_count": station["endpoint_count"],
                "source": feed["source"],
                "feed_station_id": feed["feed_station_id"],
                "feed_name": feed["station_name"],
                "feed_short_name": feed["short_name"],
                "distance_meters": round(separation, 1) if separation is not None else None,
                "name_similarity": round(score, 4),
                "exact_name": exact_name,
                "shared_short_name": shared_id,
                "same_name_class": same_class,
                "match_method": method,
                "rank": rank,
                "accepted": False,
            }
        )
    return candidates


def resolve_matches(stations: list[dict], current: list[dict]) -> tuple[list[dict], dict]:
    candidates = [c for station in stations for c in matching_candidates(station, current)]
    by_reference = defaultdict(list)
    for candidate in candidates:
        if candidate["rank"] < 99:
            by_reference[(candidate["source"], candidate["station_key"])].append(candidate)
    proposals = defaultdict(list)
    for rows in by_reference.values():
        best = min(c["rank"] for c in rows)
        eligible = [c for c in rows if c["rank"] == best]
        if len(eligible) == 1:
            candidate = eligible[0]
            proposals[(candidate["source"], candidate["feed_station_id"])].append(candidate)
    accepted = {}
    for rows in proposals.values():
        # No trip-volume or nearest-neighbor tie-break can certify duplicate identities.
        if len(rows) == 1:
            candidate = rows[0]
            candidate["accepted"] = True
            accepted[(candidate["source"], candidate["station_key"])] = candidate
    return candidates, accepted


def reconcile(root: Path) -> dict:
    feeds, manifest = load_feeds(root / "data/reference")
    geojson = json.loads((root / "data/reference/chicago_boundary.geojson").read_text())
    boundary = unary_union([shape(f["geometry"]) for f in geojson["features"]])
    current = feed_rows(feeds, boundary)
    with duckdb.connect(str(root / "data/stations.duckdb"), read_only=True) as connection:
        stations = fetch_dicts(
            connection, "SELECT * FROM stations_all ORDER BY station_first_trip_at,station_key"
        )
    candidates, accepted = resolve_matches(stations, current)
    first_by_name = {}
    for station in stations:
        for name in station["name_variants"].split(" | "):
            key = normalize_name(name)
            first_by_name[key] = min(
                first_by_name.get(key, station["station_first_trip_at"]),
                station["station_first_trip_at"],
            )
    feed_index = {(r["source"], r["feed_station_id"]): r for r in current}
    relevant = defaultdict(list)
    for row in candidates:
        if row["rank"] < 99 or row["exact_name"] or row["shared_short_name"]:
            relevant[row["station_key"]].append(row)
    for station in stations:
        for source in ("city", "gbfs"):
            match = accepted.get((source, station["station_key"]))
            feed = feed_index[(source, match["feed_station_id"])] if match is not None else {}
            station.update(
                {
                    f"{source}_station_id": feed.get("feed_station_id"),
                    f"{source}_station_name": feed.get("station_name"),
                    f"{source}_status": feed.get("status"),
                    f"{source}_match_method": match["match_method"] if match is not None else None,
                    f"{source}_match_distance_meters": match["distance_meters"]
                    if match is not None
                    else None,
                }
            )
            if source == "gbfs":
                station["gbfs_station_type"] = feed.get("feed_station_type")
        station["current_feed_match_status"] = (
            "matched"
            if station["city_station_id"] or station["gbfs_station_id"]
            else ("needs_review" if relevant[station["station_key"]] else "no_confirmed_match")
        )
        station["opening_date_status"] = "unverified_first_trip_proxy"
        earliest_name = min(
            first_by_name[normalize_name(n)] for n in station["name_variants"].split(" | ")
        )
        station["earlier_same_name_trip_at"] = (
            earliest_name if earliest_name < station["station_first_trip_at"] else None
        )
        date_review = []
        if station["earlier_same_name_trip_at"] is not None:
            date_review.append("earlier_same_name_history")
        for flag in (
            "name_has_multiple_locations",
            "possible_rename",
            "fewer_than_10_endpoints",
            "location_inferred_from_current_feed",
            "first_archive_day",
        ):
            if flag in (station["quality_flags"] or ""):
                date_review.append(flag)
        station["service_date_review_reasons"] = " | ".join(date_review)
        station["feed_snapshot_date"] = manifest["sources"]["city"]["retrieved_at"][:10]
    reverse = {(c["source"], c["feed_station_id"]): c for c in accepted.values()}
    station_index = {s["station_key"]: s for s in stations}
    coverage = []
    for row in current:
        match = reverse.get((row["source"], row["feed_station_id"]))
        station = station_index[match["station_key"]] if match is not None else {}
        coverage.append(
            {
                **{k: v for k, v in row.items() if k not in {"name_key", "name_class"}},
                "station_key": station.get("station_key"),
                "reference_station_type": station.get("station_type"),
                "station_first_trip_at": station.get("station_first_trip_at"),
                "match_method": match["match_method"] if match is not None else None,
                "coverage_status": "matched"
                if match is not None
                else "no_unambiguous_history_match",
            }
        )
    output = root / "data/processed"
    chicago = [
        s for s in stations if s["in_chicago"] is True and s["station_type"] != "operational"
    ]
    current_stations, current_fields = build_current_stations(
        feeds["city"], feeds["city_metadata"]["columns"], chicago
    )
    write_csv(output / "current_stations.csv", current_stations, current_fields)
    for name, rows in (("stations", chicago), ("stations_all", stations)):
        write_csv(output / f"{name}.csv", rows, list(stations[0]))
    write_csv(output / "current_feed_coverage.csv", coverage, list(coverage[0]))
    candidate_fields = [
        "station_key",
        "reference_name",
        "reference_first_trip_at",
        "reference_last_trip_at",
        "reference_endpoint_count",
        "source",
        "feed_station_id",
        "feed_name",
        "feed_short_name",
        "distance_meters",
        "name_similarity",
        "exact_name",
        "shared_short_name",
        "same_name_class",
        "match_method",
        "rank",
        "accepted",
    ]
    write_csv(output / "current_feed_candidates.csv", candidates, candidate_fields)
    with duckdb.connect(str(root / "data/stations.duckdb")) as connection:
        for name in (
            "stations",
            "stations_all",
            "current_feed_coverage",
            "current_feed_candidates",
            "current_stations",
        ):
            # Force IDs and optional feed fields to text even when an entire fixture column is empty.
            source = output / f"{name}.csv"
            with source.open(encoding="utf-8") as stream:
                headers = stream.readline().strip().split(",")
            text_fields = [
                h
                for h in headers
                if h.endswith("_id")
                or h.endswith("_name")
                or h.endswith("_status")
                or h.endswith("_method")
                or h == "gbfs_station_type"
                or h in {"id", "source_ids", "name_variants", "first_trip_archive"}
            ]
            types = "{" + ",".join(f"{literal(h)}:'VARCHAR'" for h in text_fields) + "}"
            connection.execute(
                f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_csv({literal(str(source))}, header=true, sample_size=-1, types={types})"
            )
    matched_pairs = [(c["source"], c["feed_station_id"]) for c in accepted.values()]
    city_fields = current_fields[: -len(CURRENT_REFERENCE_FIELDS) - 1]
    chicago_index = {station["station_key"]: station for station in chicago}
    checks = {
        "accepted_feed_matches_one_to_one": len(matched_pairs) == len(set(matched_pairs)),
        "every_feed_row_accounted_for": len(coverage)
        == len(feeds["city"]) + len(feeds["information"]["data"]["stations"]),
        "no_fuzzy_only_matches_accepted": all(c["rank"] < 99 for c in accepted.values()),
        "all_opening_dates_explicitly_unverified": all(
            s["opening_date_status"] == "unverified_first_trip_proxy" for s in stations
        ),
        "current_stations_preserve_city_rows_and_columns": (
            len(current_stations) == len(feeds["city"])
            and len({r["id"] for r in current_stations}) == len(current_stations)
            and all(
                row[field] == city_csv_value(raw.get(field))
                for raw, row in zip(feeds["city"], current_stations, strict=True)
                for field in city_fields
            )
        ),
        "current_stations_join_published_reference_exactly": all(
            row["station_key"] in chicago_index
            and chicago_index[row["station_key"]]["city_station_id"] == row["id"]
            and all(
                row[field] == chicago_index[row["station_key"]][field]
                for field in CURRENT_REFERENCE_FIELDS
            )
            if row["reference_matched"]
            else all(row[field] is None for field in CURRENT_REFERENCE_FIELDS)
            for row in current_stations
        ),
    }
    summary = {
        "snapshot_sources": manifest["sources"],
        "city_rows_updated_at": feeds["city_metadata"]["rowsUpdatedAt"],
        "gbfs_information_updated_at": feeds["information"]["last_updated"],
        "gbfs_status_updated_at": feeds["status"]["last_updated"],
        "city_status_counts": dict(Counter(r["status"] for r in coverage if r["source"] == "city")),
        "gbfs_type_counts": dict(
            Counter(r["feed_station_type"] for r in coverage if r["source"] == "gbfs")
        ),
        "feed_coverage": [
            {
                "source": source,
                "rows": len(rows),
                "inside_chicago": sum(r["in_chicago"] is True for r in rows),
                "matched": sum(r["station_key"] is not None for r in rows),
                "inside_chicago_matched": sum(
                    r["in_chicago"] is True and r["station_key"] is not None for r in rows
                ),
            }
            for source in ("city", "gbfs")
            for rows in [[r for r in coverage if r["source"] == source]]
        ],
        "chicago_reference_match_status": dict(
            Counter(s["current_feed_match_status"] for s in chicago)
        ),
        "chicago_reference_types": dict(Counter(s["station_type"] for s in chicago)),
        "current_stations_artifact": {
            "path": "data/processed/current_stations.csv",
            "reference_path": "data/processed/stations.csv",
            "rows": len(current_stations),
            "city_columns": city_fields,
            "city_column_labels": {
                c["fieldName"]: c["name"]
                for c in feeds["city_metadata"]["columns"]
                if c["fieldName"] in city_fields
            },
            "city_column_populated_rows": {
                field: sum(
                    row.get(field) is not None and row.get(field) != "" for row in feeds["city"]
                )
                for field in city_fields
            },
            "matched": sum(row["reference_matched"] for row in current_stations),
            "unmatched": sum(not row["reference_matched"] for row in current_stations),
        },
        "checks": checks,
    }
    (root / "reports/feed_reconciliation.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    if not all(checks.values()):
        raise ValueError("Feed reconciliation checks failed")
    return summary


if __name__ == "__main__":
    print(json.dumps(reconcile(Path(__file__).resolve().parent), indent=2))
