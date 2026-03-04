#!/usr/bin/env python3
"""
merge_results.py — Enrich polling_stations_86.json with turnout and party
results scraped from RIK for election round 341140.

Usage:
    python3 merge_results.py
"""

import csv
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
JSON_IN = ROOT / "polling_stations_86.json"
JSON_OUT = ROOT / "polling_stations_86.json"         # overwrite in place
JSON_SAFE = ROOT / "polling_stations_86_with_results.json"  # safe copy

# ── Cyrillic → Latin transliteration ─────────────────────────────────────────
_CYR_LAT = [
    ("Љ", "Lj"), ("Њ", "Nj"), ("Џ", "Dž"),
    ("љ", "lj"), ("њ", "nj"), ("џ", "dž"),
    ("А", "A"), ("Б", "B"), ("В", "V"), ("Г", "G"), ("Д", "D"), ("Ђ", "Đ"),
    ("Е", "E"), ("Ж", "Ž"), ("З", "Z"), ("И", "I"), ("Ј", "J"), ("К", "K"),
    ("Л", "L"), ("М", "M"), ("Н", "N"), ("О", "O"), ("П", "P"), ("Р", "R"),
    ("С", "S"), ("Т", "T"), ("Ћ", "Ć"), ("У", "U"), ("Ф", "F"), ("Х", "H"),
    ("Ц", "C"), ("Ч", "Č"), ("Ш", "Š"),
    ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("д", "d"), ("ђ", "đ"),
    ("е", "e"), ("ж", "ž"), ("з", "z"), ("и", "i"), ("ј", "j"), ("к", "k"),
    ("л", "l"), ("м", "m"), ("н", "n"), ("о", "o"), ("п", "p"), ("р", "r"),
    ("с", "s"), ("т", "t"), ("ћ", "ć"), ("у", "u"), ("ф", "f"), ("х", "h"),
    ("ц", "c"), ("ч", "č"), ("ш", "š"),
]

def cyr_to_lat(s: str) -> str:
    for c, l in _CYR_LAT:
        s = s.replace(c, l)
    return s

def strip_diacritics(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )

def normalize_key(s: str) -> str:
    """Lowercase, collapse separators to single space, strip diacritics."""
    s = s.replace("-", " ").replace("_", " ")
    s = strip_diacritics(s.lower())
    return re.sub(r"\s+", " ", s).strip()

# ── Filename → community key ──────────────────────────────────────────────────
# Ordered longest-first so longer prefixes are matched before shorter ones.
_REGION_PREFIXES = [
    ("Beogradski_region_", "beograd"),
    ("Region_Vojvodine_", ""),
    ("Region_Južne_i_Istočne_Srbije_", ""),
    ("Region_Šumadije_i_Zapadne_Srbije_", ""),
    ("Region_Kosovo_i_Metohija_", ""),
    ("Inostranstvo_", ""),
    ("Zavodi_za_izvršenje_krivičnih_sankcija_", ""),
]
_SUB_PREFIXES = ["Gradska_opština_", "Grad_"]

def filename_to_community_key(fname: str) -> str:
    """
    Derive a normalized community key from a metadata CSV filename.

    Examples:
      metadata_2_341140_Region_Vojvodine_Ada.csv              → 'ada'
      metadata_2_341140_Beogradski_region_Gradska_opština_Vožd ovac.csv
                                                              → 'beograd vozdovac'
      metadata_2_341140_Beogradski_region_Gradska_opština_Palilula__Beograd_.csv
                                                              → 'beograd palilula'
      metadata_2_341140_Region_Južne_i_Istočne_Srbije_Gradska_opština_Palilula__Niš_.csv
                                                              → 'nis palilula'
    """
    name = fname[:-4] if fname.endswith(".csv") else fname
    prefix = "metadata_2_341140_"
    name = name[len(prefix):] if name.startswith(prefix) else name

    city_prefix = ""
    for region_str, city in _REGION_PREFIXES:
        if name.startswith(region_str):
            name = name[len(region_str):]
            city_prefix = city
            break

    for sub in _SUB_PREFIXES:
        if name.startswith(sub):
            name = name[len(sub):]
            break

    # Disambiguation pattern: "Base__City_" represents "Base (City)"
    m = re.match(r'^(.+?)__(.+?)_$', name)
    if m:
        base, disambig = m.group(1), m.group(2)
        return normalize_key(disambig + " " + base)

    if city_prefix:
        return normalize_key(city_prefix + " " + name)
    return normalize_key(name)

def json_community_to_key(cyrillic_name: str) -> str:
    """
    Convert a JSON community name (Cyrillic) to a normalized key.
    E.g. 'БЕОГРАД-ВОЖДОВАЦ' → 'beograd vozdovac'
         'БОР - ГРАД'        → 'bor'   (strips trailing '- GRAD' with spaces)
         'СТАРИ ГРАД'        → 'stari grad'  (no spaces around hyphen → kept)
    """
    key = normalize_key(cyr_to_lat(cyrillic_name))
    # Only strip " grad" when the Cyrillic name has " - ГРАД" (space-hyphen-space).
    # This distinguishes "БОР - ГРАД" (city proper) from "СТАРИ ГРАД" (place name).
    if ' - ' in cyrillic_name and cyrillic_name.endswith('ГРАД'):
        key = re.sub(r'\s+grad$', '', key).strip()
    return key

