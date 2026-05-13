#!/usr/bin/env python3
"""
Cross-check accuracy between data/sve_opstine_normalized.json (xlsx, 2022)
and polling_stations_86.json (JSON, 2023).

For each polling station, classify it as:
  - confirmed: same station in both sources, coords agree (≤100 m)
  - coord_conflict: same station, coords differ (>100 m) -> one source wrong
  - xlsx_only_geo / json_only_geo: matched, only one side has coords
  - neither_geo: matched, neither side has coords
  - xlsx_only / json_only: present in only one source after fuzzy match
  - json_only_uncovered_opstina: structural gap (xlsx hasn't started that opstina)

Same-station match within an opstina is by:
  (a) matching rb/number, OR
  (b) fuzzy name+address similarity ≥ 0.80 (difflib SequenceMatcher).

Emits:
  data/sve_opstine_accuracy.csv
  data/sve_opstine_accuracy.json
  data/sve_opstine_accuracy.md

Read-only on inputs; no network; no new dependencies.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_XLSX_JSON = REPO / "data" / "sve_opstine_normalized.json"
DEFAULT_LOCALITY_JSON = REPO / "polling_stations_86.json"
DEFAULT_OUT_DIR = REPO / "data"

SAME_THRESHOLD_M = 100.0
NAME_FUZZ_THRESHOLD = 0.80
# (min_conflicts, min_share_with_strong_name_match)
SOURCE_ERROR_FLAG = (5, 0.85)
STRONG_NAME_MATCH = 0.85

# Opstinas xlsx never started populating (header present but zero data rows)
# and JSON-only special categories that xlsx is not expected to cover.
KNOWN_UNCOVERED_BY_XLSX = {
    # JSON-only special categories
    "ГОРА",
    "МИНИСТАРСТВО ОДБРАНЕ",
    "ИНОСТРАНСТВО",
    "УПРАВА ЗА ИЗВРШЕЊЕ ЗАВОДСКИХ САНКЦИЈА",
}
# (Other empty-in-xlsx opstinas are detected dynamically below.)


# ---------------------------------------------------------------------------
# Geometry & string helpers
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


_NN_PREFIX = re.compile(r"^\s*\d+\s*\.\s*")
_PUNCT = re.compile(r'[„“”"\'’`()\-\–\—]+')
_WS = re.compile(r"\s+")


def normalize_for_match(s: str | None) -> str:
    if not s:
        return ""
    s = _NN_PREFIX.sub("", s)
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip().lower()
    return s


def split_json_name(name: str | None):
    """JSON names look like '12. ОСНОВНА ШКОЛА - АДРЕСА БР. 5'. Split into
    (number_prefix, station_name, address). Address is everything after the
    LAST ' - '. Best-effort: if the name has no ' - ' the address is None.
    """
    m = re.match(r"^\s*(\d+)\s*\.\s*(.*)$", name or "")
    if not m:
        return None, name, None
    rest = m.group(2).strip()
    if " - " in rest:
        head, _, tail = rest.rpartition(" - ")
        return int(m.group(1)), head.strip(), tail.strip()
    return int(m.group(1)), rest, None


def fuzz_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def best_match_ratio(xlsx_rec, json_rec) -> float:
    """Combined name+address similarity, taking the max."""
    x_name = normalize_for_match(xlsx_rec.get("name_cyr") or "")
    x_addr = normalize_for_match(xlsx_rec.get("address") or "")
    _, j_name, j_addr = split_json_name(json_rec["name"])
    j_name_n = normalize_for_match(j_name or "")
    j_addr_n = normalize_for_match(j_addr or "")
    return max(
        fuzz_ratio(x_name, j_name_n),
        fuzz_ratio(x_addr, j_addr_n),
        fuzz_ratio(x_addr, j_name_n),  # JSON sometimes lumps address into name
        fuzz_ratio(x_name, j_addr_n),
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_xlsx(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_json_sites(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

def pair_within_opstina(xlsx_records, json_records, remaining_xlsx, remaining_json):
    """Run fuzzy pass on the unmatched leftovers within one opstina.

    `remaining_xlsx` and `remaining_json` are lists of indices. We return a
    list of `(xi, ji, ratio)` greedy pairings (highest ratio first) where
    ratio ≥ NAME_FUZZ_THRESHOLD; each index used at most once.
    """
    candidates = []
    for xi in remaining_xlsx:
        xr = xlsx_records[xi]
        # Skip xlsx records with no identifying text at all
        if not (xr.get("name_cyr") or xr.get("address")):
            continue
        for ji in remaining_json:
            jr = json_records[ji]
            r = best_match_ratio(xr, jr)
            if r >= NAME_FUZZ_THRESHOLD:
                candidates.append((r, xi, ji))
    candidates.sort(reverse=True)
    used_x, used_j, pairs = set(), set(), []
    for r, xi, ji in candidates:
        if xi in used_x or ji in used_j:
            continue
        used_x.add(xi)
        used_j.add(ji)
        pairs.append((xi, ji, r))
    return pairs


def classify_pair(xr, jr, distance):
    x_has = xr is not None and xr.get("lat") is not None
    j_has = jr is not None and (jr.get("geo") or {}).get("lat") is not None
    if x_has and j_has:
        return "confirmed" if distance is not None and distance <= SAME_THRESHOLD_M else "coord_conflict"
    if x_has:
        return "xlsx_only_geo"
    if j_has:
        return "json_only_geo"
    return "neither_geo"


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx-json", default=str(DEFAULT_XLSX_JSON))
    ap.add_argument("--locality-json", default=str(DEFAULT_LOCALITY_JSON))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--same-threshold-m", type=float, default=SAME_THRESHOLD_M)
    args = ap.parse_args()

    xlsx_path = Path(args.xlsx_json)
    json_path = Path(args.locality_json)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    xlsx = load_xlsx(xlsx_path)
    js = load_json_sites(json_path)

    # ---- Group by opstina ----
    xlsx_by_opst = defaultdict(list)
    for r in xlsx:
        xlsx_by_opst[r["opstina_cyr"]].append(r)
    json_by_loc = {loc["name"]: loc for loc in js["localities"]}

    xlsx_opstinas = set(xlsx_by_opst.keys())
    json_opstinas = set(json_by_loc.keys())

    # An opstina is "uncovered" if xlsx has no records for it (either the
    # opstina header was missing or it had zero data rows).
    uncovered_opstinas = (json_opstinas - xlsx_opstinas) | KNOWN_UNCOVERED_BY_XLSX

    # ---- Pair stations within each opstina ----
    pair_rows = []
    by_opstina_stats = {}

    for opst in sorted(xlsx_opstinas | json_opstinas):
        xlsx_recs = xlsx_by_opst.get(opst, [])
        loc = json_by_loc.get(opst)
        json_recs = loc["polling_stations"] if loc else []

        # Index by rb / number
        x_by_rb = {}
        for i, xr in enumerate(xlsx_recs):
            if xr.get("rb") is not None and xr["rb"] not in x_by_rb:
                x_by_rb[xr["rb"]] = i
        j_by_num = {jr["number"]: i for i, jr in enumerate(json_recs)}

        # Seed pairs by matching rb
        paired_xlsx = set()
        paired_json = set()
        seed_pairs = []
        for rb, xi in x_by_rb.items():
            if rb in j_by_num:
                ji = j_by_num[rb]
                seed_pairs.append((xi, ji, "seed_rb"))
                paired_xlsx.add(xi)
                paired_json.add(ji)

        # Fuzzy fallback for leftovers
        remaining_x = [i for i in range(len(xlsx_recs)) if i not in paired_xlsx]
        remaining_j = [i for i in range(len(json_recs)) if i not in paired_json]
        fuzzy_pairs = pair_within_opstina(xlsx_recs, json_recs, remaining_x, remaining_j)
        for xi, ji, ratio in fuzzy_pairs:
            seed_pairs.append((xi, ji, ("seed_fuzzy", ratio)))
            paired_xlsx.add(xi)
            paired_json.add(ji)

        # Build rows: matched pairs first, then unmatched leftovers
        n_pairs_with_both_geo = 0
        n_confirmed = 0
        n_coord_conflict = 0
        n_xlsx_only_geo = 0
        n_json_only_geo = 0
        n_neither_geo = 0
        n_xlsx_only = 0
        n_json_only = 0
        conflict_strong_names = 0

        for xi, ji, mode in seed_pairs:
            xr = xlsx_recs[xi]
            jr = json_recs[ji]
            x_has = xr.get("lat") is not None
            j_has = (jr.get("geo") or {}).get("lat") is not None
            distance = None
            if x_has and j_has:
                distance = haversine_m(
                    xr["lat"], xr["lon"],
                    jr["geo"]["lat"], jr["geo"]["lon"],
                )
                n_pairs_with_both_geo += 1
            status = classify_pair(xr, jr, distance)
            # Name similarity for the report (helpful for conflict review)
            name_sim = best_match_ratio(xr, jr)
            if status == "confirmed":
                n_confirmed += 1
            elif status == "coord_conflict":
                n_coord_conflict += 1
                if name_sim >= STRONG_NAME_MATCH:
                    conflict_strong_names += 1
            elif status == "xlsx_only_geo":
                n_xlsx_only_geo += 1
            elif status == "json_only_geo":
                n_json_only_geo += 1
            elif status == "neither_geo":
                n_neither_geo += 1

            if isinstance(mode, tuple):
                mode_label, mode_ratio = mode
                note = f"fuzzy-match ratio={mode_ratio:.2f}"
            else:
                mode_label = mode
                note = ""

            pair_rows.append({
                "opstina_cyr": opst,
                "rb_xlsx": xr.get("rb"),
                "json_number": jr.get("number"),
                "status": status,
                "distance_m": round(distance, 1) if distance is not None else None,
                "name_similarity": round(name_sim, 3),
                "match_mode": mode_label,
                "xlsx_name": xr.get("name_cyr"),
                "xlsx_address": xr.get("address"),
                "xlsx_lat": xr.get("lat"),
                "xlsx_lon": xr.get("lon"),
                "xlsx_coord_format": xr.get("coord_source_format"),
                "xlsx_gmaps_url": xr.get("gmaps_url"),
                "json_id": jr.get("id"),
                "json_name": jr.get("name"),
                "json_lat": (jr.get("geo") or {}).get("lat"),
                "json_lon": (jr.get("geo") or {}).get("lon"),
                "note": note,
            })

        # Unmatched xlsx leftovers -> xlsx_only
        for xi in range(len(xlsx_recs)):
            if xi in paired_xlsx:
                continue
            xr = xlsx_recs[xi]
            # Skip totally empty xlsx rows (rb=None, no name, no address)
            if (xr.get("rb") is None and not xr.get("name_cyr") and not xr.get("address")):
                continue
            n_xlsx_only += 1
            pair_rows.append({
                "opstina_cyr": opst,
                "rb_xlsx": xr.get("rb"),
                "json_number": None,
                "status": "xlsx_only",
                "distance_m": None,
                "name_similarity": None,
                "match_mode": "unmatched",
                "xlsx_name": xr.get("name_cyr"),
                "xlsx_address": xr.get("address"),
                "xlsx_lat": xr.get("lat"),
                "xlsx_lon": xr.get("lon"),
                "xlsx_coord_format": xr.get("coord_source_format"),
                "xlsx_gmaps_url": xr.get("gmaps_url"),
                "json_id": None,
                "json_name": None,
                "json_lat": None,
                "json_lon": None,
                "note": "",
            })

        # Unmatched json leftovers -> json_only or json_only_uncovered_opstina
        is_uncovered = opst in uncovered_opstinas or len(xlsx_recs) == 0
        for ji in range(len(json_recs)):
            if ji in paired_json:
                continue
            jr = json_recs[ji]
            status = "json_only_uncovered_opstina" if is_uncovered else "json_only"
            if status == "json_only":
                n_json_only += 1
            pair_rows.append({
                "opstina_cyr": opst,
                "rb_xlsx": None,
                "json_number": jr.get("number"),
                "status": status,
                "distance_m": None,
                "name_similarity": None,
                "match_mode": "unmatched",
                "xlsx_name": None,
                "xlsx_address": None,
                "xlsx_lat": None,
                "xlsx_lon": None,
                "xlsx_coord_format": None,
                "xlsx_gmaps_url": None,
                "json_id": jr.get("id"),
                "json_name": jr.get("name"),
                "json_lat": (jr.get("geo") or {}).get("lat"),
                "json_lon": (jr.get("geo") or {}).get("lon"),
                "note": "",
            })

        conf_rate = (n_confirmed / n_pairs_with_both_geo) if n_pairs_with_both_geo else None
        likely_source_error = False
        if n_coord_conflict >= SOURCE_ERROR_FLAG[0]:
            share_strong = conflict_strong_names / n_coord_conflict if n_coord_conflict else 0
            if share_strong >= SOURCE_ERROR_FLAG[1]:
                likely_source_error = True

        by_opstina_stats[opst] = {
            "pairs_with_both_geo": n_pairs_with_both_geo,
            "confirmed": n_confirmed,
            "coord_conflicts": n_coord_conflict,
            "xlsx_only_geo": n_xlsx_only_geo,
            "json_only_geo": n_json_only_geo,
            "neither_geo": n_neither_geo,
            "xlsx_only": n_xlsx_only,
            "json_only": n_json_only,
            "confirmation_rate": round(conf_rate, 3) if conf_rate is not None else None,
            "is_uncovered_by_xlsx": is_uncovered,
            "likely_source_error": likely_source_error,
        }

    # ---- Global summary ----
    summary = defaultdict(int)
    for row in pair_rows:
        summary[row["status"]] += 1
    summary["total_rows"] = len(pair_rows)

    # ---- Invariant check: every station appears exactly once ----
    n_xlsx_total = len(xlsx)
    n_json_total = sum(len(loc["polling_stations"]) for loc in js["localities"])
    n_paired = sum(1 for r in pair_rows if r["status"] in
                   ("confirmed", "coord_conflict", "xlsx_only_geo",
                    "json_only_geo", "neither_geo"))
    n_xlsx_emitted = n_paired + summary["xlsx_only"]
    n_json_emitted = n_paired + summary["json_only"] + summary["json_only_uncovered_opstina"]
    # Empty xlsx rows we skipped won't be in pair_rows -> compute count of skips
    n_xlsx_skipped = sum(
        1 for r in xlsx
        if r.get("rb") is None and not r.get("name_cyr") and not r.get("address")
    )
    assert n_xlsx_emitted + n_xlsx_skipped == n_xlsx_total, (
        f"xlsx accounting mismatch: emitted={n_xlsx_emitted} skipped={n_xlsx_skipped} total={n_xlsx_total}"
    )
    assert n_json_emitted == n_json_total, (
        f"json accounting mismatch: emitted={n_json_emitted} total={n_json_total}"
    )

    # ---- Write CSV ----
    csv_path = out_dir / "sve_opstine_accuracy.csv"
    columns = [
        "opstina_cyr", "rb_xlsx", "json_number", "status",
        "distance_m", "name_similarity", "match_mode",
        "xlsx_name", "xlsx_address", "xlsx_lat", "xlsx_lon", "xlsx_coord_format",
        "xlsx_gmaps_url",
        "json_id", "json_name", "json_lat", "json_lon", "note",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in pair_rows:
            w.writerow(r)

    # ---- Write JSON ----
    json_out_path = out_dir / "sve_opstine_accuracy.json"
    json_out = {
        "summary": dict(summary),
        "thresholds": {
            "same_distance_m": args.same_threshold_m,
            "fuzzy_name_ratio": NAME_FUZZ_THRESHOLD,
            "source_error_min_conflicts": SOURCE_ERROR_FLAG[0],
            "source_error_min_strong_name_share": SOURCE_ERROR_FLAG[1],
        },
        "totals": {
            "xlsx_records": n_xlsx_total,
            "json_records": n_json_total,
            "xlsx_skipped_empty": n_xlsx_skipped,
        },
        "by_opstina": by_opstina_stats,
        "pairs": pair_rows,
    }
    with json_out_path.open("w", encoding="utf-8") as f:
        json.dump(json_out, f, ensure_ascii=False, indent=2)

    # ---- Write Markdown ----
    md_path = out_dir / "sve_opstine_accuracy.md"
    write_markdown(md_path, summary, by_opstina_stats, pair_rows,
                   n_xlsx_total, n_json_total, n_xlsx_skipped)

    print(f"Wrote {csv_path}")
    print(f"Wrote {json_out_path}")
    print(f"Wrote {md_path}")
    print()
    print("Headline:")
    for k in ("confirmed", "coord_conflict", "xlsx_only_geo",
              "json_only_geo", "neither_geo",
              "xlsx_only", "json_only", "json_only_uncovered_opstina"):
        print(f"  {k}: {summary[k]}")


def write_markdown(path, summary, by_opstina, pair_rows,
                   n_xlsx_total, n_json_total, n_xlsx_skipped):
    confirmed = summary["confirmed"]
    conflict = summary["coord_conflict"]
    xog = summary["xlsx_only_geo"]
    jog = summary["json_only_geo"]
    neither = summary["neither_geo"]
    xo = summary["xlsx_only"]
    jo = summary["json_only"]
    jou = summary["json_only_uncovered_opstina"]
    pairs_with_both = confirmed + conflict

    lines = []
    lines.append("# Accuracy cross-check: `sve_opstine_normalized.json` (2022 xlsx) vs `polling_stations_86.json` (2023)")
    lines.append("")
    lines.append("Two-source confirmation: if both datasets agree on a station's identity and its coordinates "
                 f"(within {SAME_THRESHOLD_M:.0f} m), that record is treated as trustworthy. Identity match is "
                 "by `rb`/`number` within an opstina, falling back to fuzzy name+address similarity "
                 f"≥ {NAME_FUZZ_THRESHOLD:.2f}.")
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- **Confirmed** (both sources agree on identity *and* coords): **{confirmed}** stations")
    lines.append(f"- **Coord conflicts** (same station per identity, coords differ > {SAME_THRESHOLD_M:.0f} m — one source is wrong): **{conflict}**")
    if pairs_with_both:
        lines.append(f"  - Confirmation rate on overlap with geo on both sides: **{confirmed*100//pairs_with_both}%**")
    lines.append(f"- **Single-source coverage** (no cross-check possible):")
    lines.append(f"  - xlsx fills a geo gap JSON didn't have: **{xog}**")
    lines.append(f"  - JSON has geo, xlsx doesn't: **{jog}**")
    lines.append(f"  - Neither side has geo: **{neither}**")
    lines.append(f"- **Identity-only-in-one-source** (no plausible counterpart found):")
    lines.append(f"  - xlsx-only: **{xo}**")
    lines.append(f"  - JSON-only (in opstinas xlsx covers): **{jo}**")
    lines.append(f"  - JSON-only (xlsx hasn't started this opstina/category): **{jou}**")
    lines.append("")
    lines.append(f"_Totals_: xlsx = {n_xlsx_total} records (skipped {n_xlsx_skipped} empty), JSON = {n_json_total}.")
    lines.append("")

    # ---- Likely source-error opstinas ----
    flagged = [(o, s) for o, s in by_opstina.items() if s["likely_source_error"]]
    flagged.sort(key=lambda kv: -kv[1]["coord_conflicts"])
    lines.append("## Opstinas where one source appears systematically wrong")
    lines.append("")
    if flagged:
        lines.append("These opstinas have many coord-conflict pairs whose *identity* match is strong "
                     "(name-similarity ≥ 0.85). That pattern means the disagreement is consistently "
                     "about coordinates, not about which station we're looking at — so one of the two "
                     "sources has a systematic geocoding error in that opstina.")
        lines.append("")
        lines.append("| Opstina | Conflicts | Confirmed | Confirmation rate |")
        lines.append("|---|---:|---:|---:|")
        for o, s in flagged:
            rate = f"{s['confirmation_rate']*100:.0f}%" if s['confirmation_rate'] is not None else "—"
            lines.append(f"| {o} | {s['coord_conflicts']} | {s['confirmed']} | {rate} |")
    else:
        lines.append("_None flagged._")
    lines.append("")

    # ---- Per-opstina table (worst confirmation rate first) ----
    lines.append("## Per-opstina accuracy")
    lines.append("")
    lines.append("Sorted by confirmation rate ascending (worst-agreeing first), opstinas with no overlap "
                 "(`pairs_with_both_geo = 0`) listed last grouped by row count.")
    lines.append("")
    lines.append("| Opstina | Pairs (both geo) | Confirmed | Conflicts | xlsx-only geo | json-only geo | Neither geo | xlsx-only | json-only | Conf. rate |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    def sort_key(item):
        o, s = item
        rate = s["confirmation_rate"]
        if rate is None:
            return (1, -s["xlsx_only"] - s["json_only"])
        return (0, rate)
    for o, s in sorted(by_opstina.items(), key=sort_key):
        rate = f"{s['confirmation_rate']*100:.0f}%" if s['confirmation_rate'] is not None else "—"
        flag = " 🚩" if s["likely_source_error"] else ""
        unc = " *(uncovered)*" if s["is_uncovered_by_xlsx"] else ""
        lines.append(
            f"| {o}{unc}{flag} | {s['pairs_with_both_geo']} | {s['confirmed']} | {s['coord_conflicts']} | "
            f"{s['xlsx_only_geo']} | {s['json_only_geo']} | {s['neither_geo']} | "
            f"{s['xlsx_only']} | {s['json_only']} | {rate} |"
        )
    lines.append("")

    # ---- Top conflicts ----
    conflicts = [r for r in pair_rows if r["status"] == "coord_conflict"]
    conflicts.sort(key=lambda r: -(r["distance_m"] or 0))
    lines.append(f"## Top {min(50, len(conflicts))} coord conflicts (manual review)")
    lines.append("")
    lines.append("Both sources matched on identity but disagree on coordinates by more than "
                 f"{SAME_THRESHOLD_M:.0f} m. No auto-pick — the bigger the distance, the more likely "
                 "the lower-quality source has a typo or geocoder bug, not a real relocation.")
    lines.append("")
    lines.append("| Opstina | rb (xlsx) | json # | Δ (m) | Name sim. | xlsx name / addr | json name | xlsx coord | json coord |")
    lines.append("|---|---:|---:|---:|---:|---|---|---|---|")
    for r in conflicts[:50]:
        x_lbl = f"{r['xlsx_name'] or ''} / {r['xlsx_address'] or ''}".replace("|", "\\|")
        j_lbl = (r["json_name"] or "").replace("|", "\\|")
        lines.append(
            f"| {r['opstina_cyr']} | {r['rb_xlsx']} | {r['json_number']} | "
            f"{r['distance_m']:.0f} | {r['name_similarity']:.2f} | {x_lbl[:80]} | {j_lbl[:80]} | "
            f"`{r['xlsx_lat']:.5f},{r['xlsx_lon']:.5f}` | `{r['json_lat']:.5f},{r['json_lon']:.5f}` |"
        )
    lines.append("")

    # ---- xlsx-only & json-only samples ----
    xlsx_onlys = [r for r in pair_rows if r["status"] == "xlsx_only"]
    json_onlys = [r for r in pair_rows if r["status"] == "json_only"]
    lines.append(f"## xlsx-only stations (no plausible counterpart in JSON), top {min(50, len(xlsx_onlys))}")
    lines.append("")
    lines.append("Either added since 2022, or named/addressed so differently that fuzzy match missed.")
    lines.append("")
    lines.append("| Opstina | rb | xlsx name | xlsx address |")
    lines.append("|---|---:|---|---|")
    for r in xlsx_onlys[:50]:
        nm = (r["xlsx_name"] or "").replace("|", "\\|")
        ad = (r["xlsx_address"] or "").replace("|", "\\|")
        lines.append(f"| {r['opstina_cyr']} | {r['rb_xlsx']} | {nm[:100]} | {ad[:100]} |")
    lines.append("")

    lines.append(f"## JSON-only stations in opstinas xlsx covers, top {min(50, len(json_onlys))}")
    lines.append("")
    lines.append("Either removed since 2022, or named/addressed differently. (Stations in xlsx-uncovered opstinas "
                 "are in `json_only_uncovered_opstina` and excluded from this list.)")
    lines.append("")
    lines.append("| Opstina | # | json name |")
    lines.append("|---|---:|---|")
    for r in json_onlys[:50]:
        nm = (r["json_name"] or "").replace("|", "\\|")
        lines.append(f"| {r['opstina_cyr']} | {r['json_number']} | {nm[:140]} |")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
