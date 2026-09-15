"""Pin current city and operator station feeds separately from trip-archive refreshes."""

import argparse
import hashlib
import json
import math
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

FEED_URLS = {
    "city": "https://data.cityofchicago.org/resource/bbyy-e7gq.json?$limit=10000&$order=id",
    "city_metadata": "https://data.cityofchicago.org/api/views/bbyy-e7gq.json",
    "information": "https://gbfs.divvybikes.com/gbfs/en/station_information.json",
    "status": "https://gbfs.divvybikes.com/gbfs/en/station_status.json",
}


def validate_feeds(feeds: dict) -> None:
    for name, id_field, lat_field, lon_field in (
        ("city", "id", "latitude", "longitude"),
        ("information", "station_id", "lat", "lon"),
    ):
        rows = feeds[name] if name == "city" else feeds[name]["data"]["stations"]
        if not rows or len(rows) >= 10000:
            raise ValueError(f"Empty or potentially truncated {name} feed")
        if len({r[id_field] for r in rows}) != len(rows):
            raise ValueError(f"Duplicate IDs in {name} feed")
        for row in rows:
            lat, lon = float(row[lat_field]), float(row[lon_field])
            if (
                not math.isfinite(lat)
                or not math.isfinite(lon)
                or not (-90 <= lat <= 90 and -180 <= lon <= 180)
            ):
                raise ValueError(f"Invalid coordinates in {name}")
    statuses = feeds["status"]["data"]["stations"]
    if len({r["station_id"] for r in statuses}) != len(statuses):
        raise ValueError("Duplicate status IDs")
    if {r["station_id"] for r in statuses} != {
        r["station_id"] for r in feeds["information"]["data"]["stations"]
    }:
        raise ValueError("Operator information/status IDs differ; retry the snapshot")


def refresh_feeds(directory: Path) -> dict:
    """Write content-addressed responses, publishing the manifest only after validation."""
    bodies, feeds, sources = {}, {}, {}
    for name, url in FEED_URLS.items():
        with urllib.request.urlopen(url, timeout=60) as response:
            bodies[name] = response.read()
        feeds[name] = json.loads(bodies[name])
        digest = hashlib.sha256(bodies[name]).hexdigest()
        sources[name] = {
            "url": url,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "path": f"feeds/{name}-{digest[:16]}.json",
            "sha256": digest,
        }
    validate_feeds(feeds)
    (directory / "feeds").mkdir(parents=True, exist_ok=True)
    for name, body in bodies.items():
        (directory / sources[name]["path"]).write_bytes(body)
    manifest = {"sources": sources}
    partial = directory / "station_feeds.json.tmp"
    partial.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    partial.replace(directory / "station_feeds.json")
    return manifest


def load_feeds(directory: Path) -> tuple[dict, dict]:
    manifest = json.loads((directory / "station_feeds.json").read_text(encoding="utf-8"))
    feeds = {}
    for name, source in manifest["sources"].items():
        body = (directory / source["path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != source["sha256"]:
            raise ValueError(f"Pinned {name} feed hash mismatch")
        feeds[name] = json.loads(body)
    validate_feeds(feeds)
    return feeds, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Explicitly replace feed snapshot")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent / "data/reference"
    if args.refresh:
        refresh_feeds(directory)
    feeds, manifest = load_feeds(directory)
    print(
        json.dumps(
            {
                "city_rows": len(feeds["city"]),
                "operator_rows": len(feeds["information"]["data"]["stations"]),
                "sources": manifest["sources"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
