"""Resolve station names and locations without assuming that Divvy's raw IDs are stable."""

import argparse
import csv
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import duckdb
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from profile_data import fetch_dicts, literal

MATCH_DISTANCE_METERS = 150


def normalize_name(name: str | None) -> str:
    if name is None:
        return ""
    value = re.sub(r"\s+", " ", name).strip().casefold()
    # Only remove documented display suffixes, not street direction or rack prefixes.
    value = re.sub(r"\s*\((?:\*|temp)\)\s*$", "", value).strip()
    return value


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    partial = path.with_suffix(path.suffix + ".tmp")
    with partial.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    partial.replace(path)


def read_inputs(profile: dict) -> tuple[list[dict], list[dict]]:
    observations, lookups = [], []
    with duckdb.connect() as connection:
        for member in profile["members"]:
            if member["duplicate_of"] is not None:
                continue
            is_lookup = member["kind"] == "station_lookup"
            path = Path(member["cache_dir"]) / (
                "lookup.parquet" if is_lookup else "observations.parquet"
            )
            rows = fetch_dicts(connection, f"SELECT * FROM read_parquet({literal(str(path))})")
            for row in rows:
                row.update(
                    archive=member["archive"], member=member["member"], schema=member["kind"]
                )
                row["name_key"] = normalize_name(row["station_name"])
            (lookups if is_lookup else observations).extend(rows)
    return observations, lookups


def point(row: dict) -> tuple[float, float] | None:
    if row["lat"] is None or row["lon"] is None:
        return None
    if not (41.4 <= row["lat"] <= 42.3 and -88.1 <= row["lon"] <= -87.3):
        return None
    return row["lat"], row["lon"]


def required_point(row: dict) -> tuple[float, float]:
    coordinate = point(row)
    if coordinate is None:
        raise ValueError("Expected a valid coordinate")
    return coordinate


def recover_names(observations: list[dict]) -> None:
    candidates = defaultdict(dict)
    for row in observations:
        if (
            row["station_id"] is not None
            and row["name_key"]
            and not re.fullmatch(r"[\d.]+", row["name_key"])
        ):
            candidates[(row["archive"], row["station_id"])][row["name_key"]] = row["station_name"]
    for row in observations:
        row["original_station_name"] = row["station_name"]
        row["name_recovered"] = False
        if not row["name_key"] or re.fullmatch(r"[\d.]+", row["name_key"]):
            names = candidates[(row["archive"], row["station_id"])]
            if len(names) == 1:
                row["name_key"], row["station_name"] = next(iter(names.items()))
                row["name_recovered"] = True


def enrich_coordinates(observations: list[dict], lookups: list[dict]) -> None:
    by_pair = defaultdict(list)
    by_id = defaultdict(list)
    for row in lookups:
        if point(row) is not None:
            by_pair[(row["station_id"], row["name_key"])].append(row)
            by_id[row["station_id"]].append(row)
    for row in observations:
        row["coordinate_source_archive"] = row["archive"]
        row["coordinate_dispersion_meters"] = None
        row["coordinate_rejected"] = False
        if row["lat_p05"] is not None:
            spread = distance((row["lat_p05"], row["lon_p05"]), (row["lat_p95"], row["lon_p95"]))
            row["coordinate_dispersion_meters"] = spread
            if spread > 300:
                row["lat"], row["lon"] = None, None
                row["coordinate_rejected"] = True
        row["coordinate_source"] = "trip_endpoint_median" if point(row) is not None else "missing"
        row["coordinate_inferred"] = False
        candidates = by_pair[(row["station_id"], row["name_key"])]
        lookup_method = "historical_station_lookup"
        if not candidates and row["schema"] == "legacy":
            candidates = by_id[row["station_id"]]
            lookup_method = "historical_lookup_id_only"
        # A historic location is safe to attach only when same-ID/name snapshots agree spatially.
        if point(row) is None and candidates:
            anchor = required_point(candidates[0])
            if all(
                distance(anchor, required_point(candidate)) <= MATCH_DISTANCE_METERS
                for candidate in candidates
            ):
                selected = sorted(candidates, key=lambda x: (x["archive"], x["member"]))[-1]
                row["lat"], row["lon"] = selected["lat"], selected["lon"]
                row["coordinate_source"] = lookup_method
                row["coordinate_source_archive"] = selected["archive"]
                row["coordinate_inferred"] = True
    arrivals = {
        (r["archive"], r["member"], r["station_id"], r["name_key"]): r
        for r in observations
        if r["role"] == "end"
        and r["coordinate_count"] >= 3
        and point(r) is not None
        and not r["coordinate_rejected"]
    }
    for row in observations:
        key = (row["archive"], row["member"], row["station_id"], row["name_key"])
        if row["role"] == "start" and key in arrivals:
            end = arrivals[key]
            row["lat"], row["lon"] = end["lat"], end["lon"]
            row["coordinate_source"] = "same_file_arrival_median"
            row["coordinate_source_archive"] = end["coordinate_source_archive"]