# ── Parse int with Serbian thousands separator ────────────────────────────────
def parse_int(s: str) -> int:
    if not s:
        return 0
    return int(re.sub(r"[^\d]", "", s))

# ── Step 1a: Parse metadata CSVs → turnout + community-station map ────────────
turnout_lookup: dict = {}           # csv_station_id → {registered,voted,...}
community_station_map: dict = {}    # (community_key, station_number) → csv_station_id

print("Parsing metadata CSVs...", file=sys.stderr)
for csv_file in sorted(OUTPUT.glob("metadata_*.csv")):
    community_key = filename_to_community_key(csv_file.name)
    try:
        with open(csv_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    csv_id = row["Biračko mesto ID"].strip().strip('"')
                    sname = row["Biračko mesto"].strip().strip('"')
                    registered = parse_int(row["Upisanih birača"].strip().strip('"'))
                    voted = parse_int(row["Glasalo"].strip().strip('"'))
                    invalid = parse_int(row["Nevažećih"].strip().strip('"'))
                    valid = parse_int(row["Važećih"].strip().strip('"'))
                except (KeyError, ValueError) as e:
                    print(f"  Warning {csv_file.name}: {e}", file=sys.stderr)
                    continue

                m = re.match(r"^(\d+)", sname)
                if not m:
                    continue
                station_number = int(m.group(1))

                turnout_pct = round(voted / registered * 100, 2) if registered > 0 else 0.0
                turnout_lookup[csv_id] = {
                    "registered": registered,
                    "voted": voted,
                    "invalid": invalid,
                    "valid": valid,
                    "turnout_pct": turnout_pct,
                }
                community_station_map[(community_key, station_number)] = csv_id
    except Exception as e:
        print(f"  Error reading {csv_file.name}: {e}", file=sys.stderr)

print(
    f"  {len(turnout_lookup)} station turnout records, "
    f"{len(community_station_map)} community-station mappings",
    file=sys.stderr,
)

# ── Step 1b: Parse rezultati CSVs → party results ─────────────────────────────
results_lookup: dict = {}   # csv_station_id → [{party, votes, pct}, ...]

print("Parsing rezultati CSVs...", file=sys.stderr)
for csv_file in sorted(OUTPUT.glob("rezultati_*.csv")):
    m = re.match(r"rezultati_2_341140_(\d+)\.csv", csv_file.name)
    if not m:
        continue
    csv_id = m.group(1)
    results = []
    try:
        with open(csv_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    party = row["Stranka/Lista"].strip().strip('"')
                    party = re.sub(r"^\d+\.\s*", "", party)   # strip leading "1. "
                    votes = parse_int(row["Broj glasova"].strip().strip('"'))
                    pct = float(row["Procenat"].strip().strip('"').replace(",", "."))
                except (KeyError, ValueError):
                    continue
                results.append({"party": party, "votes": votes, "pct": pct})
    except Exception as e:
        print(f"  Error reading {csv_file.name}: {e}", file=sys.stderr)
    results_lookup[csv_id] = results

print(f"  {len(results_lookup)} stations with party results", file=sys.stderr)

# ── Step 1d: Enrich JSON ──────────────────────────────────────────────────────
print("Loading JSON...", file=sys.stderr)
with open(JSON_IN, encoding="utf-8") as f:
    data = json.load(f)

matched = unmatched = no_turnout = 0
unmatched_names: list = []

for community in data["communities"]:
    comm_key = json_community_to_key(community["name"])
    for station in community["polling_stations"]:
        station_number = station.get("number")
        if station_number is None:
            unmatched += 1
            continue

        # Primary lookup
        csv_id = community_station_map.get((comm_key, station_number))

        # Fallback: if community key has a city prefix ("beograd vozdovac"),
        # also try just the last word/phrase as the community key ("vozdovac").
        # Handles cases like JSON "ПОЖАРЕВАЦ-КОСТОЛАЦ" → try "kostolac".
        if csv_id is None:
            parts = comm_key.split(" ", 1)
            if len(parts) == 2:
                csv_id = community_station_map.get((parts[1], station_number))

        if csv_id is None:
            unmatched += 1
            unmatched_names.append(
                f"  {community['name']!r} (key={comm_key!r}, station={station_number})"
            )
            continue

        turnout = turnout_lookup.get(csv_id)
        if turnout is None:
            no_turnout += 1
            continue

        voting = dict(turnout)
        voting["results"] = results_lookup.get(csv_id, [])
        station["voting"] = voting
        matched += 1

total = matched + unmatched + no_turnout
print(f"\nMatch stats:", file=sys.stderr)
print(f"  Matched:        {matched:>6} / {total}  ({matched / total * 100:.1f}%)", file=sys.stderr)
print(f"  Unmatched:      {unmatched:>6}", file=sys.stderr)
print(f"  No turnout CSV: {no_turnout:>6}", file=sys.stderr)

if unmatched_names:
    print(f"\nFirst 30 unmatched:", file=sys.stderr)
    for line in unmatched_names[:30]:
        print(line, file=sys.stderr)

# ── Step 1e: Write output ─────────────────────────────────────────────────────
with open(JSON_SAFE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"\nSafe output written:    {JSON_SAFE}", file=sys.stderr)

with open(JSON_OUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"In-place output written: {JSON_OUT}", file=sys.stderr)
