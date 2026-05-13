#!/usr/bin/env python3
"""
Resolve maps.app.goo.gl shortlinks in data/sve_opstine_normalized.json into
real lat/lon coordinates, then re-emit the normalized CSV/JSON/XLSX.

For each row where `lat` is None and `gmaps_url` is a shortlink (or where
`coord_source_format == "shortlink_pending"`), follow HTTP redirects and
extract coordinates from the resolved URL. Two patterns are common:

  1. `/@lat,lon,zoom` after the `/place/` segment.
  2. `!8m2!3d{lat}!4d{lon}` or `!3d{lat}!4d{lon}` in the data= path.

Resolutions are cached in data/sve_opstine_goo_gl_cache.json so reruns are
incremental and cheap.

Usage:
    python3 scripts/resolve_goo_gl.py
    python3 scripts/resolve_goo_gl.py --limit 50 --sleep 1.0
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import openpyxl
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_sve_opstine import write_report as _write_report  # noqa: E402


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_DATA_DIR = REPO / "data"

SHORTLINK_RE = re.compile(r"https?://maps\.app\.goo\.gl/", re.IGNORECASE)
COORD_AT_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")
COORD_3D4D_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")

SERBIA_BBOX_LAT = (41.5, 46.5)
SERBIA_BBOX_LON = (18.5, 23.5)

USER_AGENT = (
    "Mozilla/5.0 (compatible; biracka-mesta-normalizer/1.0; "
    "+https://github.com/nemdub/biracka-mesta)"
)


def extract_coords_from_resolved_url(url: str):
    """Return (lat, lon) or (None, None). Prefer @lat,lon (view center);
    fall back to !3d!4d (precise place pin).
    """
    if not url:
        return None, None
    m = COORD_3D4D_RE.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = COORD_AT_RE.search(url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def resolve_shortlink(url: str, timeout: float = 15.0):
    """Return the final URL after redirects, or None on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return None


# Mirror the writer functions from normalize_sve_opstine.py
CSV_COLUMNS = [
    "row_excel", "opstina_cyr", "opstina_lat", "opstina_raw", "rb",
    "name_cyr", "name_lat", "address", "area",
    "lat", "lon", "coord_source_format", "coord_raw",
    "gmaps_url", "gmaps_url_resolved",
    "map_confirmed", "note",
    "signal_mts", "signal_yettel", "signal_a1",
    "signal_mts_raw", "signal_yettel_raw", "signal_a1_raw",
    "extra_note",
]


def write_csv(records, path):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k) for k in CSV_COLUMNS})


def write_json(records, path):
    public = [{k: r.get(k) for k in CSV_COLUMNS} for r in records]
    with path.open("w", encoding="utf-8") as f:
        json.dump(public, f, ensure_ascii=False, indent=2)


def write_xlsx(records, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "polling_stations"
    ws.append(CSV_COLUMNS)
    for r in records:
        ws.append([r.get(c) for c in CSV_COLUMNS])
    wb.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    ap.add_argument("--sleep", type=float, default=0.7,
                    help="Seconds between requests (default 0.7)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N new resolutions (0 = no limit)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    json_path = data_dir / "sve_opstine_normalized.json"
    cache_path = data_dir / "sve_opstine_goo_gl_cache.json"
    if not json_path.exists():
        sys.exit(f"not found: {json_path} — run scripts/normalize_sve_opstine.py first")

    with json_path.open(encoding="utf-8") as f:
        records = json.load(f)

    cache = {}
    if cache_path.exists():
        with cache_path.open(encoding="utf-8") as f:
            cache = json.load(f)

    # Collect rows that need resolving
    candidates = []
    for r in records:
        if r.get("lat") is not None:
            continue
        url = r.get("gmaps_url")
        if not url:
            url = r.get("coord_raw") if r.get("coord_source_format") == "shortlink_pending" else None
        if url and SHORTLINK_RE.search(url):
            candidates.append((r, url))

    print(f"Candidates with shortlinks needing resolution: {len(candidates)}")
    print(f"Cache entries: {len(cache)}")

    new_resolutions = 0
    new_coords = 0
    by_url_cache_hit = 0
    skipped_no_coords = 0

    for r, url in candidates:
        if url in cache:
            entry = cache[url]
            by_url_cache_hit += 1
        else:
            resolved = resolve_shortlink(url)
            entry = {"resolved_url": resolved}
            if resolved:
                lat, lon = extract_coords_from_resolved_url(resolved)
                if lat is not None and lon is not None:
                    entry["lat"] = lat
                    entry["lon"] = lon
            cache[url] = entry
            new_resolutions += 1
            time.sleep(args.sleep)

        # Apply to record
        r["gmaps_url_resolved"] = entry.get("resolved_url")
        lat = entry.get("lat")
        lon = entry.get("lon")
        if lat is not None and lon is not None:
            if SERBIA_BBOX_LAT[0] <= lat <= SERBIA_BBOX_LAT[1] and \
               SERBIA_BBOX_LON[0] <= lon <= SERBIA_BBOX_LON[1]:
                r["lat"] = lat
                r["lon"] = lon
                r["coord_source_format"] = "from_gmaps_shortlink"
                new_coords += 1
            else:
                skipped_no_coords += 1
        else:
            skipped_no_coords += 1

        if args.limit and new_resolutions >= args.limit:
            print(f"Hit --limit={args.limit}; stopping")
            break

    # Persist cache + re-emit outputs
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    write_csv(records, data_dir / "sve_opstine_normalized.csv")
    write_json(records, data_dir / "sve_opstine_normalized.json")
    write_xlsx(records, data_dir / "sve_opstine_normalized.xlsx")

    # Regenerate the markdown report with the updated coords
    json_names = set()
    locality_json = data_dir.parent / "polling_stations_86.json"
    if locality_json.exists():
        with locality_json.open(encoding="utf-8") as f:
            j = json.load(f)
        json_names = {loc["name"] for loc in j.get("localities", [])}
    source_xlsx = data_dir.parent / "Sve_Opstine_Spojeno 8.maj.2026.xlsx"
    _write_report(records, json_names, source_xlsx,
                  data_dir / "sve_opstine_normalize_report.md")

    total = len(records)
    with_coords = sum(1 for r in records if r.get("lat") is not None)
    print(f"  cache hits: {by_url_cache_hit}")
    print(f"  new lookups: {new_resolutions}")
    print(f"  recovered coords: {new_coords}")
    print(f"  no coords in resolved URL: {skipped_no_coords}")
    print(f"  total records: {total}")
    print(f"  total with coords: {with_coords} ({with_coords*100//max(total,1)}%)")
    print(f"  cache file: {cache_path}")


if __name__ == "__main__":
    main()
