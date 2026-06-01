"""
Download all raw data needed by the pipeline.

Sources:
  - Geofabrik Belgium OSM PBF  (~500 MB, stable URL)
  - Statbel statistical sectors 2024 (SpatiaLite zip, ~10 MB)
  - Statbel population by sector 2024 (zip, ~5 MB)
  - STIB GTFS (manual download required — see instructions below)

Run:
  cd backend/pipeline
  python 01_download.py

STIB GTFS manual download:
  1. Go to https://opendata.stib-mivb.be/
  2. Datasets → "GTFS Files (Production)" → Download ZIP
  3. Save as: backend/pipeline/data/raw/stib_gtfs.zip
  Or set env var: STIB_GTFS_URL=<direct-zip-url>
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import DATA_RAW, DOWNLOAD_SOURCES


def _download(url: str, dest: Path) -> None:
    """Stream-download url → dest with MB progress."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, stream=True, timeout=300, headers={"User-Agent": "neighbourhood-fit-score/1.0"})
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                mb_done = downloaded // 1_048_576
                mb_total = total // 1_048_576
                print(f"\r  ↓ {dest.name}  {mb_done}/{mb_total} MB ({pct:.0f}%)", end="", flush=True)
    print(f"\r  ✓ {dest.name}  {downloaded // 1_048_576} MB          ")


def _extract_zip(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Extract zip contents → dest_dir, return list of extracted paths."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(zip_path) as z:
        members = z.namelist()
        print(f"  📦 {zip_path.name}: {len(members)} file(s)")
        for member in members:
            # Flatten directory structure — extract files to dest_dir directly
            name = Path(member).name
            if not name:  # directory entry
                continue
            target = dest_dir / name
            with z.open(member) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(target)
            print(f"     → {name}")
    return extracted


def _done_marker(filename: str) -> Path:
    return DATA_RAW / f".{filename}.done"


def fetch(key: str) -> bool:
    """Download and extract one source. Returns True if available after."""
    url, filename = DOWNLOAD_SOURCES[key]
    dest = DATA_RAW / filename
    marker = _done_marker(filename)

    if marker.exists():
        print(f"  ✓ {filename} — already done, skipping")
        return True

    # For non-zip files that already exist
    if dest.exists() and not filename.endswith(".zip"):
        print(f"  ✓ {filename} — file present, skipping download")
        marker.touch()
        return True

    # Resolve URL
    if url is None:
        url = os.getenv(f"{key.upper()}_URL")

    if url is None:
        if dest.exists():
            print(f"  ✓ {filename} — found manually")
            if filename.endswith(".zip"):
                print(f"  📦 Extracting {filename} …")
                _extract_zip(dest, DATA_RAW)
            marker.touch()
            return True
        else:
            _print_manual_instructions(key, filename)
            return False

    # Download
    if not dest.exists():
        print(f"  Downloading {filename} …")
        try:
            _download(url, dest)
        except requests.HTTPError as e:
            print(f"\n  ✗ HTTP {e.response.status_code} for {filename}")
            _print_fallback(key, url, dest)
            return False
        except Exception as e:
            print(f"\n  ✗ {e}")
            _print_fallback(key, url, dest)
            return False

    # Extract
    if filename.endswith(".zip"):
        print(f"  Extracting {filename} …")
        try:
            _extract_zip(dest, DATA_RAW)
        except zipfile.BadZipFile as e:
            print(f"  ✗ Bad zip: {e}. Re-download the file manually.")
            dest.unlink(missing_ok=True)
            return False

    marker.touch()
    return True


def _print_manual_instructions(key: str, filename: str) -> None:
    dest = DATA_RAW / filename
    print(f"\n  ⚠  {filename} — no URL configured")
    if key == "stib_gtfs":
        print("     Download STIB GTFS manually:")
        print("     1. https://opendata.stib-mivb.be/")
        print("     2. Datasets → 'GTFS Files (Production)' → Download")
        print(f"     3. Save as: {dest}")
        print("     Or: set env var STIB_GTFS_URL=<direct-zip-url>")
    elif key == "statbel_sectors":
        print("     https://statbel.fgov.be/en/open-data/statistical-sectors-2024")
        print(f"     File: sh_statbel_statistical_sectors_31370_20240101.sqlite.zip → {dest}")
    elif key == "statbel_population":
        print("     https://statbel.fgov.be/en/open-data/population-statistical-sector-2024")
        print(f"     File: OPENDATA_SECTOREN_2024.zip → {dest}")


def _print_fallback(key: str, url: str, dest: Path) -> None:
    print(f"    URL tried: {url}")
    print(f"    Download manually → {dest}")
    _print_manual_instructions(key, dest.name)


def main() -> None:
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    print(f"Raw data directory: {DATA_RAW}\n")

    results: dict[str, bool] = {}
    for key in DOWNLOAD_SOURCES:
        results[key] = fetch(key)
        print()

    print("─" * 50)
    ok = [k for k, v in results.items() if v]
    missing = [k for k, v in results.items() if not v]

    if ok:
        print(f"  ✓ Ready:   {', '.join(ok)}")
    if missing:
        print(f"  ⚠  Missing: {', '.join(missing)}")
        if "stib_gtfs" in missing:
            print("    STIB GTFS is required for transit scoring (Week 3).")
            print("    Pipeline steps 02–04 can run without it.")

    print("\nNext: python 02_sectors.py")


if __name__ == "__main__":
    main()
