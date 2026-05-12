# Polling Stations API

A small read-only JSON API over the Serbian polling-station dataset that powers
this site. Three endpoints, all authorized via an API key issued to the caller.

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
the station name, the locality name (an opština, city, or village — whichever
spatial unit the station sits in), the region name, and the county (okrug)
name. A hit in any of these four fields causes the station to be included.

The match is **script- and diacritic-insensitive**: queries can be written in
Latin or Cyrillic, with or without diacritics, in any case, and they are
normalized to the same canonical form as the indexed names before matching.

### Query parameters

| Name | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `q` | string | yes | — | Must be at least 2 and at most 64 characters after trimming; the post-normalization length must still be at least 2. URL-encode non-ASCII characters. |
| `limit` | integer | no | `50` | Capped at `200`; larger values are silently clamped. |
| `region` | string | no | — | Canonical region id (e.g. `beograd`, `vojvodina`). Pre-filters the result set. See the [region & county catalogue](#region--county-catalogue). |
| `county` | string | no | — | Canonical county (okrug) id (e.g. `nisavski`, `grad-beograd`). Pre-filters the result set. See the [region & county catalogue](#region--county-catalogue). |

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
      "locality": {
        "id": "1",
        "name": "АДА",
        "region": { "id": "vojvodina", "name_cyr": "Регион Војводине", "name_lat": "Region Vojvodine" },
        "county": { "id": "severnobanatski", "name_cyr": "Севернобанатски округ", "name_lat": "Severnobanatski okrug" }
      },
      "geo": { "lat": 45.7853097, "lon": 20.1354901 }
    },
    {
      "id": "7022",
      "name": "2. Зграда I МЗ (велика сала) - Ада, Маршала Тита 43",
      "locality": {
        "id": "1",
        "name": "АДА",
        "region": { "id": "vojvodina", "name_cyr": "Регион Војводине", "name_lat": "Region Vojvodine" },
        "county": { "id": "severnobanatski", "name_cyr": "Севернобанатски округ", "name_lat": "Severnobanatski okrug" }
      },
      "geo": { "lat": 45.7928104, "lon": 20.138238 }
    },
    {
      "id": "6598",
      "name": "21. ОСНОВНА ШКОЛА - НОВАЦИ ПОЉАНСКА БР.9",
      "locality": {
        "id": "2",
        "name": "АЛЕКСАНДРОВАЦ",
        "region": { "id": "sumadija-zapadna", "name_cyr": "Регион Шумадије и Западне Србије", "name_lat": "Region Šumadije i Zapadne Srbije" },
        "county": { "id": "rasinski", "name_cyr": "Расински округ", "name_lat": "Rasinski okrug" }
      },
      "geo": { "lat": null, "lon": null }
    }
  ]
}
```

`count` is the total number of matches under the current filters. `results`
is truncated to `limit`; if `count > results.length`, narrow your query or
raise `limit` to see more. There is no pagination. Results may include
unmapped stations, which are returned with `geo.lat` and `geo.lon` set to
`null`.

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

# Filter by region: "skola" stations in Vojvodina only
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/search?q=skola&region=vojvodina'

# Filter by county: every station in Nišavski okrug
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/search?q=os&county=nisavski&limit=200'
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
| `400` | `BAD_REQUEST` | `q` missing, under 2 chars after normalization, or over 64 chars after trim; or unknown `region` / `county` id. |
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
| `region` | string | no | — | Canonical region id. Pre-filters to one region before sorting by distance. See the [region & county catalogue](#region--county-catalogue). |
| `county` | string | no | — | Canonical county (okrug) id. Pre-filters to one county before sorting by distance. See the [region & county catalogue](#region--county-catalogue). |

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
      "locality": {
        "id": "1",
        "name": "АДА",
        "region": { "id": "vojvodina", "name_cyr": "Регион Војводине", "name_lat": "Region Vojvodine" },
        "county": { "id": "severnobanatski", "name_cyr": "Севернобанатски округ", "name_lat": "Severnobanatski okrug" }
      },
      "geo": { "lat": 45.7853097, "lon": 20.1354901 },
      "distance_m": 0
    },
    {
      "id": "7022",
      "name": "2. Зграда I МЗ (велика сала) - Ада, Маршала Тита 43",
      "locality": {
        "id": "1",
        "name": "АДА",
        "region": { "id": "vojvodina", "name_cyr": "Регион Војводине", "name_lat": "Region Vojvodine" },
        "county": { "id": "severnobanatski", "name_cyr": "Севернобанатски округ", "name_lat": "Severnobanatski okrug" }
      },
      "geo": { "lat": 45.7928104, "lon": 20.138238 },
      "distance_m": 877
    }
  ]
}
```

