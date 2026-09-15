"""Retrieve, profile, resolve, validate and document the complete Divvy station reference."""

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from readme import update_readme
from station_feeds import load_feeds


def ensure_boundary(root: Path, *, offline: bool) -> None:
    """Use the pinned boundary or recover that exact snapshot from its recorded URL."""
    directory = root / "data/reference"
    metadata = json.loads((directory / "sources.json").read_text(encoding="utf-8"))[
        "chicago_boundary"
    ]
    path = directory / "chicago_boundary.geojson"
    if path.exists():
        body = path.read_bytes()
    elif offline:
        raise FileNotFoundError("The pinned Chicago boundary is required for an offline run")
    else:
        with urllib.request.urlopen(metadata["url"], timeout=120) as response:
            body = response.read()
    if hashlib.sha256(body).hexdigest() != metadata["sha256"]:
        raise ValueError(
            "Chicago boundary differs from the pinned snapshot; review before updating"
        )
    if not path.exists():
        path.write_bytes(body)


def run_pipeline(root: Path, *, workers: int, refresh: bool, offline: bool) -> None:
    if workers < 1:
        raise ValueError("workers must be positive")
    if refresh and offline:
        raise ValueError("refresh and offline cannot be combined")
    ensure_boundary(root, offline=offline)
    load_feeds(root / "data/reference")
    download_args = ["--workers", str(workers)]
    if refresh:
        download_args.append("--refresh")
    if offline:
        download_args.append("--offline")
    steps = [
        ("download.py", download_args),
        ("profile_data.py", []),
        ("build_stations.py", []),
        ("reconcile_stations.py", []),
        ("report.py", []),
        ("verify_sources.py", []),
    ]
    started = time.monotonic()
    for i, (script, arguments) in enumerate(steps, start=1):
        print(f"\n[{i}/{len(steps)}] {script}", flush=True)
        subprocess.run([sys.executable, str(root / script), *arguments], cwd=root, check=True)
    update_readme(root)
    print(
        f"\nPipeline completed in {time.monotonic() - started:.1f}s: {root / 'data/processed/stations.csv'}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers", type=int, default=8, help="Concurrent archive downloads (default: 8)"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="Discover newer ZIPs and rebuild the published snapshot",
    )
    mode.add_argument(
        "--offline",
        action="store_true",
        help="Rebuild using only existing local ZIPs and pinned metadata",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    run_pipeline(
        Path(__file__).resolve().parent,
        workers=args.workers,
        refresh=args.refresh,
        offline=args.offline,
    )


if __name__ == "__main__":
    main()
