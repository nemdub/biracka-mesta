#!/usr/bin/env python3
"""
Build html/coord_conflicts_data.js from data/sve_opstine_accuracy.json.

Filters to `coord_conflict` rows only (the 2,160 same-identity-different-coords
cases) and emits the minimal payload the review page needs. Mirrors the
build-time pattern used by generate_data.py / generate_api_data.py.
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ACC_PATH = REPO / "data" / "sve_opstine_accuracy.json"
OUT_PATH = REPO / "html" / "coord_conflicts_data.js"


REVIEW_FIELDS = (
    "opstina_cyr", "rb_xlsx", "json_number", "json_id",
    "distance_m", "name_similarity",
    "xlsx_name", "xlsx_address", "xlsx_lat", "xlsx_lon",
    "xlsx_coord_format", "xlsx_gmaps_url",
    "json_name", "json_lat", "json_lon",
)


def conflict_key(p):
    rb = p["rb_xlsx"] if p["rb_xlsx"] is not None else "-"
    jn = p["json_number"] if p["json_number"] is not None else "-"
    return f"{p['opstina_cyr']}|{rb}|{jn}"


def main():
    data = json.loads(ACC_PATH.read_text(encoding="utf-8"))
    conflicts = []
    for p in data["pairs"]:
        if p["status"] != "coord_conflict":
            continue
        row = {"key": conflict_key(p)}
        for f in REVIEW_FIELDS:
            row[f] = p.get(f)
        conflicts.append(row)

    flagged_opstinas = sorted(
        o for o, s in data["by_opstina"].items() if s.get("likely_source_error")
    )

    payload = {
        "conflicts": conflicts,
        "flaggedOpstinas": flagged_opstinas,
        "generatedAt": data.get("updated_at"),
    }
    js = "window.CONFLICTS_DATA=" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    OUT_PATH.write_text(js, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(conflicts)} conflicts, {len(flagged_opstinas)} flagged opstinas)")


if __name__ == "__main__":
    main()
