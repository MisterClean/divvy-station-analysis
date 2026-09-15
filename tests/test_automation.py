"""Verify artifact reproducibility and retrieval failure behavior without external downloads."""

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from download import download_one, validate
from readme import update_readme


def test_pinned_hash_is_checked_even_if_etag_matches(tmp_path: Path) -> None:
    path = tmp_path / "sample.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("data.csv", "id,name\n001,Station\n")
    body = path.read_bytes()
    metadata = {"size": len(body), "etag": hashlib.md5(body).hexdigest(), "sha256": "0" * 64}
    with pytest.raises(ValueError, match="Pinned SHA-256"):
        validate(path, metadata)


def test_download_reuses_verified_files_and_publishes_atomically(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("data.csv", "id,name\n001,Station\n")
    body = source.read_bytes()
    metadata = {
        "key": "sample.zip",
        "url": source.as_uri(),
        "size": len(body),
        "etag": hashlib.md5(body).hexdigest(),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    output = tmp_path / "raw"
    output.mkdir()
    assert download_one(metadata, output)["crc_verified"]
    assert (output / "sample.zip").read_bytes() == body
    assert not (output / "sample.zip.part").exists()

    def unexpected_network(*args, **kwargs):
        raise AssertionError("A verified cached ZIP must not trigger a download")

    monkeypatch.setattr("download.urllib.request.urlopen", unexpected_network)
    assert download_one(metadata, output)["sha256"] == metadata["sha256"]


def test_complete_pipeline_from_clean_directory(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    for script in source_root.glob("*.py"):
        shutil.copy2(script, tmp_path / script.name)
    shutil.copytree(source_root / "sql", tmp_path / "sql")
    shutil.copytree(source_root / "data/reference", tmp_path / "data/reference")
    (tmp_path / "README.md").write_text(
        "# Fixture\n<!-- BEGIN GENERATED PROFILE -->\n<!-- END GENERATED PROFILE -->\n",
        encoding="utf-8",
    )
    raw = tmp_path / "data/raw"
    raw.mkdir()
    archive = raw / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "fixture.csv",
            "ride_id,started_at,ended_at,start_station_id,start_station_name,end_station_id,end_station_name,start_lat,start_lng,end_lat,end_lng\n0001,2020-01-01 10:00:00,2020-01-01 10:05:00,001,First station,002,Second station,41.88123,-87.63123,41.89123,-87.63123\n0002,2020-01-01 10:10:00,2020-01-01 10:15:00,002,Second station,001,First station,41.89123,-87.63123,41.88123,-87.63123\n",
        )
    body = archive.read_bytes()
    manifest = {
        "bucket": "https://divvy-tripdata.s3.amazonaws.com/",
        "retrieved_at": "2026-09-15T00:00:00+00:00",
        "download_seconds": 0,
        "objects": [
            {
                "key": "fixture.zip",
                "url": "https://invalid.example/fixture.zip",
                "size": len(body),
                "etag": hashlib.md5(body).hexdigest(),
                "sha256": hashlib.sha256(body).hexdigest(),
                "crc_verified": True,
            }
        ],
    }
    (tmp_path / "data/archive_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    subprocess.run(
        [sys.executable, str(tmp_path / "pipeline.py"), "--offline", "--workers", "2"],
        cwd=source_root,
        check=True,
        capture_output=True,
        text=True,
    )
    with (tmp_path / "data/processed/stations.csv").open(encoding="utf-8") as stream:
        stations = list(csv.DictReader(stream))
    assert {s["station_id"] for s in stations} == {"001", "002"}
    assert (
        next(s for s in stations if s["station_id"] == "002")["station_first_trip_at"]
        == "2020-01-01 10:05:00"
    )
    assert all(json.loads((tmp_path / "reports/validation.json").read_text())["checks"].values())
    with (tmp_path / "data/processed/current_stations.csv").open(encoding="utf-8") as stream:
        current_stations = list(csv.DictReader(stream))
    assert current_stations
    assert all(row["reference_matched"] == "False" for row in current_stations)
    assert all(row["station_first_trip_at"] == "" for row in current_stations)
    readme = (tmp_path / "README.md").read_text()
    assert "candidate public station/location entities" in readme
    assert "(data/processed/stations.csv)" in readme


def test_checked_in_artifact_and_embedded_profile(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "data/processed/stations.csv").open(encoding="utf-8") as stream:
        stations = list(csv.DictReader(stream))
    metrics = json.loads((root / "reports/validation.json").read_text())["metrics"]
    assert len(stations) == metrics["chicago_rows"]
    assert len({row["station_key"] for row in stations}) == len(stations)
    assert all(
        row["station_first_trip_at"] and row["station_lat"] and row["station_lon"]
        for row in stations
    )
    (tmp_path / "reports").mkdir()
    shutil.copy2(root / "README.md", tmp_path / "README.md")
    shutil.copy2(root / "reports/profile.md", tmp_path / "reports/profile.md")
    update_readme(tmp_path)
    assert (tmp_path / "README.md").read_bytes() == (root / "README.md").read_bytes()
