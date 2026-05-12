#!/usr/bin/env python3
"""
Enrich every locality in a polling-stations JSON with region + county
(okrug) sub-objects, using the curated mapping in data/.

Usage:
    python3 scripts/build_localities.py <input.json> [<output.json>]

If output is omitted, the input file is rewritten in place.

Region/county catalogue:        data/serbia_admin.json
Locality -> county_id mapping:  data/locality_county_map.json

Fails loudly if any locality is missing from the map — that's how new
localities get caught when the source data is refreshed.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _region_payload(region):
    return {
        "id": region["id"],
        "name_cyr": region["name_cyr"],
        "name_lat": region["name_lat"],
    }


def _county_payload(county):
    return {
        "id": county["id"],
        "name_cyr": county["name_cyr"],
        "name_lat": county["name_lat"],
    }


def enrich(data, admin, loc_map):
    regions = admin["regions"]
    counties = admin["counties"]

    missing = []
    for loc in data.get("localities", []):
        name = loc["name"]
        county_id = loc_map.get(name)
        if county_id is None:
            missing.append(name)
            continue

        if county_id == "ostalo":
            loc["region"] = _region_payload(regions["ostalo"])
            loc["county"] = None
        else:
            county = counties[county_id]
            region = regions[county["region_id"]]
            loc["region"] = _region_payload(region)
            loc["county"] = _county_payload(county)

    if missing:
        raise SystemExit(
            "Unmapped localities found in data/locality_county_map.json:\n  - "
            + "\n  - ".join(missing)
        )

    return data


def main(src, dst):
    admin = _load_json(os.path.join(DATA_DIR, "serbia_admin.json"))
    loc_map = _load_json(os.path.join(DATA_DIR, "locality_county_map.json"))
    data = _load_json(src)

    enrich(data, admin, loc_map)

    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    by_region = {}
    by_county = {}
    for loc in data["localities"]:
        by_region[loc["region"]["id"]] = by_region.get(loc["region"]["id"], 0) + 1
        cid = loc["county"]["id"] if loc["county"] else "—"
        by_county[cid] = by_county.get(cid, 0) + 1

    print(f"Enriched {len(data['localities'])} localities → {dst}")
    print("By region:")
    for r, n in sorted(by_region.items()):
        print(f"  {r:20s} {n:4d}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/build_localities.py <input.json> [<output.json>]", file=sys.stderr)
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) >= 3 else src
    main(src, dst)
