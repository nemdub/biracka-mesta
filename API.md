# Polling Stations API

A small read-only JSON API over the Serbian polling-station dataset that powers
this site. Two endpoints, both authorized via an API key issued to the caller.

- Base URL: `https://<your-netlify-deploy>` (e.g. the production domain or any
  branch / deploy preview URL).
- All endpoints return `Content-Type: application/json; charset=utf-8`.
- `/search` returns every matching station; unmapped stations (those without
  coordinates) come back with `geo.lat = null` and `geo.lon = null`. `/nearby`
  excludes unmapped stations because no distance can be computed.

---

## Authentication

Every request must include an `X-Api-Key` header.

```
X-Api-Key: k_live_abcd1234...
```

Keys are issued and revoked by editing the `API_KEYS` environment variable in
the Netlify dashboard. The format is a comma-separated list of
`clientId:key` pairs:

```
API_KEYS=acme:k_live_abcd1234,ngo-x:k_live_efgh5678
```

The `clientId` is logged with every successful request (visible in Netlify
function logs) so usage can be attributed per caller. Comparisons are
constant-time.

A missing, malformed, or unknown key returns:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{ "error": "Unauthorized", "code": "UNAUTHORIZED" }
```

There is no rate limiting in this version.

---

## `GET /api/stations/search`

Searches polling stations by name. The query is matched as a substring against
both the station name and the locality name (an opština, city, or village —
whichever spatial unit the station sits in); matches in either field cause the
station to be included.

The match is **script- and diacritic-insensitive**: queries can be written in
Latin or Cyrillic, with or without diacritics, in any case, and they are
normalized to the same canonical form as the indexed names before matching.

### Query parameters

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `q` | string | yes | — | Must be at least 2 and at most 64 characters after trimming; the post-normalization length must still be at least 2. URL-encode non-ASCII characters. |
| `limit` | integer | no | `50` | Capped at `200`; larger values are silently clamped. |

### Response

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "count": 3,
  "results": [
    {
      "id": "4246",
      "name": "1. \"Прецизни Лив\" - Ада, Молски пут 4",
      "locality": { "id": "1", "name": "АДА" },
      "geo": { "lat": 45.7853097, "lon": 20.1354901 }
    },
    {
      "id": "7022",
      "name": "2. Зграда I МЗ (велика сала) - Ада, Маршала Тита 43",
      "locality": { "id": "1", "name": "АДА" },
      "geo": { "lat": 45.7928104, "lon": 20.138238 }
    },
    {
      "id": "6598",
      "name": "21. ОСНОВНА ШКОЛА - НОВАЦИ ПОЉАНСКА БР.9",
      "locality": { "id": "2", "name": "АЛЕКСАНДРОВАЦ" },
      "geo": { "lat": null, "lon": null }
    }
  ]
}
```

`count` always equals `results.length`. There is no pagination — narrow your
query if you hit the `limit`. Results may include unmapped stations, which
are returned with `geo.lat` and `geo.lon` set to `null`.

### Examples

```bash
# Latin query
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/search?q=ada&limit=10'

# Cyrillic query (URL-encoded). Returns the same id set as q=ada.
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/search?q=%D0%B0%D0%B4%D0%B0'

# Match a locality (search hits locality name too)
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/search?q=beograd&limit=200'
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
| `400` | `BAD_REQUEST` | `q` missing, under 2 chars after normalization, or over 64 chars after trim. |
| `401` | `UNAUTHORIZED` | Missing or unknown `X-Api-Key`. |
| `405` | `METHOD_NOT_ALLOWED` | Anything other than `GET`. |
| `500` | `INTERNAL_ERROR` | Data file failed to load. |

---

## `GET /api/stations/nearby`

Returns the closest mapped polling stations to a coordinate, sorted by
ascending distance. Distance is computed with the haversine formula on a
spherical Earth (radius 6 371 000 m).

### Query parameters

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `lat` | float | yes | — | WGS84 latitude, range `[-90, 90]`. Must also fall inside Serbia's bounding box (roughly `[42, 47]`); out-of-area calls return `400`. |
| `lon` | float | yes | — | WGS84 longitude, range `[-180, 180]`. Must also fall inside Serbia's bounding box (roughly `[18, 23.5]`). |
| `limit` | integer | no | `10` | Capped at `100`; larger values are silently clamped. |
| `max_distance_m` | integer | no | — | If set, exclude stations farther than this many meters. Must be a positive integer. |

### Response

Same station shape as `/search`, plus a `distance_m` field (integer meters,
rounded). Results are sorted ascending by distance.

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "count": 3,
  "results": [
    {
      "id": "4246",
      "name": "1. \"Прецизни Лив\" - Ада, Молски пут 4",
      "locality": { "id": "1", "name": "АДА" },
      "geo": { "lat": 45.7853097, "lon": 20.1354901 },
      "distance_m": 0
    },
    {
      "id": "7022",
      "name": "2. Зграда I МЗ (велика сала) - Ада, Маршала Тита 43",
      "locality": { "id": "1", "name": "АДА" },
      "geo": { "lat": 45.7928104, "lon": 20.138238 },
      "distance_m": 877
    },
    {
      "id": "...",
      "name": "...",
      "locality": { "id": "1", "name": "АДА" },
      "geo": { "lat": 45.79, "lon": 20.14 },
      "distance_m": 1342
    }
  ]
}
```

