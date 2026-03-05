#!/usr/bin/env python3
"""
Generate polling_stations_data.js from a polling stations JSON file.

Usage:
    python3 generate_data.py polling_stations_86.json html/polling_stations_data.js
"""

import copy
import json
import math
import sys
import os
from collections import defaultdict

def spread_overlapping(data, threshold=1e-4, radius=3e-5):
    """
    Spread markers that fall within the same ~11 m grid cell so they
    are individually visible on the map.  Works on a deep copy; the
    original data is not mutated.

    threshold : grid cell size in degrees (~11 m in latitude)
    radius    : spread circle radius in degrees (~3 m) — kept minimal
    """
    data = copy.deepcopy(data)

    # Collect geo dicts grouped by rounded position
    groups = defaultdict(list)
    for community in data['communities']:
        for station in community['polling_stations']:
            geo = station.get('geo')
            if geo and geo.get('lat') is not None:
                key = (round(geo['lat'] / threshold), round(geo['lon'] / threshold))
                groups[key].append(geo)

    for geos in groups.values():
        n = len(geos)
        if n < 2:
            continue
        center_lat = sum(g['lat'] for g in geos) / n
        center_lon = sum(g['lon'] for g in geos) / n
        cos_lat    = math.cos(math.radians(center_lat))
        for i, geo in enumerate(geos):
            angle      = 2 * math.pi * i / n
            geo['lat'] = round(center_lat + radius * math.cos(angle), 7)
            geo['lon'] = round(center_lon + radius * math.sin(angle) / cos_lat, 7)

    return data


if len(sys.argv) < 2:
    print("Usage: python3 generate_data.py <input.json> <output.js>")
    sys.exit(1)
src = sys.argv[1]
dst = sys.argv[2] if len(sys.argv) >= 3 else sys.argv[1]

if not os.path.exists(src):
    print(f"Error: {src} not found")
    sys.exit(1)

with open(src, 'r', encoding='utf-8') as f:
    data = json.load(f)

data = spread_overlapping(data)

# ── Optimise: replace party-name strings with integer indices ─────────────────
parties = []
party_index = {}
for community in data['communities']:
    for station in community['polling_stations']:
        for result in station.get('voting', {}).get('results', []):
            name = result['party']
            if name not in party_index:
                party_index[name] = len(parties)
                parties.append(name)

for community in data['communities']:
    for station in community['polling_stations']:
        for result in station.get('voting', {}).get('results', []):
            result['party'] = party_index[result['party']]

data['parties'] = parties
# ─────────────────────────────────────────────────────────────────────────────

compact = json.dumps(data, ensure_ascii=False, separators=(',', ':'))

total    = sum(len(c['polling_stations']) for c in data['communities'])
mapped   = sum(1 for c in data['communities'] for s in c['polling_stations'] if s.get('geo'))
unmapped = total - mapped

with open(dst, 'w', encoding='utf-8') as f:
    f.write(f'// Generated from {src}\n')
    f.write(f'// Total: {total}, Mapped: {mapped}, Unmapped: {unmapped}\n')
    f.write(f'const DATA = {compact};\n')

size = os.path.getsize(dst)
print(f"Written {dst} ({size:,} bytes, {size/1024/1024:.2f} MB)")
print(f"  Total: {total} | Mapped: {mapped} | Unmapped: {unmapped}")