`count` is the total number of matches under the current filters (region,
county, `max_distance_m`). `results` is truncated to `limit`; if
`count > results.length`, raise `limit` or tighten filters to see more.
There is no pagination.

### Examples

```bash
# Five nearest stations to a coordinate in Ada
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/nearby?lat=45.7853097&lon=20.1354901&limit=5'

# Stations within 500 m of Belgrade city centre, up to 50 of them
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/nearby?lat=44.8176&lon=20.4569&limit=50&max_distance_m=500'

# Nearest stations to a point, restricted to one okrug
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/stations/nearby?lat=43.32&lon=21.90&county=nisavski&limit=10'
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
| `400` | `BAD_REQUEST` | `lat` or `lon` missing / not a number / out of range / outside the Serbia bounding box; `max_distance_m` not a positive integer; or unknown `region` / `county` id. |
| `401` | `UNAUTHORIZED` | Missing or unknown `X-Api-Key`. |
| `405` | `METHOD_NOT_ALLOWED` | Anything other than `GET`. |
| `500` | `INTERNAL_ERROR` | Data file failed to load. |

---

## `GET /api/regions`

Returns the regions + counties catalogue used by the `region` and `county`
filter parameters on `/api/stations/search` and `/api/stations/nearby`. Counties
are nested under their parent region, and each entry includes a `station_count`
(mapped + unmapped combined). Both arrays are sorted by `name_lat` ascending.

The same data is documented as a static table further down (see
[Region & county catalogue](#region--county-catalogue)) — this endpoint is the
runtime-queryable form, useful for populating a cascading picker without
shipping the catalogue with your client.

### Query parameters

None.

### Response

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "regions": [
    {
      "id": "beograd",
      "name_cyr": "Београдски регион",
      "name_lat": "Beogradski region",
      "station_count": 1234,
      "counties": [
        {
          "id": "grad-beograd",
          "name_cyr": "Град Београд",
          "name_lat": "Grad Beograd",
          "station_count": 1234
        }
      ]
    },
    {
      "id": "vojvodina",
      "name_cyr": "Регион Војводине",
      "name_lat": "Region Vojvodine",
      "station_count": 2000,
      "counties": [
        {
          "id": "severnobacki",
          "name_cyr": "Севернобачки округ",
          "name_lat": "Severnobački okrug",
          "station_count": 250
        },
        {
          "id": "srednjebanatski",
          "name_cyr": "Средњебанатски округ",
          "name_lat": "Srednjebanatski okrug",
          "station_count": 220
        }
      ]
    }
  ]
}
```

`station_count` values above are illustrative — real values come from the
current build's dataset. A region or county with zero stations is still
listed (it remains a valid filter id); the `ostalo` region exposes an empty
`counties` array because its localities (diaspora, prisons, MoD) have no
county.

### Examples

```bash
curl -H 'X-Api-Key: k_live_...' \
  'https://example.netlify.app/api/regions'
```

### Errors

