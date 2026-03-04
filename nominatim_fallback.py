#!/usr/bin/env python3
"""
Nominatim fallback geocoder for polling stations with geo: null.

Reads polling_stations_86.json, queries the OSM Nominatim API for each
unmatched station, and writes coordinates back with geo_source: "nominatim".

Usage:
    python nominatim_fallback.py [--json polling_stations_86.json]

Nominatim policy: max 1 request/second, meaningful User-Agent header required.
Cache is persisted at output/tmp/nominatim_cache.json to allow resuming.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

JSON_FILE   = Path("polling_stations_86.json")
CACHE_FILE  = Path("output/tmp/nominatim_cache.json")
USER_AGENT  = "biracki-spisak-geocoder/1.0 (github.com/nemdub/biracki-spisak)"
NOMINATIM   = "https://nominatim.openstreetmap.org/search"
SLEEP_SEC   = 1.1   # slightly over 1 s to be safe


# ---------------------------------------------------------------------------
# Serbian Cyrillic → Latin transliteration table
# Used to convert municipality/street names to Latin for the API query.
# ---------------------------------------------------------------------------
_CYR_TO_LAT = str.maketrans({
    "А": "A",  "а": "a",
    "Б": "B",  "б": "b",
    "В": "V",  "в": "v",
    "Г": "G",  "г": "g",
    "Д": "D",  "д": "d",
    "Ђ": "Đ",  "ђ": "đ",
    "Е": "E",  "е": "e",
    "Ж": "Ž",  "ж": "ž",
    "З": "Z",  "з": "z",
    "И": "I",  "и": "i",
    "Ј": "J",  "ј": "j",
    "К": "K",  "к": "k",
    "Л": "L",  "л": "l",
    "Љ": "Lj", "љ": "lj",
    "М": "M",  "м": "m",
    "Н": "N",  "н": "n",
    "Њ": "Nj", "њ": "nj",
    "О": "O",  "о": "o",
    "П": "P",  "п": "p",
    "Р": "R",  "р": "r",
    "С": "S",  "с": "s",
    "Т": "T",  "т": "t",
    "Ћ": "Ć",  "ћ": "ć",
    "У": "U",  "у": "u",
    "Ф": "F",  "ф": "f",
    "Х": "H",  "х": "h",
    "Ц": "C",  "ц": "c",
    "Ч": "Č",  "ч": "č",
    "Џ": "Dž", "џ": "dž",
    "Ш": "Š",  "ш": "š",
})


def cyr_to_lat(s: str) -> str:
    """Transliterate Serbian Cyrillic to Latin."""
    return s.translate(_CYR_TO_LAT)


# ---------------------------------------------------------------------------
# Station name parsing (minimal copy – only what we need for query building)
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"^(.+?)\s+(?:(?:br|бр|број|broj)\.?\s*)?(\d+\S*)$", re.IGNORECASE)
_STREET_PREFIX_RE = re.compile(
    r"^(?:ул(?:ица)?\.?|тг\.?|трг\.?|бул(?:евар)?\.?|бб\.?)\s+",
    re.IGNORECASE | re.UNICODE,
)


def _parse(name: str) -> tuple[str | None, str | None, str | None]:
    """Return (settlement, street, number) from a station name."""
    if "," in name:
        before_sep, address_part = name.rsplit(",", 1)
        settlement = before_sep.rsplit(" - ", 1)[1].strip() if " - " in before_sep else None
    elif " - " in name:
        before_sep, address_part = name.rsplit(" - ", 1)
        settlement = before_sep.rsplit(" - ", 1)[1].strip() if " - " in before_sep else None
    else:
        return None, None, None

    address_part = address_part.strip()
    if not address_part:
        return settlement or None, None, None

    m = _NUMBER_RE.match(address_part)
    if m:
        street = _STREET_PREFIX_RE.sub("", m.group(1).strip()).strip()
        return settlement or None, street, m.group(2).strip()
    street = _STREET_PREFIX_RE.sub("", address_part).strip()
    return settlement or None, street, None


# ---------------------------------------------------------------------------
# Nominatim query builder
# ---------------------------------------------------------------------------
def _build_url(street: str, number: str | None, municipality: str) -> str:
    """Build a structured Nominatim search URL."""
    street_lat = cyr_to_lat(street)
    muni_lat   = cyr_to_lat(municipality)

    # Strip compound municipality suffixes like "- ГРАД", "-БАРАЈЕВО"
    muni_clean = re.sub(r"[-–]\s*\S+$", "", muni_lat).strip()

    params: dict[str, str] = {
        "format":        "jsonv2",
        "limit":         "1",
        "country":       "Serbia",
        "city":          muni_clean,
    }
    if number:
        params["street"] = f"{number} {street_lat}"
    else:
        params["street"] = street_lat

    return f"{NOMINATIM}?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def _fetch(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def _load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Nominatim fallback geocoder")
    parser.add_argument("--json", default=str(JSON_FILE), help="Path to polling stations JSON")
    args = parser.parse_args()

    json_path = Path(args.json)
    if not json_path.exists():
        raise SystemExit(f"Not found: {json_path}")

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    cache = _load_cache()
    print(f"Cache loaded: {len(cache)} entries.", flush=True)

    total_null = matched = skipped_embassy = already_cached = errors = 0

    for community in data["communities"]:
        community_name = community["name"]

        for station in community["polling_stations"]:
            if station.get("geo") is not None:
                continue  # already geocoded

            total_null += 1
            name = station.get("name", "")

            # Skip foreign/embassy stations
            if "ИНОСТРАНСТВО" in name or "иностранство" in name.lower():
                skipped_embassy += 1
                continue

            settlement, street, number = _parse(name)
            if not street:
                continue

            url = _build_url(street, number, community_name)

            # Use cache if available
            if url in cache:
                result = cache[url]
                already_cached += 1
            else:
                time.sleep(SLEEP_SEC)
                try:
                    results = _fetch(url)
                    result = results[0] if results else None
                except Exception as exc:
                    print(f"  [ERROR] {url}: {exc}", flush=True)
                    errors += 1
                    result = None
                cache[url] = result
                _save_cache(cache)

            if result:
                lat = float(result["lat"])
                lon = float(result["lon"])
                # Sanity check: must be within rough Serbia bounding box
                if 41.8 <= lat <= 46.2 and 18.8 <= lon <= 23.1:
                    station["geo"] = {"lat": round(lat, 7), "lon": round(lon, 7)}
                    station["geo_source"] = "nominatim"
                    matched += 1
                else:
                    print(
                        f"  [OUT OF BOUNDS] {community_name} | {street} {number or ''}: "
                        f"lat={lat}, lon={lon}",
                        flush=True,
                    )

    print(
        f"\nDone: {matched} newly matched via Nominatim "
        f"({already_cached} from cache, {errors} errors, {skipped_embassy} embassy skipped).",
        flush=True,
    )
    print(f"Total null stations processed: {total_null}", flush=True)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Written to {json_path}")


if __name__ == "__main__":
    main()
