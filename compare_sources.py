"""
Compare polling stations between two sources:
  1. biracka_mesta_consolidated.xlsx    — the consolidated working spreadsheet
  2. polling_stations_86_with_geolocation_and_results.json — the geocoded + results JSON

Reports:
  - Counts: total in each source, intersection, only-in-xlsx, only-in-JSON
  - Per-community discrepancies (counts mismatch, station-id mismatch)
  - For shared stations: voter_count mismatches, geolocation drift, community-id mismatches
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

REPO = Path(__file__).resolve().parent
XLSX_PATH = REPO / "biracka_mesta_consolidated.xlsx"
JSON_PATH = REPO / "polling_stations_86_with_geolocation_and_results.json"

DEFAULT_GEO_TOLERANCE_M = 100.0  # meters — drift above this is flagged
EARTH_RADIUS_M = 6_371_008.8


def load_xlsx(path: Path):
    """Return dict[station_id] -> record, plus community map."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    idx = {name: i for i, name in enumerate(header)}

    stations = {}
    communities = defaultdict(set)  # opstina_id -> set of bm_ids
    community_names = {}

    for row in rows:
        if row is None or all(c is None for c in row):
            continue
        bm_id = row[idx["bm_id"]]
        if bm_id is None:
            continue
        bm_id = str(bm_id).strip()
        opstina_id = row[idx["opstina_id"]]
        opstina_id = str(opstina_id).strip() if opstina_id is not None else None
        opstina_latin = row[idx["opstina_latin"]]
        lat = row[idx["lat"]]
        lon = row[idx["lon"]]
        voter_count = row[idx["voter_count"]]
        try:
            voter_count = int(voter_count) if voter_count is not None else None
        except (TypeError, ValueError):
            pass
        try:
            lat = float(lat) if lat is not None else None
            lon = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            lat = lon = None

        if bm_id in stations:
            stations[bm_id]["duplicate"] = True
        stations[bm_id] = {
            "bm_id": bm_id,
            "opstina_id": opstina_id,
            "opstina_latin": opstina_latin,
            "bm_name": row[idx["bm_name"]],
            "lat": lat,
            "lon": lon,
            "voter_count": voter_count,
        }
        if opstina_id is not None:
            communities[opstina_id].add(bm_id)
            if opstina_latin:
                community_names.setdefault(opstina_id, opstina_latin)

    return stations, communities, community_names


def load_json(path: Path):
    """Return dict[station_id] -> record, plus community map."""
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    stations = {}
    communities = defaultdict(set)
    community_names = {}

    for comm in data.get("communities", []):
        comm_id = str(comm.get("id")).strip()
        comm_name = comm.get("name")
        if comm_name:
            community_names[comm_id] = comm_name
        for ps in comm.get("polling_stations", []):
            ps_id = str(ps.get("id")).strip()
            geo = ps.get("geo") or {}
            lat = geo.get("lat")
            lon = geo.get("lon")
            stations[ps_id] = {
                "id": ps_id,
                "community_id": comm_id,
                "community_name": comm_name,
                "name": ps.get("name"),
                "lat": lat,
                "lon": lon,
                "voter_count": ps.get("voter_count"),
                "number": ps.get("number"),
                "has_voting": bool(ps.get("voting")),
            }
            communities[comm_id].add(ps_id)

    return stations, communities, community_names


