#!/usr/bin/env python3
"""
Geocode polling stations from polling_stations_86.json using kucni_broj.csv.

For each polling station:
  1. Parse the address from the "name" field (part after the last comma)
     and the settlement name (part between last " - " and the last comma).
  2. Strip known street-type prefixes (ул., улица, тг., etc.) from the street.
  3. Split address into street name + house number.
  4. Look up in kucni_broj.csv by municipality → settlement → street → number.
     Tries Cyrillic street index first, then Latin street index, then
     falls back to a settlement centroid when the "street" token is a village name.
  5. Convert UTM Zone 34N (EPSG:32634) coordinates to WGS84 lat/lon.
  6. Write back to polling_stations_86_copy.json as a "geo": {"lat": …, "lon": …} key.
     Centroid-derived coordinates also get "geo_approx": true on the station object.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
from difflib import get_close_matches
from pathlib import Path

JSON_FILE = Path("polling_stations_86.json")
CSV_FILE  = Path("kucni_broj.csv")


# ---------------------------------------------------------------------------
# UTM Zone 34N → WGS84 conversion  (pure Python, no external libraries)
# EPSG:32634  –  central meridian 21 °E, false easting 500 000 m
# ---------------------------------------------------------------------------
def utm34n_to_latlon(easting: float, northing: float) -> tuple[float, float]:
    a   = 6_378_137.0          # WGS84 semi-major axis (m)
    f   = 1 / 298.257_223_563  # WGS84 flattening
    b   = a * (1 - f)
    e2  = 1 - (b / a) ** 2    # first eccentricity squared
    ep2 = e2 / (1 - e2)       # second eccentricity squared

    k0   = 0.9996
    E0   = 500_000.0
    lon0 = math.radians(21.0)  # zone 34 central meridian

    x = easting - E0
    y = northing               # no false northing (northern hemisphere)

    M   = y / k0
    mu  = M / (a * (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    e1  = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3*e1/2    - 27*e1**3/32)   * math.sin(2*mu)
        + (21*e1**2/16 - 55*e1**4/32) * math.sin(4*mu)
        + (151*e1**3/96)               * math.sin(6*mu)
        + (1097*e1**4/512)             * math.sin(8*mu)
    )

    N1 = a / math.sqrt(1 - e2 * math.sin(phi1)**2)
    T1 = math.tan(phi1)**2
    C1 = ep2 * math.cos(phi1)**2
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1)**2)**1.5
    D  = x / (N1 * k0)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
          D**2/2
        - (5 + 3*T1 + 10*C1 - 4*C1**2 - 9*ep2)             * D**4/24
        + (61 + 90*T1 + 298*C1 + 45*T1**2 - 252*ep2 - 3*C1**2) * D**6/720
    )
    lon = lon0 + (
          D
        - (1 + 2*T1 + C1)                                   * D**3/6
        + (5 - 2*C1 + 28*T1 - 3*C1**2 + 8*ep2 + 24*T1**2)  * D**5/120
    ) / math.cos(phi1)

    return round(math.degrees(lat), 7), round(math.degrees(lon), 7)


def _parse_wkt_point(wkt: str) -> tuple[float, float] | None:
    m = re.match(r"POINT\(([0-9.]+)\s+([0-9.]+)\)", wkt.strip())
    return (float(m.group(1)), float(m.group(2))) if m else None


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Station names sometimes mix visually identical Latin and Cyrillic codepoints
# (e.g. Latin O U+004F instead of Cyrillic О U+041E).  Map every confusable
# Latin character to its Cyrillic lookalike so comparisons work correctly.
# Keys use explicit Unicode escapes to avoid the same copy-paste ambiguity.
_HOMOGLYPHS = str.maketrans({
    '\u0061': '\u0430',  # a → а
    '\u0063': '\u0441',  # c → с
    '\u0065': '\u0435',  # e → е
    '\u006a': '\u0458',  # j → ј
    '\u006b': '\u043a',  # k → к
    '\u006f': '\u043e',  # o → о
    '\u0070': '\u0440',  # p → р
    '\u0078': '\u0445',  # x → х
})


def norm(s: str) -> str:
    """Lowercase + collapse whitespace + convert Latin homoglyphs to Cyrillic."""
    return re.sub(r"\s+", " ", s.strip().lower()).translate(_HOMOGLYPHS)


def norm_latin(s: str) -> str:
    """Lowercase + collapse whitespace, WITHOUT Latin→Cyrillic homoglyph conversion."""
    return re.sub(r"\s+", " ", s.strip().lower())


def norm_number(s: str) -> str:
    """
    Normalise a house number: lowercase, remove slash and internal spaces.
    '32/а' → '32а'   '190Б' → '190б'   '3 б' → '3б'
    """
    s = norm(s).replace("/", "").replace(" ", "")
    return s


# ---------------------------------------------------------------------------
# Street-type prefix stripping
# ---------------------------------------------------------------------------
_STREET_PREFIX_RE = re.compile(
    r"^(?:"
    r"ул(?:ица)?\.?"   # ул. / улица
    r"|тг\.?"          # тг.
    r"|трг\.?"         # трг.
    r"|бул(?:евар)?\.?"  # бул. / булевар
    r"|бб\.?"          # бб.
    r")\s+",
    re.IGNORECASE | re.UNICODE,
)


def strip_street_prefix(s: str) -> str:
    """Remove Serbian street-type prefixes (ул., улица, тг., трг., бул., бб.) from a street string."""
    return _STREET_PREFIX_RE.sub("", s).strip()


# ---------------------------------------------------------------------------
# Station name parsing
# ---------------------------------------------------------------------------
# Names follow:  "N. Venue [- more desc] - Settlement, Street HouseNumber"
# Also handles "BR" / "br." between street name and number ("Улица BR 43").
# We want:
#   settlement = part after the last " - " and before the last ","
#   street     = words before the (optional BR +) house-number token
#   number     = last token that starts with a digit (BR prefix is stripped)
_NUMBER_RE = re.compile(r"^(.+?)\s+(?:(?:br|бр|број|broj)\.?\s*)?(\d+\S*)$", re.IGNORECASE)


def parse_station_name(name: str) -> tuple[str | None, str | None, str | None]:
    """
    Return (settlement, street, house_number).
    Any component can be None if not parseable.

    Handles two formats:
      comma format:  "N. Venue - Settlement, Street Number"
      dash format:   "N. Venue - Settlement - Street Number"  (no comma)
    """
    if "," in name:
        before_sep, address_part = name.rsplit(",", 1)
        # Settlement: part after the last " - " before the comma
        settlement = before_sep.rsplit(" - ", 1)[1].strip() if " - " in before_sep else None
    elif " - " in name:
        # No comma: address is after the last " - ", settlement is the segment before that
        before_sep, address_part = name.rsplit(" - ", 1)
        settlement = before_sep.rsplit(" - ", 1)[1].strip() if " - " in before_sep else None
    else:
        return None, None, None

    address_part = address_part.strip()
    settlement = settlement or None

    if not address_part:
        return settlement, None, None

    m = _NUMBER_RE.match(address_part)
    if m:
        return settlement, strip_street_prefix(m.group(1).strip()), m.group(2).strip()

    # No recognisable house number
    return settlement, strip_street_prefix(address_part), None


# ---------------------------------------------------------------------------
# CSV index loader
# Returns:
#   index        – four-level Cyrillic street index
#                  index[opstina_norm][settlement_norm][street_norm][number_norm] = (E, N)
#   centroids    – settlement centroid index (UTM mean coords)
#                  centroids[opstina_norm][settlement_norm] = (mean_E, mean_N)
#   latin_index  – four-level Latin street index (same structure, no homoglyph conversion)
#                  latin_index[opstina_norm][settlement_norm][street_lat_norm][number_norm] = (E, N)
# ---------------------------------------------------------------------------
def load_index(csv_path: Path) -> tuple[dict, dict, dict]:
    print(f"Loading {csv_path} …", flush=True)
    index: dict = {}
    latin_index: dict = {}
    # centroid_acc: opstina → settlement → [sum_E, sum_N, count]
    centroid_acc: dict = {}
    n = 0

    with open(csv_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n += 1
            if n % 500_000 == 0:
                print(f"  {n:,} rows …", flush=True)

            wkt = row.get("wkt", "").strip()
            if not wkt:
                continue
            coords = _parse_wkt_point(wkt)
            if not coords:
                continue

            opstina    = norm(row.get("opstina_ime", ""))
            settlement = norm(row.get("naselje_ime", ""))
            street     = norm(row.get("ulica_ime", ""))
            number     = norm_number(row.get("kucni_broj", ""))

            if not opstina or not street:
                continue

            # Cyrillic street index
            (index
             .setdefault(opstina, {})
             .setdefault(settlement, {})
             .setdefault(street, {}))[number] = coords

            # Latin street index
            street_lat = norm_latin(row.get("ulica_ime_lat", ""))
            if street_lat:
                (latin_index
                 .setdefault(opstina, {})
                 .setdefault(settlement, {})
                 .setdefault(street_lat, {}))[number] = coords

            # Centroid accumulation
            acc = centroid_acc.setdefault(opstina, {}).setdefault(settlement, [0.0, 0.0, 0])
            acc[0] += coords[0]
            acc[1] += coords[1]
            acc[2] += 1

    # Build centroid index from accumulators
    centroids: dict = {}
    for opstina, settlements in centroid_acc.items():
        for sett, (sum_e, sum_n, count) in settlements.items():
            centroids.setdefault(opstina, {})[sett] = (sum_e / count, sum_n / count)

    print(f"Indexed {n:,} rows across {len(index)} municipalities.", flush=True)
    return index, centroids, latin_index


# ---------------------------------------------------------------------------
# Coordinate lookup
# ---------------------------------------------------------------------------
def _municipality_keys(community_name: str, index: dict) -> list[str]:
    """
    Priority-ordered list of municipality keys to try.

    Handles several naming patterns:
      'БЕОГРАД-БАРАЈЕВО'   → also try 'барајево'         (suffix is the municipality)
      'СОМБОР - ГРАД'      → also try 'сомбор'           (prefix is the municipality)
      'БЕОГРАД-ПАЛИЛУЛА'   → also try 'палилула (београд)'  (CSV uses parenthetical)
      'НИШ-ПАЛИЛУЛА'       → also try 'палилула (ниш)'
    """
    primary = norm(community_name)
    candidates: list[str] = []

    if primary in index:
        candidates.append(primary)

    split_parts: list[str] = []
    if "-" in primary:
        split_parts = [p.strip() for p in primary.split("-", 1) if p.strip()]
        for part in split_parts:
            if part in index and part not in candidates:
                candidates.append(part)

        # Starts-with fallback for parenthetically disambiguated CSV entries.
        # e.g. "палилула" → matches "палилула (београд)" and "палилула (ниш)".
        # Among multiple matches prefer the one that contains the other split part.
        for part in split_parts:
            prefix = part + " "
            matches = [o for o in index if o.startswith(prefix) and o not in candidates]
            if matches:
                other = [p for p in split_parts if p != part]
                matches.sort(key=lambda o: not any(p in o for p in other))
                candidates.extend(matches)

    for c in get_close_matches(primary, index.keys(), n=3, cutoff=0.75):
        if c not in candidates:
            candidates.append(c)

    return candidates


def _match_street(street_norm: str, streets: dict) -> str | None:
    if street_norm in streets:
        return street_norm
    hits = get_close_matches(street_norm, streets.keys(), n=1, cutoff=0.70)
    return hits[0] if hits else None


def _match_number(number_norm: str, numbers: dict) -> tuple[float, float] | None:
    if number_norm in numbers:
        return numbers[number_norm]
    # Digits-only fallback: '32а' → '32'
    digits = re.sub(r"[^0-9]", "", number_norm)
    if digits and digits in numbers:
        return numbers[digits]
    hits = get_close_matches(number_norm, numbers.keys(), n=1, cutoff=0.75)
    return numbers[hits[0]] if hits else None


def lookup(
    index: dict,
    centroids: dict,
    latin_index: dict,
    community_name: str,
    settlement_name: str | None,
    street: str,
    number: str | None,
) -> tuple[tuple[float, float], bool] | None:
    """
    Returns ((E, N), is_approx) or None.
    is_approx=True means the coordinate is a settlement centroid, not a building address.
    """
    street_norm = norm(street)
    street_norm_latin = norm_latin(street)
    number_norm = norm_number(number) if number else None
    settlement_norm = norm(settlement_name) if settlement_name else None

    muni_keys = _municipality_keys(community_name, index)

    for muni in muni_keys:
        settlements_by_muni = index[muni]
        latin_by_muni = latin_index.get(muni, {})

        # Build ordered list of settlements to search:
        # exact/fuzzy match on the parsed settlement first, then everything else.
        ordered: list[str] = []
        if settlement_norm:
            if settlement_norm in settlements_by_muni:
                ordered.append(settlement_norm)
            for c in get_close_matches(
                settlement_norm, settlements_by_muni.keys(), n=3, cutoff=0.75
            ):
                if c not in ordered:
                    ordered.append(c)
        # Append remaining settlements as fallback
        for s in settlements_by_muni:
            if s not in ordered:
                ordered.append(s)

        for sett in ordered:
            streets = settlements_by_muni[sett]
            matched_street = _match_street(street_norm, streets)

            if matched_street is None:
                # Try Latin street index for this settlement
                latin_streets = latin_by_muni.get(sett, {})
                matched_street_lat = _match_street(street_norm_latin, latin_streets)
                if matched_street_lat is not None:
                    numbers = latin_streets[matched_street_lat]
                    if number_norm is None:
                        result = next(iter(numbers.values()), None)
                    else:
                        result = _match_number(number_norm, numbers)
                    if result is not None:
                        return result, False
                continue

            numbers = streets[matched_street]
            if number_norm is None:
                result = next(iter(numbers.values()), None)
            else:
                result = _match_number(number_norm, numbers)

            if result is not None:
                return result, False

    # Settlement centroid fallback:
    # If the "street" token is actually a village/settlement name, return its centroid.
    for muni in muni_keys:
        centroids_by_muni = centroids.get(muni, {})
        if not centroids_by_muni:
            continue
        if street_norm in centroids_by_muni:
            return centroids_by_muni[street_norm], True
        hits = get_close_matches(street_norm, centroids_by_muni.keys(), n=1, cutoff=0.80)
        if hits:
            return centroids_by_muni[hits[0]], True

    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if not JSON_FILE.exists():
        sys.exit(f"Not found: {JSON_FILE}")
    if not CSV_FILE.exists():
        sys.exit(f"Not found: {CSV_FILE}")

    index, centroids, latin_index = load_index(CSV_FILE)

    with open(JSON_FILE, encoding="utf-8") as f:
        data = json.load(f)

    total = matched = unmatched = approx = 0

    for community in data["communities"]:
        community_name = community["name"]

        for station in community["polling_stations"]:
            total += 1
            name = station.get("name", "")

            settlement, street, number = parse_station_name(name)

            if not street:
                station["geo"] = None
                unmatched += 1
                continue

            result = lookup(index, centroids, latin_index, community_name, settlement, street, number)
            if result:
                coords, is_approx = result
                lat, lon = utm34n_to_latlon(*coords)
                station["geo"] = {"lat": lat, "lon": lon}
                if is_approx:
                    station["geo_approx"] = True
                    approx += 1
                matched += 1
            else:
                station["geo"] = None
                unmatched += 1
                print(
                    f"  [NO MATCH] {community_name} / {settlement} | {street} {number or ''}",
                    flush=True,
                )

    print(
        f"\nDone: {matched}/{total} matched ({approx} approx centroids), {unmatched} unmatched.",
        flush=True,
    )

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Written to {JSON_FILE}")


if __name__ == "__main__":
    main()