def cluster_name(rows: list[dict]) -> list[dict]:
    """Greedy fixed-anchor clusters prevent transitive chains from merging distant stations."""
    located = [r for r in rows if point(r) is not None]
    missing = [r for r in rows if point(r) is None]
    # Frequent observations anchor the clusters; end coordinates are typically dock coordinates.
    located.sort(
        key=lambda r: (
            -r["valid_endpoint_count"],
            r["role"] != "end",
            str(r["first_at"]),
            r["archive"],
            r["station_id"] or "",
        )
    )
    clusters: list[dict] = []
    for row in located:
        candidates = [
            (distance(required_point(row), c["anchor"]), i) for i, c in enumerate(clusters)
        ]
        nearest = min(candidates) if candidates else None
        if nearest is not None and nearest[0] <= MATCH_DISTANCE_METERS:
            clusters[nearest[1]]["rows"].append(row)
        else:
            clusters.append(
                {"anchor": point(row), "rows": [row], "ambiguous_missing_location": False}
            )
    for row in missing:
        matches = [
            c for c in clusters if any(r["station_id"] == row["station_id"] for r in c["rows"])
        ]
        contemporaneous = [
            c
            for c in matches
            if c["anchor"] is not None
            and any(
                r["archive"] == row["archive"] and r["station_id"] == row["station_id"]
                for r in c["rows"]
            )
        ]
        if len(contemporaneous) == 1:
            matches = contemporaneous
        if len(matches) == 1 or (not matches and len(clusters) == 1):
            match = matches[0] if matches else clusters[0]
            row["coordinate_inferred"] = True
            match["rows"].append(row)
        else:
            unlocated: dict | None = next((c for c in clusters if c["anchor"] is None), None)
            if unlocated is None:
                unlocated = {
                    "anchor": None,
                    "rows": [],
                    "ambiguous_missing_location": bool(clusters),
                }
                clusters.append(unlocated)
            unlocated["rows"].append(row)
    return clusters