### Examples

```bash
# Five nearest stations to a coordinate in Ada
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/nearby?lat=45.7853097&lon=20.1354901&limit=5'

# Stations within 500 m of Belgrade city centre, up to 50 of them
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/nearby?lat=44.8176&lon=20.4569&limit=50&max_distance_m=500'
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
| `400` | `BAD_REQUEST` | `lat` or `lon` missing / not a number / out of range / outside the Serbia bounding box; or `max_distance_m` not a positive integer. |
| `401` | `UNAUTHORIZED` | Missing or unknown `X-Api-Key`. |
| `405` | `METHOD_NOT_ALLOWED` | Anything other than `GET`. |
| `500` | `INTERNAL_ERROR` | Data file failed to load. |

---

## Response fields

| Field | Type | Description |
| --- | --- | --- |
| `id` | string | Stable polling-station id from the source dataset. |
| `name` | string | Cyrillic station name as published, typically including the street address. |
| `locality.id` | string | Numeric locality id, as a string. The locality is the spatial unit that contains the polling station — typically an opština (municipality), but may also be a city or a village. |
| `locality.name` | string | Cyrillic locality name (e.g. `АДА`, `БЕОГРАД`). |
| `geo.lat` | number \| null | WGS84 latitude. `null` for unmapped stations in `/search` results; never `null` on `/nearby`. |
| `geo.lon` | number \| null | WGS84 longitude. Same nullability rules as `geo.lat`. |
| `distance_m` | integer | Distance from the query point in meters, rounded. **Only on `/nearby`.** |

The full election results for each station are *not* exposed by this API.

---

## Error response shape

All non-`200` responses share this shape:

```json
{ "error": "human-readable message", "code": "MACHINE_CODE" }
```

Possible `code` values: `BAD_REQUEST`, `UNAUTHORIZED`, `METHOD_NOT_ALLOWED`,
`INTERNAL_ERROR`.

---

## Caching

Successful (`200`) responses are cached at Netlify's CDN edge for **5 minutes**
(`s-maxage=300`), with `stale-while-revalidate=600` so a brief origin hiccup
doesn't disrupt callers. Browsers cache for 60 seconds. Error responses are
never cached.

The cache key is the **full request URL** — `X-Api-Key` is intentionally not
part of it. Consequence: once a response for a given query has been served to
any valid key, the CDN serves the same bytes to every other valid key
requesting the same URL. The auth check still runs on cache misses, so
unauthorized callers cannot trigger or read cached responses. One thing to
know: per-client logging is only emitted on cache misses.

Query parameters that differ only in casing or order produce separate cache
entries (`?q=Niš` and `?q=niš` are two URLs, even though they match the same
stations). For best hit rates, pick a canonical form on the client side.

---

## CORS

These endpoints do **not** send `Access-Control-Allow-Origin` headers, so they
cannot be called from a browser on a different origin. They are intended for
server-side callers. Open a request if you need cross-origin browser access.

---

## Notes for implementers

- The dataset has ~8,300 polling stations across 194 localities. About 7,700
  are mapped (have `geo`). All of them are returned by `/search`; only mapped
  ones appear on `/nearby`.
- Name normalization performs sr-Cyrl → sr-Latn transliteration (including the
  digraphs `љ`/`њ`/`џ`), then strips combining marks, then lowercases. The same
  pipeline is applied to indexed names at build time and to incoming queries.
- Functions bundle a pre-built JavaScript module containing the flat,
  pre-normalized dataset. V8 lazy-parses the module at container init (off
  the request critical path), so cold-start cost is small.