| Status | `code` | Cause |
| --- | --- | --- |
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
| `locality.name` | string | Cyrillic locality name (e.g. `АДА`, `БЕОГРАД-ВРАЧАР`). |
| `locality.region` | object \| null | Region (NSTJ-1) the locality belongs to. Object with `id`, `name_cyr`, `name_lat`. See the [catalogue](#region--county-catalogue). `null` is never returned in practice — every locality has a region, including the catch-all `ostalo` bucket for diaspora / prisons / Kosovo. |
| `locality.county` | object \| null | County (okrug) the locality belongs to. Same shape as `region`. **`null`** for localities in the `ostalo` region (diaspora, prisons, Kosovo). |
| `geo.lat` | number \| null | WGS84 latitude. `null` for unmapped stations in `/search` results; never `null` on `/nearby`. |
| `geo.lon` | number \| null | WGS84 longitude. Same nullability rules as `geo.lat`. |
| `distance_m` | integer | Distance from the query point in meters, rounded. **Only on `/nearby`.** |

The full election results for each station are *not* exposed by this API.

---

## Region & county catalogue

Every locality is tagged with one **region** (NSTJ-1 statistical region) and
typically one **county** (okrug). Both ids are stable kebab-case Latin slugs
suitable for URLs and faceted-search params. The authoritative source is
[`data/serbia_admin.json`](data/serbia_admin.json).

### Regions

| `id` | Cyrillic | Latin |
| --- | --- | --- |
| `beograd` | Београдски регион | Beogradski region |
| `vojvodina` | Регион Војводине | Region Vojvodine |
| `sumadija-zapadna` | Регион Шумадије и Западне Србије | Region Šumadije i Zapadne Srbije |
| `juzna-istocna` | Регион Јужне и Источне Србије | Region Južne i Istočne Srbije |
| `kosovo-metohija` | Регион Косова и Метохије | Region Kosova i Metohije |
| `ostalo` | Остало | Ostalo |

The `ostalo` bucket carries localities outside the five geographic regions:
diaspora (`ИНОСТРАНСТВО`), prisons (`УПРАВА ЗА ИЗВРШЕЊЕ ЗАВОДСКИХ САНКЦИЈА`),
and the Ministry of Defence voters' roll (`МИНИСТАРСТВО ОДБРАНЕ`). Their
`locality.county` is `null`.

### Counties (okruzi)

| `id` | Cyrillic | Latin | Region |
| --- | --- | --- | --- |
| `grad-beograd` | Град Београд | Grad Beograd | `beograd` |
| `severnobacki` | Севернобачки округ | Severnobački okrug | `vojvodina` |
| `srednjebanatski` | Средњебанатски округ | Srednjebanatski okrug | `vojvodina` |
| `severnobanatski` | Севернобанатски округ | Severnobanatski okrug | `vojvodina` |
| `juznobanatski` | Јужнобанатски округ | Južnobanatski okrug | `vojvodina` |
| `zapadnobacki` | Западнобачки округ | Zapadnobački okrug | `vojvodina` |
| `juznobacki` | Јужнобачки округ | Južnobački okrug | `vojvodina` |
| `sremski` | Сремски округ | Sremski okrug | `vojvodina` |
| `macvanski` | Мачвански округ | Mačvanski okrug | `sumadija-zapadna` |
| `kolubarski` | Колубарски округ | Kolubarski okrug | `sumadija-zapadna` |
| `sumadijski` | Шумадијски округ | Šumadijski okrug | `sumadija-zapadna` |
| `pomoravski` | Поморавски округ | Pomoravski okrug | `sumadija-zapadna` |
| `zlatiborski` | Златиборски округ | Zlatiborski okrug | `sumadija-zapadna` |
| `moravicki` | Моравички округ | Moravički okrug | `sumadija-zapadna` |
| `raski` | Рашки округ | Raški okrug | `sumadija-zapadna` |
| `rasinski` | Расински округ | Rasinski okrug | `sumadija-zapadna` |
| `podunavski` | Подунавски округ | Podunavski okrug | `juzna-istocna` |
| `branicevski` | Браничевски округ | Braničevski okrug | `juzna-istocna` |
| `borski` | Борски округ | Borski okrug | `juzna-istocna` |
| `zajecarski` | Зајечарски округ | Zaječarski okrug | `juzna-istocna` |
| `nisavski` | Нишавски округ | Nišavski okrug | `juzna-istocna` |
| `toplicki` | Топлички округ | Toplički okrug | `juzna-istocna` |
| `pirotski` | Пиротски округ | Pirotski okrug | `juzna-istocna` |
| `jablanicki` | Јабланички округ | Jablanički okrug | `juzna-istocna` |
| `pcinjski` | Пчињски округ | Pčinjski okrug | `juzna-istocna` |
| `kosovski` | Косовски округ | Kosovski okrug | `kosovo-metohija` |
| `pecki` | Пећки округ | Pećki okrug | `kosovo-metohija` |
| `prizrenski` | Призренски округ | Prizrenski okrug | `kosovo-metohija` |
| `kosovsko-mitrovacki` | Косовско-митровачки округ | Kosovsko-mitrovački okrug | `kosovo-metohija` |
| `kosovsko-pomoravski` | Косовско-поморавски округ | Kosovsko-pomoravski okrug | `kosovo-metohija` |

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
