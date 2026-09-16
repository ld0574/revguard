#!/usr/bin/env python3
"""Fetch a registered public dataset without modifying the source archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "public" / "datasets.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_dataset(dataset_id: str) -> dict:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for dataset in registry["datasets"]:
        if dataset["dataset_id"] == dataset_id:
            return dataset
    raise ValueError(f"unknown dataset: {dataset_id}")


def fetch(dataset: dict, output_dir: Path, *, force: bool = False) -> dict:
    if dataset["dataset_id"] != "olist-brazilian-ecommerce":
        raise ValueError("only the Olist archive has an automated fetch endpoint")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = output_dir / "olist-brazilian-ecommerce.zip"

    with request.urlopen(dataset["metadata_url"], timeout=30) as response:  # nosec B310
        metadata = json.load(response)
    observed_license = metadata.get("licenseName")
    if observed_license != dataset["license"]:
        raise RuntimeError(
            f"dataset license changed: expected {dataset['license']}, got {observed_license}"
        )

    if force or not archive.exists():
        descriptor, temp_name = tempfile.mkstemp(prefix="olist-", suffix=".zip", dir=output_dir)
        try:
            with os.fdopen(descriptor, "wb") as target:
                with request.urlopen(dataset["download_url"], timeout=120) as response:  # nosec B310
                    while chunk := response.read(1024 * 1024):
                        target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temp_name, archive)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    archive.chmod(0o600)

    manifest = {
        "schema_version": "1.0",
        "dataset_id": dataset["dataset_id"],
        "title": dataset["title"],
        "classification": dataset["classification"],
        "source_url": dataset["source_url"],
        "download_url": dataset["download_url"],
        "license": observed_license,
        "license_url": dataset["license_url"],
        "source_last_updated": metadata.get("lastUpdated"),
        "source_reported_bytes": metadata.get("totalBytes"),
        "archive_file": archive.name,
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256(archive),
        "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "redistribution": dataset["redistribution"],
    }
    manifest_path = output_dir / "source-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", default="olist-brazilian-ecommerce")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifest = fetch(load_dataset(args.dataset_id), args.output_dir, force=args.force)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