def summarize_cluster(cluster: dict, name_key: str, boundary, lookups: list[dict]) -> dict:
    rows = cluster["rows"]
    first = min(
        rows, key=lambda r: (r["first_at"], r["first_trip_id"] or "", r["role"], r["archive"])
    )
    named = [r for r in rows if r["station_id"] is not None]
    latest = max(
        named or rows,
        key=lambda r: (
            r["last_at"],
            r["valid_endpoint_count"],
            r["station_id"] or "",
            r["station_name"],
        ),
    )
    coords = [r for r in rows if point(r) is not None]
    # Use a well-supported observation from the most recent calendar month. Prefer arrivals.
    reliable = [r for r in coords if r["coordinate_count"] >= 10 and r["role"] == "end"]
    chosen = (
        max(
            reliable or coords,
            key=lambda r: (
                r["last_at"].strftime("%Y-%m"),
                r["coordinate_count"],
                r["last_at"],
                r["archive"],
            ),
        )
        if coords
        else None
    )
    lat, lon = (chosen["lat"], chosen["lon"]) if chosen is not None else (None, None)
    ids = sorted({r["station_id"] for r in rows if r["station_id"] is not None})
    raw_names = sorted({r["station_name"] for r in rows})
    # Keys are reproducible for the same archive snapshot; source IDs are preserved separately.
    seed = f"{name_key}|{cluster['anchor']}"
    station_key = "divvy_" + hashlib.sha256(seed.encode()).hexdigest()[:16]
    flags = []
    count = sum(r["valid_endpoint_count"] for r in rows)
    if count < 10:
        flags.append("fewer_than_10_endpoints")
    if chosen is None:
        flags.append("missing_coordinates")
    if cluster["ambiguous_missing_location"]:
        flags.append("ambiguous_historical_location")
    if any(r["coordinate_inferred"] for r in rows):
        flags.append("historical_location_inferred")
    if any(r["coordinate_source"] == "historical_lookup_id_only" for r in rows):
        flags.append("historical_coordinates_matched_by_id")
    if any(r["coordinate_rejected"] for r in rows):
        flags.append("scattered_gps_rejected")
    if any(r["name_recovered"] for r in rows):
        flags.append("missing_name_recovered")
    if len(ids) > 1:
        flags.append("multiple_source_ids")
    if not ids:
        flags.append("missing_source_id")
    if first["first_at"].date().isoformat() == "2013-06-27":
        flags.append("first_archive_day")
    # Explicit operational names; temporary public stations and public racks remain eligible.
    nonpublic = (
        re.search(
            r"\b(test(?:ing)?|warehouse|repair|maintenance|bike.checking|divvy mobile|depot)\b|^base\b|^mtv\b|hubbard_test|hastings|chi.watson|map frame",
            name_key + " " + " ".join(ids).casefold(),
        )
        is not None
    )
    if nonpublic:
        flags.append("operational_station")
    in_chicago = boundary.covers(Point(lon, lat)) if chosen is not None else None
    online = [
        r["reported_online_at"]
        for r in lookups
        if r["station_id"] in ids
        and r["name_key"] in {row["name_key"] for row in rows}
        and r["reported_online_at"] is not None
        and (
            cluster["anchor"] is None
            or point(r) is None
            or distance(required_point(r), cluster["anchor"]) <= MATCH_DISTANCE_METERS
        )
    ]
    if len({d.date() for d in online}) > 1:
        flags.append("conflicting_reported_online_dates")
    starts = [r["first_at"] for r in rows if r["role"] == "start"]
    ends = [r["first_at"] for r in rows if r["role"] == "end"]
    return {
        "station_key": station_key,
        "station_id": latest["station_id"],
        "station_name": re.sub(
            r"\s*\((?:\*|Temp)\)\s*$", "", latest["station_name"], flags=re.I
        ).strip(),
        "station_lon": lon,
        "station_lat": lat,
        "station_first_trip_at": first["first_at"],
        "station_first_departure_at": min(starts) if starts else None,
        "station_first_arrival_at": min(ends) if ends else None,
        "station_last_trip_at": max(r["last_at"] for r in rows),
        "reported_online_at": min(online) if online else None,
        "reported_online_at_latest": max(online) if online else None,
        "in_chicago": in_chicago,
        "station_type": "operational"
        if nonpublic
        else ("public_rack" if "public rack" in name_key else "station"),
        "endpoint_count": count,
        "source_id_count": len(ids),
        "source_ids": " | ".join(ids),
        "name_variants": " | ".join(raw_names),
        "quality_flags": " | ".join(flags),
        "first_trip_id": first["first_trip_id"],
        "first_trip_role": first["role"],
        "first_trip_archive": first["archive"],
        "first_trip_member": first["member"],
        "coordinate_source": chosen["coordinate_source"] if chosen is not None else "missing",
        "coordinate_observed_month": chosen["last_at"].strftime("%Y-%m")
        if chosen is not None and not chosen["coordinate_source"].startswith("historical_")
        else None,
        "coordinate_source_archive": chosen.get("coordinate_source_archive", chosen["archive"])
        if chosen is not None
        else None,
        "name_key": name_key,
        "_anchor": cluster["anchor"],
        "_rows": rows,
    }


