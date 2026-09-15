"""Discover and download the public S3 archive, with bounded concurrency and verification."""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

BUCKET = "https://divvy-tripdata.s3.amazonaws.com/"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


def discover() -> list[dict]:
    objects = []
    token = None
    while True:
        params = {"list-type": "2"}
        if token is not None:
            params["continuation-token"] = token
        with urllib.request.urlopen(BUCKET + "?" + urllib.parse.urlencode(params), timeout=60) as r:
            root = ET.fromstring(r.read())
        for item in root.findall("s:Contents", NS):
            key = item.findtext("s:Key", namespaces=NS)
            if key is not None and key.lower().endswith(".zip"):
                size = item.findtext("s:Size", namespaces=NS)
                etag = item.findtext("s:ETag", namespaces=NS)
                if size is None or etag is None:
                    raise ValueError(f"Missing S3 metadata for {key}")
                objects.append(
                    {
                        "key": key,
                        "url": BUCKET + urllib.parse.quote(key),
                        "size": int(size),
                        "etag": etag.strip('"'),
                        "last_modified": item.findtext("s:LastModified", namespaces=NS),
                    }
                )
        if root.findtext("s:IsTruncated", namespaces=NS) != "true":
            return sorted(objects, key=lambda x: x["key"])
        token = root.findtext("s:NextContinuationToken", namespaces=NS)


def validate(path: Path, item: dict) -> dict:
    if path.stat().st_size != item["size"]:
        raise ValueError(f"Wrong byte count for {path.name}")
    with path.open("rb") as stream:
        md5 = hashlib.file_digest(stream, "md5").hexdigest()
    # A single-part S3 ETag is an MD5; multipart ETags are not.
    if "-" not in item["etag"] and md5 != item["etag"]:
        raise ValueError(f"ETag mismatch for {path.name}")
    with zipfile.ZipFile(path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError(f"ZIP CRC failure: {path.name}/{bad_member}")
    with path.open("rb") as stream:
        sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    if item.get("sha256") is not None and sha256 != item["sha256"]:
        raise ValueError(
            f"Pinned SHA-256 mismatch for {path.name}; use --refresh for changed sources"
        )
    return {**item, "sha256": sha256, "crc_verified": True}


def download_one(item: dict, directory: Path) -> dict:
    # Flatten names only after rejecting collisions in main(). Never extract ZIP paths here.
    destination = directory / Path(item["key"]).name
    if destination.exists():
        try:
            return validate(destination, item)
        except (ValueError, zipfile.BadZipFile) as error:
            print(f"Redownloading {destination.name}: {error}", flush=True)
    partial = destination.with_suffix(".zip.part")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(item["url"], timeout=120) as r, partial.open("wb") as out:
                while chunk := r.read(1024 * 1024):
                    out.write(chunk)
            result = validate(partial, item)
            partial.replace(destination)
            return result
        except (OSError, urllib.error.URLError, ValueError, zipfile.BadZipFile) as error:
            if attempt == 3:
                raise RuntimeError(f"Failed to download {item['key']}") from error
            print(f"Retry {attempt + 1}: {item['key']}: {error}", flush=True)
            time.sleep(2**attempt)
    raise AssertionError("Unreachable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="Discover the latest archive snapshot")
    mode.add_argument(
        "--offline", action="store_true", help="Verify existing local ZIPs without network access"
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    manifest_path = args.data_dir / "archive_manifest.json"
    fresh = args.refresh or not manifest_path.exists()
    if args.offline and fresh:
        parser.error("Offline mode requires an existing archive manifest")
    if fresh:
        objects = discover()
        manifest: dict = {
            "bucket": BUCKET,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "objects": objects,
        }
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        objects = manifest["objects"]
    if not objects:
        raise ValueError("No archive ZIPs found")
    if len({Path(x["key"]).name for x in objects}) != len(objects):
        raise ValueError("S3 keys collide when flattened")
    directory = args.data_dir / "raw"
    directory.mkdir(parents=True, exist_ok=True)
    print(
        f"Retrieving/verifying {len(objects)} ZIPs, {sum(x['size'] for x in objects) / 1e9:.2f} GB",
        flush=True,
    )
    started = time.monotonic()
    verified = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        if args.offline:
            futures = [
                pool.submit(validate, directory / Path(item["key"]).name, item) for item in objects
            ]
        else:
            futures = [pool.submit(download_one, item, directory) for item in objects]
        for future in as_completed(futures):
            result = future.result()
            verified.append(result)
            print(f"[{len(verified)}/{len(objects)}] {result['key']}", flush=True)
    elapsed = round(time.monotonic() - started, 2)
    # Reproduction leaves snapshot metadata unchanged; refresh publishes a new manifest atomically.
    if fresh:
        manifest["objects"] = sorted(verified, key=lambda x: x["key"])
        manifest["download_seconds"] = elapsed
        partial = manifest_path.with_suffix(".json.tmp")
        partial.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        partial.replace(manifest_path)
    print(f"All archives verified in {elapsed} seconds", flush=True)


if __name__ == "__main__":
    main()