def geo_distance_m(a, b):
    """Haversine distance in meters between two (lat, lon) points."""
    if None in a or None in b:
        return None
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def report(xlsx_stations, xlsx_comms, xlsx_comm_names,
           json_stations, json_comms, json_comm_names,
           geo_tolerance_m=DEFAULT_GEO_TOLERANCE_M, verbose=False):
    xlsx_ids = set(xlsx_stations)
    json_ids = set(json_stations)

    both = xlsx_ids & json_ids
    only_xlsx = xlsx_ids - json_ids
    only_json = json_ids - xlsx_ids

    print("=" * 70)
    print("OVERALL")
    print("=" * 70)
    print(f"  xlsx stations:           {len(xlsx_ids):>6}")
    print(f"  json stations:           {len(json_ids):>6}")
    print(f"  in both:                 {len(both):>6}")
    print(f"  only in xlsx:            {len(only_xlsx):>6}")
    print(f"  only in json:            {len(only_json):>6}")
    print(f"  xlsx communities:        {len(xlsx_comms):>6}")
    print(f"  json communities:        {len(json_comms):>6}")
    print()

    # Communities
    xlsx_comm_ids = set(xlsx_comms)
    json_comm_ids = set(json_comms)
    only_xlsx_c = xlsx_comm_ids - json_comm_ids
    only_json_c = json_comm_ids - xlsx_comm_ids
    if only_xlsx_c or only_json_c:
        print("COMMUNITY MEMBERSHIP MISMATCH")
        if only_xlsx_c:
            print(f"  only in xlsx: {sorted(only_xlsx_c)}")
        if only_json_c:
            print(f"  only in json: {sorted(only_json_c)}")
        print()

    # Per-community count drift
    print("=" * 70)
    print("PER-COMMUNITY STATION COUNT (only mismatches shown)")
    print("=" * 70)
    mismatches = []
    for cid in sorted(xlsx_comm_ids | json_comm_ids, key=lambda x: int(x) if str(x).isdigit() else x):
        x_count = len(xlsx_comms.get(cid, set()))
        j_count = len(json_comms.get(cid, set()))
        if x_count != j_count:
            name = xlsx_comm_names.get(cid) or json_comm_names.get(cid) or "?"
            mismatches.append((cid, name, x_count, j_count))
    if not mismatches:
        print("  (all per-community counts agree)")
    else:
        print(f"  {'cid':<6}{'community':<30}{'xlsx':>6}{'json':>6}{'diff':>6}")
        for cid, name, x, j in mismatches:
            print(f"  {cid:<6}{str(name)[:28]:<30}{x:>6}{j:>6}{x-j:>+6}")
    print()

    # Stations only in one source
    if only_xlsx:
        print("=" * 70)
        print(f"STATIONS ONLY IN XLSX ({len(only_xlsx)})")
        print("=" * 70)
        for sid in sorted(only_xlsx, key=lambda s: (xlsx_stations[s]["opstina_id"] or "", s)):
            r = xlsx_stations[sid]
            print(f"  bm_id={sid:<6}  opstina={r['opstina_latin']:<22}  {str(r['bm_name'])[:50]}")
        print()

    if only_json:
        print("=" * 70)
        print(f"STATIONS ONLY IN JSON ({len(only_json)})")
        print("=" * 70)
        for sid in sorted(only_json, key=lambda s: (json_stations[s]["community_id"] or "", s)):
            r = json_stations[sid]
            print(f"  id={sid:<6}  community={str(r['community_name'])[:22]:<22}  {str(r['name'])[:50]}")
        print()

    # Field-level discrepancies for shared stations
    voter_diffs = []
    geo_diffs = []
    comm_diffs = []
    missing_geo_json = []
    missing_voter_json = []

    for sid in both:
        x = xlsx_stations[sid]
        j = json_stations[sid]

        # Community membership
        if x["opstina_id"] != j["community_id"]:
            comm_diffs.append((sid, x["opstina_id"], j["community_id"]))

        # Voter count
        if x["voter_count"] is not None and j["voter_count"] is not None:
            if x["voter_count"] != j["voter_count"]:
                voter_diffs.append((sid, x["voter_count"], j["voter_count"]))
        elif x["voter_count"] is not None and j["voter_count"] is None:
            missing_voter_json.append(sid)

        # Geolocation
        if (x["lat"] is not None and x["lon"] is not None
                and j["lat"] is not None and j["lon"] is not None):
            d = geo_distance_m((x["lat"], x["lon"]), (j["lat"], j["lon"]))
            if d is not None and d > geo_tolerance_m:
                geo_diffs.append((sid, x["lat"], x["lon"], j["lat"], j["lon"], d))
        elif (x["lat"] is not None and x["lon"] is not None
              and (j["lat"] is None or j["lon"] is None)):
            missing_geo_json.append(sid)

    print("=" * 70)
    print("FIELD-LEVEL DRIFT (shared stations)")
    print("=" * 70)
    print(f"  community mismatches:    {len(comm_diffs):>6}")
    print(f"  voter_count mismatches:  {len(voter_diffs):>6}")
    print(f"  geolocation drift (>{geo_tolerance_m:g} m): {len(geo_diffs)}")
    print(f"  voter_count missing in json: {len(missing_voter_json)}")
    print(f"  geo missing in json:         {len(missing_geo_json)}")
    print()

    if comm_diffs:
        print("-- community mismatches (bm_id, xlsx_opstina, json_community)")
        for row in comm_diffs[:50]:
            print(f"  {row}")
        if len(comm_diffs) > 50:
            print(f"  ... and {len(comm_diffs) - 50} more")
        print()

    if voter_diffs:
        print("-- voter_count mismatches (bm_id, xlsx, json)")
        limit = None if verbose else 30
        for row in voter_diffs[:limit] if limit else voter_diffs:
            print(f"  {row[0]:<6}  xlsx={row[1]:<6}  json={row[2]}")
        if limit and len(voter_diffs) > limit:
            print(f"  ... and {len(voter_diffs) - limit} more (use --verbose)")
        print()

    if geo_diffs:
        # Sort by distance descending so worst drifts surface first
        geo_diffs_sorted = sorted(geo_diffs, key=lambda r: r[5], reverse=True)
        print("-- geolocation drift (bm_id, xlsx_lat, xlsx_lon, json_lat, json_lon, drift_m)")
        limit = None if verbose else 20
        for sid, xlat, xlon, jlat, jlon, d in (geo_diffs_sorted[:limit] if limit else geo_diffs_sorted):
            print(f"  {sid:<6}  xlsx=({xlat:.5f},{xlon:.5f})  json=({jlat:.5f},{jlon:.5f})  d={d:>10.1f} m")
        if limit and len(geo_diffs) > limit:
            print(f"  ... and {len(geo_diffs) - limit} more (use --verbose)")
        print()

    if missing_voter_json and verbose:
        print(f"-- voter_count present in xlsx but missing in json: {missing_voter_json}")
    if missing_geo_json and verbose:
        print(f"-- geo present in xlsx but missing in json: {missing_geo_json}")

    return {
        "xlsx_count": len(xlsx_ids),
        "json_count": len(json_ids),
        "in_both": len(both),
        "only_xlsx": sorted(only_xlsx),
        "only_json": sorted(only_json),
        "community_mismatches": comm_diffs,
        "voter_count_mismatches": voter_diffs,
        "geo_drift": geo_diffs,
        "voter_count_missing_in_json": missing_voter_json,
        "geo_missing_in_json": missing_geo_json,
        "per_community_count_mismatch": mismatches,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", default=str(XLSX_PATH), help="Path to xlsx")
    ap.add_argument("--json", default=str(JSON_PATH), help="Path to JSON")
    ap.add_argument("--verbose", action="store_true", help="Print full lists")
    ap.add_argument("--threshold-meters", type=float, default=DEFAULT_GEO_TOLERANCE_M,
                    help=f"Geolocation drift threshold in meters (default: {DEFAULT_GEO_TOLERANCE_M:g})")
    ap.add_argument("--out", help="Optional path to write the report as JSON")
    args = ap.parse_args()

    xlsx_path = Path(args.xlsx)
    json_path = Path(args.json)
    if not xlsx_path.exists():
        sys.exit(f"xlsx not found: {xlsx_path}")
    if not json_path.exists():
        sys.exit(f"json not found: {json_path}")

    print(f"Loading {xlsx_path.name}...")
    xlsx_stations, xlsx_comms, xlsx_comm_names = load_xlsx(xlsx_path)
    print(f"Loading {json_path.name}...")
    json_stations, json_comms, json_comm_names = load_json(json_path)
    print()

    summary = report(
        xlsx_stations, xlsx_comms, xlsx_comm_names,
        json_stations, json_comms, json_comm_names,
        geo_tolerance_m=args.threshold_meters,
        verbose=args.verbose,
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.out}")


if __name__ == "__main__":
    main()