def merge_nearby_aliases(
    stations: list[dict], boundary, lookups: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Accept name changes only with a shared source ID and locations within 35 meters.

    Keep rack/station conversions and larger relocations for review. Check the full group
    diameter to prevent chains of short links from bridging distant locations.
    """
    by_id = defaultdict(set)
    for i, station in enumerate(stations):
        for source_id in {r["station_id"] for r in station["_rows"] if r["station_id"] is not None}:
            by_id[source_id].add(i)
    parents = list(range(len(stations)))
    groups = {i: {i} for i in parents}

    def root(i: int) -> int:
        while parents[i] != i:
            i = parents[i]
        return i

    accepted = []
    for source_id, indices in sorted(by_id.items()):
        ordered = sorted(indices)
        for i, left_index in enumerate(ordered):
            for right_index in ordered[i + 1 :]:
                a, b = root(left_index), root(right_index)
                if a == b:
                    continue
                members = [stations[j] for j in sorted(groups[a] | groups[b])]
                if len({s["station_type"] for s in members}) != 1 or any(
                    s["station_lat"] is None for s in members
                ):
                    continue
                locations = [(s["station_lat"], s["station_lon"]) for s in members]
                if any(distance(x, y) > 35 for x in locations for y in locations):
                    continue
                accepted.append(
                    {
                        "source_id": source_id,
                        "station_key_a": stations[left_index]["station_key"],
                        "station_name_a": stations[left_index]["station_name"],
                        "station_key_b": stations[right_index]["station_key"],
                        "station_name_b": stations[right_index]["station_name"],
                        "reason": "shared_source_id_and_within_35m",
                    }
                )
                parents[b] = a
                groups[a] |= groups.pop(b)
    output = []
    for indices in groups.values():
        members = [stations[i] for i in sorted(indices)]
        if len(members) == 1:
            output.append(members[0])
            continue
        anchor = min(members, key=lambda s: (s["station_first_trip_at"], s["station_key"]))
        combined = {
            "anchor": anchor["_anchor"],
            "rows": [r for s in members for r in s["_rows"]],
            "ambiguous_missing_location": False,
        }
        station = summarize_cluster(combined, anchor["name_key"], boundary, lookups)
        station["quality_flags"] += " | nearby_name_aliases_merged"
        output.append(station)
    for match in accepted:
        left = next(i for i, s in enumerate(stations) if s["station_key"] == match["station_key_a"])
        anchor = min(
            [stations[i] for i in groups[root(left)]],
            key=lambda s: (s["station_first_trip_at"], s["station_key"]),
        )
        match["merged_station_key"] = anchor["station_key"]
    return output, accepted


def identity_review(stations: list[dict]) -> list[dict]:
    by_id = defaultdict(list)
    for station in stations:
        for station_id in {
            r["station_id"] for r in station["_rows"] if r["station_id"] is not None
        }:
            by_id[station_id].append(station)
    review = []
    seen = set()
    for source_id, matches in sorted(by_id.items()):
        for i, left in enumerate(matches):
            for right in matches[i + 1 :]:
                pair = tuple(sorted((left["station_key"], right["station_key"])))
                if pair in seen:
                    continue
                seen.add(pair)
                separation = None
                if left["station_lat"] is not None and right["station_lat"] is not None:
                    separation = distance(
                        (left["station_lat"], left["station_lon"]),
                        (right["station_lat"], right["station_lon"]),
                    )
                reason = (
                    "shared_id_location_unknown"
                    if separation is None
                    else (
                        "possible_rename_or_rack_conversion"
                        if separation <= MATCH_DISTANCE_METERS
                        else "reused_id_or_relocation"
                    )
                )
                review.append(
                    {
                        "source_id": source_id,
                        "station_key_a": left["station_key"],
                        "station_name_a": left["station_name"],
                        "first_trip_at_a": left["station_first_trip_at"],
                        "station_key_b": right["station_key"],
                        "station_name_b": right["station_name"],
                        "first_trip_at_b": right["station_first_trip_at"],
                        "distance_meters": round(separation, 1) if separation is not None else None,
                        "reason": reason,
                    }
                )
                if reason == "possible_rename_or_rack_conversion":
                    for station in (left, right):
                        if "possible_rename" not in station["quality_flags"]:
                            station["quality_flags"] += " | possible_rename"
    return review


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--allow-partial", action="store_true", help="Exploratory output only")
    args = parser.parse_args()
    reports = args.data_dir.parent / "reports"
    profile = json.loads((reports / "profile.json").read_text(encoding="utf-8"))
    if not profile["complete"] and not args.allow_partial:
        raise ValueError("Profile is incomplete; run download.py and profile_data.py first")
    observations, lookups = read_inputs(profile)
    recover_names(observations)
    enrich_coordinates(observations, lookups)
    boundary_json = json.loads(
        (args.data_dir / "reference/chicago_boundary.geojson").read_text(encoding="utf-8")
    )
    boundary = unary_union([shape(f["geometry"]) for f in boundary_json["features"]])
    by_name = defaultdict(list)
    excluded: list[dict] = []
    for row in observations:
        reason = None
        if row["valid_endpoint_count"] == 0 or row["first_at"] is None:
            reason = "no_valid_trip_time"
        elif not row["name_key"]:
            reason = "missing_station_name"
        elif re.fullmatch(r"[\d.]+", row["name_key"]):
            reason = "numeric_placeholder_name"
        if reason is not None:
            excluded.append({**row, "reason": reason})
        else:
            by_name[row["name_key"]].append(row)
    stations = []
    for name_key, rows in sorted(by_name.items()):
        clusters = cluster_name(rows)
        for cluster in clusters:
            station = summarize_cluster(cluster, name_key, boundary, lookups)
            if len(clusters) > 1:
                station["quality_flags"] += " | name_has_multiple_locations"
            stations.append(station)
    stations, accepted = merge_nearby_aliases(stations, boundary, lookups)
    stations.sort(key=lambda r: (r["station_first_trip_at"], r["station_key"]))
    review = identity_review(stations)
    for station in stations:
        station["quality_flags"] = " | ".join(
            sorted({flag.strip() for flag in station["quality_flags"].split("|") if flag.strip()})
        )
    output = args.data_dir / "processed"
    output.mkdir(parents=True, exist_ok=True)
    fields = [k for k in stations[0] if not k.startswith("_") and k != "name_key"]
    chicago = [
        r for r in stations if r["in_chicago"] is True and r["station_type"] != "operational"
    ]
    write_csv(output / "stations.csv", chicago, fields)
    write_csv(output / "stations_all.csv", stations, fields)
    write_csv(
        output / "accepted_alias_merges.csv",
        accepted,
        [
            "source_id",
            "station_key_a",
            "station_name_a",
            "station_key_b",
            "station_name_b",
            "merged_station_key",
            "reason",
        ],
    )
    write_csv(
        output / "identity_review.csv",
        review,
        [
            "source_id",
            "station_key_a",
            "station_name_a",
            "first_trip_at_a",
            "station_key_b",
            "station_name_b",
            "first_trip_at_b",
            "distance_meters",
            "reason",
        ],
    )
    aliases = []
    for station in stations:
        for row in station["_rows"]:
            aliases.append({"station_key": station["station_key"], **row})
    write_csv(
        output / "station_observations.csv",
        aliases,
        [
            "station_key",
            "station_id",
            "station_name",
            "original_station_name",
            "archive",
            "member",
            "role",
            "first_at",
            "last_at",
            "first_trip_id",
            "endpoint_count",
            "valid_endpoint_count",
            "lat",
            "lon",
            "coordinate_source",
            "coordinate_dispersion_meters",
            "coordinate_rejected",
        ],
    )
    write_csv(
        output / "excluded_observations.csv",
        excluded,
        [
            "station_id",
            "station_name",
            "reason",
            "archive",
            "member",
            "role",
            "first_at",
            "last_at",
            "endpoint_count",
            "valid_endpoint_count",
        ],
    )
    write_csv(
        output / "station_catalog.csv",
        lookups,
        [
            "station_id",
            "station_name",
            "lat",
            "lon",
            "reported_online_at",
            "reported_created_at",
            "archive",
            "member",
        ],
    )
    summary = {
        "complete": profile["complete"],
        "archive_count": len({m["archive"] for m in profile["members"]}),
        "trip_rows": sum(
            m["rows"]
            for m in profile["members"]
            if m["kind"] != "station_lookup" and m["duplicate_of"] is None
        ),
        "all_station_entities": len(stations),
        "chicago_station_entities": len(chicago),
        "operational_entities": sum(s["station_type"] == "operational" for s in stations),
        "outside_chicago_entities": sum(s["in_chicago"] is False for s in stations),
        "missing_location_entities": sum(s["in_chicago"] is None for s in stations),
        "excluded_endpoints": sum(r["endpoint_count"] for r in excluded),
        "match_distance_meters": MATCH_DISTANCE_METERS,
        "accepted_alias_merges": len(accepted),
        "identity_review_pairs": len(review),
    }
    summary["resolved_endpoints"] = sum(r["endpoint_count"] for r in aliases)
    summary["valid_resolved_endpoints"] = sum(r["valid_endpoint_count"] for r in aliases)
    if summary["resolved_endpoints"] + summary["excluded_endpoints"] != 2 * summary["trip_rows"]:
        raise ValueError("Endpoint reconciliation failed")
    if len({s["station_key"] for s in stations}) != len(stations):
        raise ValueError("Duplicate resolved station keys")
    with duckdb.connect(str(args.data_dir / "stations.duckdb")) as connection:
        connection.execute("BEGIN")
        for name in (
            "stations",
            "stations_all",
            "station_observations",
            "station_catalog",
            "identity_review",
            "accepted_alias_merges",
            "excluded_observations",
        ):
            source = output / f"{name}.csv"
            string_fields = {"station_id", "first_trip_id", "source_id"}
            with source.open(encoding="utf-8", newline="") as stream:
                headers = next(csv.reader(stream))
            types = (
                "{"
                + ",".join(f"{literal(h)}:'VARCHAR'" for h in headers if h in string_fields)
                + "}"
            )
            connection.execute(
                f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM read_csv({literal(str(source))}, header=true, sample_size=-1, types={types})"
            )
        connection.execute("COMMIT")
        connection.execute(
            f"COPY station_observations TO {literal(str(output / 'station_observations.parquet'))} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    (reports / "build_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
